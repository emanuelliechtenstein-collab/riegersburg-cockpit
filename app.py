from __future__ import annotations

import io
import json
import hmac
import hashlib
import os
import re
import shutil
import sqlite3
import zipfile
from datetime import date, datetime
from email import policy
from email.parser import BytesParser, Parser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text

try:
    import streamlit as st
except ImportError:
    st = None

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

FUNDING_FILE = DATA_DIR / "foerderstellen.csv"
TASKS_FILE = DATA_DIR / "aufgaben.csv"
CONTACTS_FILE = DATA_DIR / "kontakte.csv"
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "riegersburg.db"
BACKUP_DIR = DATA_DIR / "backups"
DEFAULT_DOCUMENT_DIR = Path.home() / "Desktop" / "Riegersburg_Cockpit"
DEFAULT_PASSWORD = "Riegersburg2030"
DEFAULT_LOGO_FILE = BASE_DIR / "assets" / "logo.png"

FUNDING_COLUMNS = [
    "Name",
    "Ebene",
    "zuständige Stelle",
    "Ansprechpartner",
    "thematische Eignung",
    "geschätztes Förderpotenzial",
    "Status",
    "nächste Aktion",
    "Frist",
    "Notizen",
]

TASK_COLUMNS = [
    "Aufgabe",
    "Verantwortlich",
    "Priorität",
    "Status",
    "Frist",
    "Bezug zu Förderstelle",
    "Notizen",
]

CONTACT_COLUMNS = [
    "Name",
    "Organisation",
    "Funktion",
    "E-Mail",
    "Telefon",
    "Relevanz",
    "letzte Kontaktaufnahme",
    "nächste Aktion",
    "Notizen",
]

IMPORT_COLUMNS = ["fingerprint", "source_type", "title", "imported_at", "notes"]
SYNC_COLUMNS = ["fingerprint", "filename", "source_path", "synced_at", "file_type", "content", "status", "notes"]

LEVELS = ["Land", "Bund", "EU", "Stiftung", "Privat"]
FUNDING_STATUS = ["Idee", "in Prüfung", "Kontakt aufnehmen", "Kontakt aufgenommen", "Antrag in Arbeit", "eingereicht", "bewilligt", "abgelehnt", "pausiert"]
TASK_STATUS = ["offen", "in Arbeit", "wartet", "erledigt"]
PRIORITIES = ["hoch", "mittel", "niedrig"]
RELEVANCE = ["hoch", "mittel", "niedrig"]

TABLES = {
    FUNDING_FILE.name: ("foerderstellen", FUNDING_COLUMNS),
    TASKS_FILE.name: ("aufgaben", TASK_COLUMNS),
    CONTACTS_FILE.name: ("kontakte", CONTACT_COLUMNS),
}


def table_spec(path: Path, columns: list[str]) -> tuple[str, list[str]]:
    return TABLES.get(path.name, (path.stem, columns))


def ensure_csv(path: Path, columns: list[str]) -> None:
    if not path.exists():
        template_path = DATA_DIR / "templates" / path.name
        if template_path.exists():
            shutil.copy2(template_path, path)
        else:
            pd.DataFrame(columns=columns).to_csv(path, index=False)


def secret_value(name: str, default: str = "") -> str:
    env_value = os.environ.get(name.upper()) or os.environ.get(name)
    if env_value:
        return env_value
    if st is not None:
        try:
            value = st.secrets.get(name, "")
            if value:
                return str(value)
        except Exception:
            pass
    return default


def database_url() -> str:
    return secret_value("database_url") or f"sqlite:///{DB_FILE}"


def is_cloud_database() -> bool:
    return not database_url().startswith("sqlite:///")


def database_engine():
    url = database_url()
    if st is not None:
        @st.cache_resource(show_spinner=False)
        def cached_engine(cached_url: str):
            return create_engine(cached_url, pool_pre_ping=True)

        return cached_engine(url)
    return create_engine(url, pool_pre_ping=True)


if st is not None:
    @st.cache_data(ttl=30, show_spinner=False)
    def cached_read_table(cached_url: str, cached_table_name: str, cached_columns: tuple[str, ...]) -> pd.DataFrame:
        engine = create_engine(cached_url, pool_pre_ping=True)
        with engine.connect() as connection:
            table = pd.read_sql_query(text(f'SELECT * FROM "{cached_table_name}"'), connection, dtype=str).fillna("")
        for column in cached_columns:
            if column not in table.columns:
                table[column] = ""
        return table[list(cached_columns)]
else:
    cached_read_table = None


def read_table_from_database(url: str, table_name: str, columns: tuple[str, ...]) -> pd.DataFrame:
    if cached_read_table is not None:
        return cached_read_table(url, table_name, columns)

    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        table = pd.read_sql_query(text(f'SELECT * FROM "{table_name}"'), connection, dtype=str).fillna("")
    for column in columns:
        if column not in table.columns:
            table[column] = ""
    return table[list(columns)]


def load_table(path: Path, columns: list[str]) -> pd.DataFrame:
    ensure_database()
    table_name, table_columns = table_spec(path, columns)
    return read_table_from_database(database_url(), table_name, tuple(table_columns))


def load_settings() -> dict[str, str]:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_settings(settings: dict[str, str]) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_table(table: pd.DataFrame, columns: list[str], required_column: str | None = None) -> pd.DataFrame:
    cleaned = table.copy()
    for column in columns:
        if column not in cleaned.columns:
            cleaned[column] = ""
    cleaned = cleaned[columns].fillna("")
    cleaned = cleaned.map(lambda value: value.strip() if isinstance(value, str) else value)
    non_empty_mask = cleaned.astype(str).apply(lambda row: any(value.strip() for value in row), axis=1)
    cleaned = cleaned[non_empty_mask]
    if required_column and required_column in cleaned.columns:
        cleaned = cleaned[cleaned[required_column].astype(str).str.strip() != ""]
    return cleaned.reset_index(drop=True)


def save_table(path: Path, table: pd.DataFrame, columns: list[str]) -> None:
    ensure_database()
    backup_database()
    required_columns = {
        FUNDING_FILE.name: "Name",
        TASKS_FILE.name: "Aufgabe",
        CONTACTS_FILE.name: "Name",
    }
    output = clean_table(table, columns, required_columns.get(path.name))
    for column in ["Frist", "letzte Kontaktaufnahme"]:
        if column in output.columns:
            output[column] = output[column].apply(format_date_value)
    output = output.fillna("")
    table_name, _ = table_spec(path, columns)
    engine = database_engine()
    with engine.begin() as connection:
        connection.execute(text(f'DELETE FROM "{table_name}"'))
        output[columns].to_sql(table_name, connection, if_exists="append", index=False)
    output.to_csv(path, index=False)
    if st is not None:
        st.cache_data.clear()
        st.session_state["database_schema_checked"] = True


def ensure_table_schema(connection, table_name: str, columns: list[str]) -> None:
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        pd.DataFrame(columns=columns).to_sql(table_name, connection, if_exists="replace", index=False)
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    for column in columns:
        if column not in existing_columns:
            connection.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column}" TEXT'))


def ensure_database() -> None:
    if st is not None and st.session_state.get("database_schema_checked"):
        return

    BACKUP_DIR.mkdir(exist_ok=True)
    settings = load_settings()
    engine = database_engine()

    with engine.begin() as connection:
        for table_name, columns in [spec for spec in TABLES.values()]:
            ensure_table_schema(connection, table_name, columns)
        ensure_table_schema(connection, "importe", IMPORT_COLUMNS)
        ensure_table_schema(connection, "sync_files", SYNC_COLUMNS)

        migration_key = "cloud_migrated" if is_cloud_database() else "sqlite_migrated"
        if not settings.get(migration_key):
            for csv_name, (table_name, columns) in TABLES.items():
                existing = pd.read_sql_query(text(f'SELECT * FROM "{table_name}"'), connection)
                if not existing.empty:
                    continue
                csv_path = DATA_DIR / csv_name
                ensure_csv(csv_path, columns)
                table = pd.read_csv(csv_path, dtype=str).fillna("")
                for column in columns:
                    if column not in table.columns:
                        table[column] = ""
                table[columns].to_sql(table_name, connection, if_exists="append", index=False)
            settings[migration_key] = datetime.now().isoformat(timespec="seconds")
            save_settings(settings)

    if st is not None:
        st.session_state["database_schema_checked"] = True


def backup_database() -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if not is_cloud_database() and DB_FILE.exists():
        backup_path = BACKUP_DIR / f"riegersburg-{timestamp}.db"
        shutil.copy2(DB_FILE, backup_path)
        return backup_path

    backup_path = BACKUP_DIR / f"riegersburg-{timestamp}.zip"
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for csv_name, (table_name, columns) in TABLES.items():
            table = load_table(DATA_DIR / csv_name, columns)
            csv_bytes = table.to_csv(index=False).encode("utf-8")
            archive.writestr(csv_name, csv_bytes)
    return backup_path


def export_csv_files() -> None:
    for csv_name, (table_name, columns) in TABLES.items():
        table = load_table(DATA_DIR / csv_name, columns)
        table[columns].to_csv(DATA_DIR / csv_name, index=False)


def real_tasks(tasks: pd.DataFrame) -> pd.DataFrame:
    if tasks.empty or "Aufgabe" not in tasks.columns:
        return pd.DataFrame(columns=TASK_COLUMNS)
    return tasks[tasks["Aufgabe"].astype(str).str.strip() != ""].copy()


def normalized_column(table: pd.DataFrame, column: str) -> pd.Series:
    if table.empty or column not in table.columns:
        return pd.Series(dtype=str)
    return table[column].astype(str).str.strip().str.lower()


def task_data_editor(tasks: pd.DataFrame) -> pd.DataFrame:
    editable_table = tasks.copy()
    editable_table.insert(0, "Löschen", False)
    editable_table["Frist"] = pd.to_datetime(editable_table["Frist"], errors="coerce")
    edited = st.data_editor(
        editable_table,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config={
            "Löschen": st.column_config.CheckboxColumn("Löschen"),
            "Priorität": st.column_config.SelectboxColumn("Priorität", options=PRIORITIES, required=False),
            "Status": st.column_config.SelectboxColumn("Status", options=TASK_STATUS, required=False),
            "Frist": st.column_config.DateColumn("Frist", format="YYYY-MM-DD"),
        },
        key="tasks_editor",
    )
    if "Löschen" in edited.columns:
        edited = edited[edited["Löschen"] != True].drop(columns=["Löschen"])
    return edited[TASK_COLUMNS]


def open_task_rows(tasks: pd.DataFrame) -> pd.DataFrame:
    if tasks.empty:
        return tasks
    return tasks[normalized_column(tasks, "Status") != "erledigt"].copy()


def completed_task_rows(tasks: pd.DataFrame) -> pd.DataFrame:
    if tasks.empty:
        return tasks
    return tasks[normalized_column(tasks, "Status") == "erledigt"].copy()


def acute_task_rows(tasks: pd.DataFrame) -> pd.DataFrame:
    urgent = add_urgency(open_task_rows(tasks))
    if urgent.empty:
        return urgent
    return urgent[urgent["Dringlichkeit"] >= 45].head(6)


def mark_tasks_done(all_tasks: pd.DataFrame, task_names: list[str]) -> pd.DataFrame:
    updated = all_tasks.copy()
    done_names = {str(name).strip() for name in task_names if str(name).strip()}
    if not done_names or "Aufgabe" not in updated.columns:
        return updated
    mask = updated["Aufgabe"].astype(str).str.strip().isin(done_names)
    updated.loc[mask, "Status"] = "erledigt"
    return updated


def task_key(row: pd.Series) -> str:
    return hashlib.sha256(
        "|".join(
            [
                str(row.get("Aufgabe", "")).strip(),
                str(row.get("Verantwortlich", "")).strip(),
                str(row.get("Frist", "")).strip(),
                str(row.get("Bezug zu Förderstelle", "")).strip(),
            ]
        ).encode("utf-8")
    ).hexdigest()[:12]


def infer_next_step(task_text: str, notes: str) -> str:
    combined = f"{task_text} {notes}".lower()
    if "unterlagen" in combined and any(word in combined for word in ["übermitteln", "senden", "nachreichen"]):
        return "Unterlagen zusammenstellen, prüfen und an die genannte Stelle übermitteln."
    if any(word in combined for word in ["kontakt", "telefon", "anrufen", "nachfassen"]):
        return "Kontakt aufnehmen bzw. nachfassen und das Ergebnis danach kurz im Cockpit dokumentieren."
    if any(word in combined for word in ["prüfen", "klären", "abklären"]):
        return "Offene Punkte prüfen, Ergebnis festhalten und daraus den nächsten konkreten Schritt ableiten."
    if any(word in combined for word in ["termin", "meeting", "besprechung"]):
        return "Termin koordinieren, Teilnehmer festlegen und Vorbereitungsunterlagen bereitlegen."
    if any(word in combined for word in ["antrag", "förder", "einreichen"]):
        return "Förderlogik und Unterlagen prüfen, Zuständigkeit klären und Einreichschritt vorbereiten."
    return "Aufgabe anhand der Notizen konkretisieren, Zuständigkeit bestätigen und den nächsten Schritt dokumentieren."


def show_task_detail(row: pd.Series) -> None:
    st.markdown(f"**{row.get('Aufgabe', '')}**")
    detail_cols = st.columns(4)
    detail_cols[0].write(f"Verantwortlich: {row.get('Verantwortlich', '') or '-'}")
    detail_cols[1].write(f"Priorität: {row.get('Priorität', '') or '-'}")
    detail_cols[2].write(f"Status: {row.get('Status', '') or '-'}")
    detail_cols[3].write(f"Frist: {row.get('Frist', '') or '-'}")
    if str(row.get("Bezug zu Förderstelle", "")).strip():
        st.write(f"Bezug: {row.get('Bezug zu Förderstelle', '')}")
    if str(row.get("Notizen", "")).strip():
        st.write(f"Notizen: {row.get('Notizen', '')}")
    st.info(infer_next_step(str(row.get("Aufgabe", "")), str(row.get("Notizen", ""))))


def import_fingerprint(source_type: str, title: str, body: str, sender_or_participants: str = "") -> str:
    normalized = "\n".join(
        [
            source_type.strip().lower(),
            title.strip().lower(),
            sender_or_participants.strip().lower(),
            re.sub(r"\s+", " ", body).strip().lower(),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def import_exists(fingerprint: str) -> bool:
    ensure_database()
    engine = database_engine()
    with engine.connect() as connection:
        result = connection.execute(text('SELECT COUNT(*) FROM "importe" WHERE fingerprint = :fingerprint'), {"fingerprint": fingerprint}).scalar()
    return bool(result)


def record_import(fingerprint: str, source_type: str, title: str, notes: str = "") -> None:
    ensure_database()
    engine = database_engine()
    row = pd.DataFrame(
        [
            {
                "fingerprint": fingerprint,
                "source_type": source_type,
                "title": title,
                "imported_at": datetime.now().isoformat(timespec="seconds"),
                "notes": notes,
            }
        ],
        columns=IMPORT_COLUMNS,
    )
    with engine.begin() as connection:
        existing = connection.execute(text('SELECT COUNT(*) FROM "importe" WHERE fingerprint = :fingerprint'), {"fingerprint": fingerprint}).scalar()
        if not existing:
            row.to_sql("importe", connection, if_exists="append", index=False)


def load_imports() -> pd.DataFrame:
    ensure_database()
    engine = database_engine()
    with engine.connect() as connection:
        table = pd.read_sql_query(text('SELECT * FROM "importe"'), connection, dtype=str).fillna("")
    for column in IMPORT_COLUMNS:
        if column not in table.columns:
            table[column] = ""
    if "imported_at" in table.columns:
        table = table.sort_values("imported_at", ascending=False)
    return table[IMPORT_COLUMNS]


def load_synced_files(include_content: bool = False) -> pd.DataFrame:
    ensure_database()
    engine = database_engine()
    with engine.connect() as connection:
        table = pd.read_sql_query(text('SELECT * FROM "sync_files"'), connection, dtype=str).fillna("")
    for column in SYNC_COLUMNS:
        if column not in table.columns:
            table[column] = ""
    if "synced_at" in table.columns:
        table = table.sort_values("synced_at", ascending=False)
    visible_columns = SYNC_COLUMNS if include_content else [column for column in SYNC_COLUMNS if column != "content"]
    return table[visible_columns]


def load_synced_file_content(fingerprint: str) -> str:
    if not fingerprint:
        return ""
    ensure_database()
    engine = database_engine()
    with engine.connect() as connection:
        result = connection.execute(text('SELECT content FROM "sync_files" WHERE fingerprint = :fingerprint'), {"fingerprint": fingerprint}).scalar()
    return str(result or "")


def update_synced_file_status(fingerprint: str, status: str, notes: str = "") -> None:
    if not fingerprint:
        return
    ensure_database()
    engine = database_engine()
    with engine.begin() as connection:
        connection.execute(
            text('UPDATE "sync_files" SET status = :status, notes = :notes WHERE fingerprint = :fingerprint'),
            {"fingerprint": fingerprint, "status": status, "notes": notes},
        )


def format_date_value(value: object) -> str:
    if value is None or value == "":
        return ""
    if pd.isna(value):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return str(value)
    return parsed.date().isoformat()


def parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def urgency_score(row: pd.Series, status_column: str = "Status") -> int:
    priority = str(row.get("Priorität", "")).lower()
    deadline = parse_date(str(row.get("Frist", "")))
    status = str(row.get(status_column, "")).strip().lower()
    if status in {"erledigt", "bewilligt", "abgelehnt"}:
        return 0

    score = {"hoch": 50, "mittel": 25, "niedrig": 10}.get(priority, 15)
    if deadline:
        days_left = (deadline - date.today()).days
        if days_left < 0:
            score += 60
        elif days_left <= 7:
            score += 45
        elif days_left <= 30:
            score += 25
        else:
            score += 5
    return score


def urgency_label(score: int) -> str:
    if score >= 70:
        return "kritisch"
    if score >= 45:
        return "hoch"
    if score >= 25:
        return "mittel"
    if score > 0:
        return "niedrig"
    return "abgeschlossen"


def add_urgency(table: pd.DataFrame) -> pd.DataFrame:
    if table.empty:
        return table.copy()
    enriched = table.copy()
    enriched["Dringlichkeit"] = enriched.apply(urgency_score, axis=1)
    enriched["Einordnung"] = enriched["Dringlichkeit"].apply(urgency_label)
    return enriched.sort_values(["Dringlichkeit", "Frist"], ascending=[False, True], na_position="last")


def scan_documents(folder: Path) -> pd.DataFrame:
    if not folder.exists() or not folder.is_dir():
        return pd.DataFrame(columns=["Datei", "Typ", "Ordner", "Größe KB", "Pfad"])

    rows = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in {".docx", ".pdf", ".md", ".csv"}:
            continue
        rows.append(
            {
                "Datei": path.name,
                "Typ": path.suffix.lower().lstrip("."),
                "Ordner": str(path.parent.relative_to(folder)),
                "Größe KB": round(path.stat().st_size / 1024, 1),
                "Pfad": str(path),
            }
        )
    return pd.DataFrame(rows)


def repair_text_encoding(text_value: str) -> str:
    replacements = {
        "Ã¤": "ä",
        "Ã¶": "ö",
        "Ã¼": "ü",
        "Ã„": "Ä",
        "Ã–": "Ö",
        "Ãœ": "Ü",
        "ÃŸ": "ß",
        "â€“": "-",
        "â€”": "-",
        "â€ž": "„",
        "â€œ": "“",
        "â€": "”",
        "â€™": "’",
    }
    repaired = str(text_value or "")
    for broken, fixed in replacements.items():
        repaired = repaired.replace(broken, fixed)
    word_replacements = {
        "gro�": "groß",
        "Gro�": "Groß",
        "Gru�": "Gruß",
        "gru�": "gruß",
        "Stra�e": "Straße",
        "stra�e": "straße",
        "Ma�": "Maß",
        "ma�": "maß",
        "au�": "auß",
        "Au�": "Auß",
        "Gr��e": "Grüße",
        "gr��e": "grüße",
        "Gr��en": "Grüßen",
        "gr��en": "grüßen",
    }
    for broken, fixed in word_replacements.items():
        repaired = repaired.replace(broken, fixed)
    repaired = re.sub(r"(?<=[A-Za-zÄÖÜäöü])�(?=[A-Za-zÄÖÜäöü])", "ß", repaired)
    return repaired


def clean_import_line(line: str) -> str:
    line = repair_text_encoding(line)
    line = re.sub(r"^\s*[-*•\d.)\]]+\s*", "", line.strip())
    return re.sub(r"\s+", " ", line).strip()


def split_import_candidates(body: str) -> list[tuple[str, bool]]:
    candidates = []
    for raw_line in repair_text_encoding(body).splitlines():
        if not raw_line.strip():
            continue
        bullet_like = bool(re.match(r"^\s*[-*•\d.)\]]+", raw_line))
        if bullet_like:
            candidates.append((raw_line, True))
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", raw_line.strip()):
            if sentence:
                candidates.append((sentence, False))
    return candidates


def task_candidate_is_useful(line: str, bullet_like: bool, action_words: list[str]) -> bool:
    lowered = line.lower()
    if len(line) < 12:
        return False
    if len(line) > 230 and not bullet_like:
        return False
    if len(line.split()) > 34 and not bullet_like:
        return False
    soft_noise = [
        "vielen dank",
        "herzlichen dank",
        "mit freundlichen grüßen",
        "beste grüße",
        "liebe grüße",
        "ich hoffe",
        "zur kenntnis",
        "anbei",
        "wie besprochen",
        "ich freue mich",
    ]
    if any(phrase in lowered for phrase in soft_noise):
        return False
    strong_patterns = [
        r"\bbitte\b",
        r"\bbis\s+\d{1,2}\.",
        r"\bfrist\b",
        r"\bnächste[rs]?\s+schritt",
        r"\bzu\s+klären\b",
        r"\bzu\s+prüfen\b",
        r"\bzu\s+übermitteln\b",
        r"\bunterlagen\s+(?:senden|übermitteln|nachreichen|vorbereiten)\b",
    ]
    if any(re.search(pattern, lowered) for pattern in strong_patterns):
        return True
    if bullet_like and any(word in lowered for word in action_words):
        return True
    return False


def shorten_task_text(line: str) -> str:
    cleaned = clean_import_line(line)
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    if 20 <= len(first_sentence) <= 180:
        return first_sentence
    return cleaned[:180].rstrip(" ,.;")


def extract_deadline(text_value: str) -> str:
    patterns = [
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b",
        r"\b(\d{1,2})\.(\d{1,2})\.(\d{2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_value)
        if not match:
            continue
        if len(match.groups()) == 1:
            return match.group(1)
        day, month, year = match.groups()
        if len(year) == 2:
            year = "20" + year
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return ""


def guess_priority(text_value: str) -> str:
    lowered = text_value.lower()
    if any(word in lowered for word in ["dringend", "sofort", "kritisch", "frist", "bis morgen", "deadline", "eilt"]):
        return "hoch"
    if any(word in lowered for word in ["prüfen", "klären", "vorbereiten", "nachfassen", "kontakt", "einreichen"]):
        return "mittel"
    return "niedrig"


def guess_funding_reference(text_value: str, funding: pd.DataFrame) -> str:
    lowered = text_value.lower()
    for _, row in funding.iterrows():
        name = str(row.get("Name", ""))
        if name and name.lower() in lowered:
            return name
    references = [
        ("Bundesdenkmalamt", "Bundesdenkmalamt - Bauaufnahme / Befundung"),
        ("BDA", "Bundesdenkmalamt - Bauaufnahme / Befundung"),
        ("ÖROK", "EFRE / Operationelles Programm 2028-2034"),
        ("EFRE", "EFRE / Operationelles Programm 2028-2034"),
        ("Operationelles Programm", "EFRE / Operationelles Programm 2028-2034"),
        ("LIFE", "LIFE-Programm"),
        ("LEADER", "LEADER"),
        ("Interreg", "Interreg"),
        ("Totschnig", "Bund / BML - ländliche Entwicklung und Kulturerbe"),
        ("Landwirtschaftsministerium", "Bund / BML - ländliche Entwicklung und Kulturerbe"),
        ("Schrägaufzug", "WIGA / SFG - Schrägaufzug"),
    ]
    for keyword, reference in references:
        if keyword.lower() in lowered:
            return reference
    return ""


def extract_task_suggestions(source_type: str, title: str, body: str, owner: str, funding: pd.DataFrame) -> pd.DataFrame:
    action_words = [
        "bitte",
        "soll",
        "muss",
        "müssen",
        "klären",
        "prüfen",
        "vorbereiten",
        "nachfassen",
        "kontakt",
        "einreichen",
        "erstellen",
        "übermitteln",
        "abstimmen",
        "entscheiden",
        "organisieren",
        "termin",
        "nächste",
        "to do",
        "todo",
        "action",
    ]
    rows = []
    seen = set()
    for raw_line, bullet_like in split_import_candidates(body):
        line = clean_import_line(raw_line)
        if not task_candidate_is_useful(line, bullet_like, action_words):
            continue
        task_text = shorten_task_text(line)
        if task_text.lower() in seen:
            continue
        seen.add(task_text.lower())
        rows.append(
            {
                "Aufgabe": task_text,
                "Verantwortlich": owner,
                "Priorität": guess_priority(line),
                "Status": "offen",
                "Frist": extract_deadline(line),
                "Bezug zu Förderstelle": guess_funding_reference(line, funding),
                "Notizen": f"Import aus {source_type}: {title}".strip(),
            }
        )
    return pd.DataFrame(rows, columns=TASK_COLUMNS)


def normalize_contact_name(name: str) -> str:
    normalized = clean_import_line(name).lower()
    normalized = re.sub(r"\b(frau|herr|dr\.?|mag\.?|mag\.a|di|dipl\.-ing\.?|lh|bm)\b", "", normalized)
    normalized = re.sub(r"[^a-zäöüß ]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def existing_contact_keys(existing_contacts: pd.DataFrame | None) -> tuple[set[str], set[str]]:
    if existing_contacts is None or existing_contacts.empty:
        return set(), set()
    names = set()
    emails = set()
    for _, row in existing_contacts.fillna("").iterrows():
        name_key = normalize_contact_name(str(row.get("Name", "")))
        if name_key:
            names.add(name_key)
        email = str(row.get("E-Mail", "")).strip().lower()
        if email:
            emails.add(email)
    return names, emails


def extract_contact_suggestions(
    source_type: str,
    title: str,
    body: str,
    sender_name: str = "",
    sender_email: str = "",
    participants: str = "",
    existing_contacts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows = []
    seen = set()
    known_names, known_emails = existing_contact_keys(existing_contacts)
    clean_title = re.sub(r"\.(docx|pdf|txt|md|srt|vtt|eml)$", "", repair_text_encoding(title), flags=re.IGNORECASE)
    searchable_text = repair_text_encoding("\n".join([clean_title, body, participants]))

    def add_contact(name: str, email: str = "", note: str = "") -> None:
        clean_name = clean_import_line(name).strip(" ,;")
        if not clean_name and email:
            clean_name = email.split("@")[0]
        if len(clean_name) < 3:
            return
        name_key = normalize_contact_name(clean_name)
        email_key = email.strip().lower()
        if name_key and name_key in known_names:
            return
        if email_key and email_key in known_emails:
            return
        key = (clean_name.lower(), email.lower())
        if key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "Name": clean_name,
                "Organisation": "",
                "Funktion": "",
                "E-Mail": email,
                "Telefon": "",
                "Relevanz": "mittel",
                "letzte Kontaktaufnahme": date.today().isoformat(),
                "nächste Aktion": "",
                "Notizen": note or f"Import aus {source_type}: {title}",
            }
        )

    if sender_name or sender_email:
        add_contact(sender_name, sender_email, f"Absender aus {source_type}: {title}")

    for email in sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", searchable_text))):
        add_contact("", email, f"Im Text gefunden: {title}")

    person_pattern = r"\b(?:Frau|Herr)?[ \t]*(?:Dr\.|Mag\.|Mag\.a|DI|Dipl\.-Ing\.|LH|BM)[ \t]+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+(?:[ \t]+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+)?"
    for name in sorted(set(re.findall(person_pattern, searchable_text))):
        add_contact(name, "", f"Im Text/Teilnehmerkreis gefunden: {title}")

    meeting_partner_pattern = r"\b(?:mit|bei|an|von)[ \t]+((?:Frau|Herr)?[ \t]*(?:Dr\.|Mag\.|Mag\.a|DI|Dipl\.-Ing\.)?[ \t]*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+(?:[ \t]+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+){1,2})"
    for name in sorted(set(re.findall(meeting_partner_pattern, searchable_text))):
        if any(stopword in name.lower() for stopword in ["der ", "die ", "das ", "dem ", "den ", "und "]):
            continue
        add_contact(name, "", f"Gesprächspartner aus Titel/Text: {title}")

    for participant in re.split(r"[,;\n]", participants):
        add_contact(participant, "", f"Teilnehmer aus Protokoll: {title}")

    return pd.DataFrame(rows, columns=CONTACT_COLUMNS)


def read_uploaded_text(uploaded_file) -> str:
    content = uploaded_file.getvalue()
    for encoding in ["utf-8", "utf-8-sig", "cp1252", "latin-1"]:
        try:
            return repair_text_encoding(content.decode(encoding))
        except UnicodeDecodeError:
            continue
    return repair_text_encoding(content.decode("utf-8", errors="ignore"))


def parse_raw_email(raw_text: str, uploaded_file=None) -> dict[str, str]:
    if uploaded_file is not None and uploaded_file.name.lower().endswith(".eml"):
        message = BytesParser(policy=policy.default).parsebytes(uploaded_file.getvalue())
    else:
        message = Parser(policy=policy.default).parsestr(raw_text)

    subject = repair_text_encoding(str(message.get("subject", "") or ""))
    sender_name, sender_email = parseaddr(str(message.get("from", "") or ""))
    sender_name = repair_text_encoding(sender_name)
    raw_date = str(message.get("date", "") or "")
    mail_date = ""
    if raw_date:
        try:
            mail_date = parsedate_to_datetime(raw_date).date().isoformat()
        except (TypeError, ValueError):
            mail_date = ""

    body = ""
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("content-disposition", "")).lower()
            if content_type == "text/plain" and "attachment" not in disposition:
                body = repair_text_encoding(part.get_content())
                break
    else:
        try:
            body = repair_text_encoding(message.get_content())
        except Exception:
            body = raw_text

    if not subject and not sender_email and not body.strip():
        body = raw_text

    return {
        "subject": subject,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "date": mail_date,
        "body": repair_text_encoding(str(body or raw_text)),
    }


def append_unique(existing: pd.DataFrame, additions: pd.DataFrame, key_column: str) -> pd.DataFrame:
    if additions.empty:
        return existing.copy()
    combined = pd.concat([existing, additions], ignore_index=True).fillna("")
    return combined.drop_duplicates(subset=[key_column], keep="last")


def protocol_summary(title: str, protocol_date: str, participants: str, body: str, tasks: pd.DataFrame) -> str:
    lines = [
        f"# Protokollauswertung: {title or 'ohne Titel'}",
        "",
        f"Datum: {protocol_date or date.today().isoformat()}",
        f"Teilnehmer: {participants or 'nicht angegeben'}",
        "",
        "## Erkannte Aufgaben",
        "",
    ]
    if tasks.empty:
        lines.append("Keine Aufgaben automatisch erkannt.")
    else:
        for _, row in tasks.iterrows():
            lines.append(f"- {row['Aufgabe']} | verantwortlich: {row['Verantwortlich']} | Frist: {row['Frist']}")
    lines.extend(["", "## Textauszug", "", body[:2500]])
    return "\n".join(lines)


def markdown_report(funding: pd.DataFrame, tasks: pd.DataFrame, contacts: pd.DataFrame) -> str:
    tasks = real_tasks(tasks)
    urgent_tasks = add_urgency(open_task_rows(tasks)).head(5)
    open_tasks = tasks[normalized_column(tasks, "Status") != "erledigt"] if not tasks.empty else tasks
    active_funding = funding[~funding["Status"].str.lower().isin(["bewilligt", "abgelehnt"])] if not funding.empty else funding
    critical_tasks = urgent_tasks[urgent_tasks["Einordnung"].isin(["kritisch", "hoch"])] if not urgent_tasks.empty else urgent_tasks
    next_funding = active_funding[active_funding["nächste Aktion"].astype(str).str.strip() != ""] if not active_funding.empty else active_funding

    lines = [
        "# Riegersburg Sanierungsprogramm - Kurzbericht",
        "",
        f"Erstellt am: {date.today().strftime('%d.%m.%Y')}",
        "",
        "## Lage",
        "",
        (
            f"Derzeit sind {len(active_funding)} von {len(funding)} Förderlinien aktiv. "
            f"Im Cockpit stehen {len(open_tasks)} offene Aufgaben, davon {len(critical_tasks)} mit hoher oder kritischer Dringlichkeit."
        ),
        "",
        "## Was jetzt wichtig ist",
        "",
    ]

    if urgent_tasks.empty:
        lines.append("Aktuell sind keine dringenden Aufgaben erfasst.")
    else:
        for _, row in urgent_tasks.iterrows():
            sentence = f"{row['Aufgabe']} liegt bei {row['Verantwortlich'] or 'noch nicht zugeordnet'}"
            if str(row.get("Frist", "")).strip():
                sentence += f" und sollte bis {row['Frist']} erledigt werden"
            if str(row.get("Bezug zu Förderstelle", "")).strip():
                sentence += f". Der Bezug liegt bei {row['Bezug zu Förderstelle']}"
            lines.append(f"- {sentence}.")

    lines.extend(["", "## Förderstand", ""])
    if next_funding.empty:
        lines.append("Bei den aktiven Förderlinien ist derzeit keine konkrete nächste Aktion hinterlegt.")
    else:
        for _, row in next_funding.head(5).iterrows():
            lines.append(
                f"- Für {row['Name']} ist der Status {row['Status']}. Als nächster Schritt ist vorgesehen: {row['nächste Aktion']}."
            )

    return "\n".join(lines)


def pdf_report(markdown_text: str) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.6 * cm, leftMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10))
    story = []

    for line in markdown_text.splitlines():
        if line.startswith("# "):
            story.append(Paragraph(line[2:], styles["Title"]))
            story.append(Spacer(1, 0.25 * cm))
        elif line.startswith("## "):
            story.append(Spacer(1, 0.2 * cm))
            story.append(Paragraph(line[3:], styles["Heading2"]))
        elif line.startswith("### "):
            story.append(Paragraph(line[4:], styles["Heading3"]))
        elif line.startswith("- "):
            story.append(Paragraph("• " + line[2:].replace("**", ""), styles["Small"]))
        elif line.strip():
            story.append(Paragraph(line.replace("**", ""), styles["BodyText"]))
        else:
            story.append(Spacer(1, 0.12 * cm))

    doc.build(story)
    return buffer.getvalue()


def sample_data() -> None:
    if load_table(FUNDING_FILE, FUNDING_COLUMNS).empty:
        save_table(
            FUNDING_FILE,
            pd.DataFrame(
            [
                {
                    "Name": "Land Steiermark - Revitalisierung",
                    "Ebene": "Land",
                    "zuständige Stelle": "Abteilung Kultur / Denkmalpflege",
                    "Ansprechpartner": "offen",
                    "thematische Eignung": "Baukulturelles Erbe, regionale Wirkung",
                    "geschätztes Förderpotenzial": "mittel bis hoch",
                    "Status": "in Prüfung",
                    "nächste Aktion": "Förderkriterien abgleichen",
                    "Frist": "",
                    "Notizen": "Erste Linie für Landesabstimmung.",
                },
                {
                    "Name": "Bundesdenkmalamt",
                    "Ebene": "Bund",
                    "zuständige Stelle": "Landeskonservatorat Steiermark",
                    "Ansprechpartner": "offen",
                    "thematische Eignung": "Denkmalschutz, Substanzerhalt",
                    "geschätztes Förderpotenzial": "mittel",
                    "Status": "Kontakt aufnehmen",
                    "nächste Aktion": "Sanierungsumfang abstimmen",
                    "Frist": "",
                    "Notizen": "Fachliche Freigaben früh sichern.",
                },
            ]
            ),
            FUNDING_COLUMNS,
        )

    if load_table(TASKS_FILE, TASK_COLUMNS).empty:
        save_table(
            TASKS_FILE,
            pd.DataFrame(
            [
                {
                    "Aufgabe": "Fördermatrix vervollständigen",
                    "Verantwortlich": "Projektteam",
                    "Priorität": "hoch",
                    "Status": "offen",
                    "Frist": "",
                    "Bezug zu Förderstelle": "alle",
                    "Notizen": "Potenziale und Fristen konsolidieren.",
                }
            ]
            ),
            TASK_COLUMNS,
        )


def remember_login_token(configured_password: str) -> str:
    return hashlib.sha256(f"riegersburg-login:{configured_password}".encode("utf-8")).hexdigest()


def get_query_param(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        return value[0] if value else ""
    return str(value)


def check_login(settings: dict[str, str]) -> bool:
    configured_password = secret_value("app_password") or settings.get("app_password", DEFAULT_PASSWORD)
    if st.session_state.get("authenticated"):
        return True

    remember_token = remember_login_token(configured_password)
    if hmac.compare_digest(get_query_param("zugang"), remember_token):
        st.session_state["authenticated"] = True
        return True

    st.title("Riegersburg Sanierungsprogramm")
    st.caption("Bitte anmelden, um das gemeinsame Cockpit zu öffnen.")
    password = st.text_input("Passwort", type="password")
    remember_device = st.checkbox("Auf diesem Gerät merken", value=True)
    if st.button("Anmelden", type="primary"):
        if hmac.compare_digest(password, configured_password):
            st.session_state["authenticated"] = True
            if remember_device:
                st.query_params["zugang"] = remember_token
            st.rerun()
        else:
            st.error("Das Passwort stimmt nicht.")
    if "app_password" not in settings:
        st.info("Das voreingestellte Passwort lautet: Riegersburg2030")
    return False


def sidebar_admin(settings: dict[str, str]) -> None:
    st.header("Aktionen")
    if st.button("Abmelden", width="stretch"):
        st.session_state["authenticated"] = False
        if "zugang" in st.query_params:
            del st.query_params["zugang"]
        st.rerun()

    if st.button("Backup erstellen", width="stretch"):
        backup_path = backup_database()
        if backup_path:
            st.success(f"Backup erstellt: {backup_path.name}")
        else:
            st.warning("Noch keine Datenbank für ein Backup gefunden.")

    if st.button("CSV-Dateien aktualisieren", width="stretch"):
        export_csv_files()
        st.success("CSV-Dateien wurden aus der Datenbank aktualisiert.")

    if st.button("Beispieldaten anlegen", width="stretch"):
        sample_data()
        st.rerun()

    if is_cloud_database():
        st.write("Datenbank: Cloud-Postgres")
    else:
        st.write("Datenbank: `data/riegersburg.db`")
    st.write("Backups: `data/backups/`")

    with st.expander("Logo"):
        logo_url = st.text_input("Logo-URL", value=settings.get("logo_url", ""), placeholder="https://.../logo.png")
        local_logo = st.file_uploader("Logo lokal hochladen", type=["png", "jpg", "jpeg"], key="logo_upload")
        if st.button("Logo speichern"):
            if local_logo is not None:
                logo_dir = DATA_DIR / "assets"
                logo_dir.mkdir(exist_ok=True)
                suffix = Path(local_logo.name).suffix.lower() or ".png"
                logo_path = logo_dir / f"logo{suffix}"
                logo_path.write_bytes(local_logo.getvalue())
                settings["logo_path"] = str(logo_path)
                settings["logo_url"] = ""
            else:
                settings["logo_url"] = logo_url
                settings.pop("logo_path", None)
            save_settings(settings)
            st.success("Logo gespeichert.")

    with st.expander("Passwort ändern"):
        if secret_value("app_password"):
            st.info("In der Cloud wird das Passwort über die App-Secrets geändert.")
            return
        current_password = st.text_input("Aktuelles Passwort", type="password", key="current_password")
        new_password = st.text_input("Neues Passwort", type="password", key="new_password")
        repeat_password = st.text_input("Neues Passwort wiederholen", type="password", key="repeat_password")
        if st.button("Neues Passwort speichern"):
            configured_password = settings.get("app_password", DEFAULT_PASSWORD)
            if not hmac.compare_digest(current_password, configured_password):
                st.error("Das aktuelle Passwort stimmt nicht.")
            elif len(new_password) < 8:
                st.error("Bitte ein Passwort mit mindestens 8 Zeichen wählen.")
            elif new_password != repeat_password:
                st.error("Die neuen Passwörter stimmen nicht überein.")
            else:
                settings["app_password"] = new_password
                save_settings(settings)
                st.success("Passwort geändert.")


def data_editor(label: str, table: pd.DataFrame, columns: list[str], key: str, select_options: dict[str, list[str]]) -> pd.DataFrame:
    editable_table = table.copy()
    config = {}
    for column, options in select_options.items():
        config[column] = st.column_config.SelectboxColumn(column, options=options, required=False)
    if "Frist" in columns:
        editable_table["Frist"] = pd.to_datetime(editable_table["Frist"], errors="coerce")
        config["Frist"] = st.column_config.DateColumn("Frist", format="YYYY-MM-DD")
    if "letzte Kontaktaufnahme" in columns:
        editable_table["letzte Kontaktaufnahme"] = pd.to_datetime(editable_table["letzte Kontaktaufnahme"], errors="coerce")
        config["letzte Kontaktaufnahme"] = st.column_config.DateColumn("letzte Kontaktaufnahme", format="YYYY-MM-DD")

    st.subheader(label)
    return st.data_editor(
        editable_table,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config=config,
        key=key,
    )


def show_header(settings: dict[str, str]) -> None:
    logo_url = settings.get("logo_url", "")
    logo_path = settings.get("logo_path", "")
    default_logo = str(DEFAULT_LOGO_FILE) if DEFAULT_LOGO_FILE.exists() else ""
    logo_source = logo_url or logo_path or default_logo
    if logo_source:
        left, right = st.columns([1, 7])
        with left:
            st.image(logo_source, width=120)
        with right:
            st.title("Riegersburg Sanierungsprogramm")
            st.caption("Gemeinsames Förder- und Aufgaben-Cockpit")
    else:
        st.title("Riegersburg Sanierungsprogramm")
        st.caption("Gemeinsames Förder- und Aufgaben-Cockpit")


def import_panel(funding: pd.DataFrame, tasks: pd.DataFrame, contacts: pd.DataFrame) -> None:
    st.subheader("E-Mails und Protokolle importieren")
    st.caption("Die App erstellt Vorschläge. Gespeichert wird erst, wenn ihr die Vorschläge bestätigt.")

    mail_tab, protocol_tab = st.tabs(["E-Mail", "Protokoll / Plaud"])

    with mail_tab:
        uploaded_email = st.file_uploader("E-Mail-Datei hochladen", type=["eml", "txt"], key="mail_upload")
        raw_mail = st.text_area("Oder komplette E-Mail hier einfügen", height=220, placeholder="Am besten die ganze Mail inkl. Von/From, Betreff/Subject und Datum einfügen.", key="mail_raw")
        with st.expander("Felder bei Bedarf manuell ergänzen"):
            sender_name = st.text_input("Absender Name", key="mail_sender_name")
            sender_email = st.text_input("Absender E-Mail", key="mail_sender_email")
            subject = st.text_input("Betreff", key="mail_subject")
            mail_date = st.date_input("Eingangsdatum", value=date.today(), key="mail_date")
        owner = st.text_input("Standard-Verantwortlich", value="Sonja Liechtenstein / Emanuel Liechtenstein", key="mail_owner")

        if st.button("E-Mail auswerten", type="primary"):
            raw_text = read_uploaded_text(uploaded_email) if uploaded_email is not None else raw_mail
            if not raw_text.strip():
                st.warning("Bitte zuerst eine E-Mail-Datei hochladen oder den E-Mail-Text einfügen.")
            else:
                try:
                    parsed_mail = parse_raw_email(raw_text, uploaded_email)
                    final_subject = subject or parsed_mail["subject"] or f"Mail vom {mail_date.isoformat()}"
                    final_sender_name = sender_name or parsed_mail["sender_name"]
                    final_sender_email = sender_email or parsed_mail["sender_email"]
                    mail_body = parsed_mail["body"] or raw_text
                    fingerprint = import_fingerprint("E-Mail", final_subject, mail_body, final_sender_email or final_sender_name)
                    st.session_state["mail_fingerprint"] = fingerprint
                    st.session_state["mail_duplicate"] = import_exists(fingerprint)
                    if st.session_state["mail_duplicate"]:
                        st.session_state["mail_task_suggestions"] = pd.DataFrame(columns=TASK_COLUMNS)
                        st.session_state["mail_contact_suggestions"] = pd.DataFrame(columns=CONTACT_COLUMNS)
                    else:
                        st.session_state["mail_task_suggestions"] = extract_task_suggestions("E-Mail", final_subject, mail_body, owner, funding)
                        st.session_state["mail_contact_suggestions"] = extract_contact_suggestions(
                            "E-Mail",
                            final_subject,
                            mail_body,
                            final_sender_name,
                            final_sender_email,
                            existing_contacts=contacts,
                        )
                    st.session_state["mail_detected"] = {
                        "Betreff": final_subject,
                        "Absender": final_sender_name,
                        "E-Mail": final_sender_email,
                        "Datum": parsed_mail["date"] or mail_date.isoformat(),
                    }
                except Exception as exc:
                    st.session_state["mail_task_suggestions"] = pd.DataFrame(columns=TASK_COLUMNS)
                    st.session_state["mail_contact_suggestions"] = pd.DataFrame(columns=CONTACT_COLUMNS)
                    st.error(f"Diese E-Mail konnte nicht ausgewertet werden: {exc}")

        if st.session_state.get("mail_detected"):
            st.write("Erkannt:", st.session_state["mail_detected"])
        if st.session_state.get("mail_duplicate"):
            st.warning("Diese E-Mail wurde bereits importiert. Es wurden keine neuen Vorschläge erzeugt.")

        suggested_tasks = st.session_state.get("mail_task_suggestions", pd.DataFrame(columns=TASK_COLUMNS))
        suggested_contacts = st.session_state.get("mail_contact_suggestions", pd.DataFrame(columns=CONTACT_COLUMNS))

        if not suggested_tasks.empty:
            edited_tasks = data_editor("Aufgabenvorschläge aus E-Mail", suggested_tasks, TASK_COLUMNS, "mail_tasks_editor", {"Priorität": PRIORITIES, "Status": TASK_STATUS})
            if st.button("Aufgabenvorschläge speichern"):
                save_table(TASKS_FILE, append_unique(tasks, edited_tasks, "Aufgabe"), TASK_COLUMNS)
                record_import(st.session_state.get("mail_fingerprint", ""), "E-Mail", st.session_state.get("mail_detected", {}).get("Betreff", ""), "Aufgaben gespeichert")
                st.success("Aufgaben aus E-Mail gespeichert.")
                st.rerun()
        else:
            st.info("Noch keine Aufgabenvorschläge. E-Mail-Datei hochladen oder komplette Mail einfügen und auswerten.")

        if not suggested_contacts.empty:
            edited_contacts = data_editor("Kontaktvorschläge aus E-Mail", suggested_contacts, CONTACT_COLUMNS, "mail_contacts_editor", {"Relevanz": RELEVANCE})
            if st.button("Kontaktvorschläge speichern"):
                save_table(CONTACTS_FILE, append_unique(contacts, edited_contacts, "Name"), CONTACT_COLUMNS)
                record_import(st.session_state.get("mail_fingerprint", ""), "E-Mail", st.session_state.get("mail_detected", {}).get("Betreff", ""), "Kontakte gespeichert")
                st.success("Kontakte aus E-Mail gespeichert.")
                st.rerun()

    with protocol_tab:
        protocol_title = st.text_input("Protokolltitel für eingefügten Text", key="protocol_title")
        protocol_date = st.date_input("Besprechungsdatum", value=date.today(), key="protocol_date")
        participants = st.text_area("Teilnehmer", height=90, placeholder="Namen durch Komma oder Zeilenumbruch trennen", key="protocol_participants")
        uploaded_protocols = st.file_uploader("Plaud-/Protokolldateien hochladen", type=["txt", "md", "srt", "vtt"], accept_multiple_files=True, key="protocol_upload")
        protocol_text = st.text_area("Oder Protokolltext / Plaud-Transkript einfügen", height=260, key="protocol_text")
        protocol_owner = st.text_input("Standard-Verantwortlich", value="Projektteam", key="protocol_owner")

        protocol_sources = []
        for uploaded_protocol in uploaded_protocols:
            file_text = read_uploaded_text(uploaded_protocol)
            protocol_sources.append((uploaded_protocol.name, file_text))
        if protocol_text.strip():
            protocol_sources.append((protocol_title or f"Protokoll {protocol_date.isoformat()}", protocol_text))
        if protocol_sources:
            st.caption(f"{len(protocol_sources)} Protokollquelle(n) bereit zur Auswertung.")

        if st.button("Protokoll auswerten", type="primary"):
            all_tasks = []
            all_contacts = []
            summaries = []
            duplicate_titles = []
            fingerprints = []
            for source_title, source_text in protocol_sources:
                fingerprint = import_fingerprint("Protokoll", source_title, source_text, participants)
                if import_exists(fingerprint):
                    duplicate_titles.append(source_title)
                    continue
                fingerprints.append((fingerprint, source_title))
                tasks_for_source = extract_task_suggestions("Protokoll", source_title, source_text, protocol_owner, funding)
                contacts_for_source = extract_contact_suggestions("Protokoll", source_title, source_text, participants=participants, existing_contacts=contacts)
                all_tasks.append(tasks_for_source)
                all_contacts.append(contacts_for_source)
                summaries.append(protocol_summary(source_title, protocol_date.isoformat(), participants, source_text, tasks_for_source))
            st.session_state["protocol_fingerprints"] = fingerprints
            st.session_state["protocol_duplicates"] = duplicate_titles
            st.session_state["protocol_task_suggestions"] = pd.concat(all_tasks, ignore_index=True).drop_duplicates(subset=["Aufgabe"], keep="last") if all_tasks else pd.DataFrame(columns=TASK_COLUMNS)
            st.session_state["protocol_contact_suggestions"] = pd.concat(all_contacts, ignore_index=True).drop_duplicates(subset=["Name"], keep="last") if all_contacts else pd.DataFrame(columns=CONTACT_COLUMNS)
            st.session_state["protocol_summary"] = "\n\n---\n\n".join(summaries)

        if st.session_state.get("protocol_duplicates"):
            st.warning("Bereits importiert: " + ", ".join(st.session_state["protocol_duplicates"]))

        protocol_tasks = st.session_state.get("protocol_task_suggestions", pd.DataFrame(columns=TASK_COLUMNS))
        protocol_contacts = st.session_state.get("protocol_contact_suggestions", pd.DataFrame(columns=CONTACT_COLUMNS))
        summary = st.session_state.get("protocol_summary", "")

        if summary:
            st.text_area("Protokoll-Zusammenfassung", summary, height=260)
            st.download_button(
                "Zusammenfassung als Markdown herunterladen",
                summary,
                file_name=f"protokollauswertung-{date.today().isoformat()}.md",
                mime="text/markdown",
            )

        if not protocol_tasks.empty:
            edited_protocol_tasks = data_editor("Aufgabenvorschläge aus Protokoll", protocol_tasks, TASK_COLUMNS, "protocol_tasks_editor", {"Priorität": PRIORITIES, "Status": TASK_STATUS})
            if st.button("Aufgaben aus Protokoll speichern"):
                save_table(TASKS_FILE, append_unique(tasks, edited_protocol_tasks, "Aufgabe"), TASK_COLUMNS)
                for fingerprint, source_title in st.session_state.get("protocol_fingerprints", []):
                    record_import(fingerprint, "Protokoll", source_title, "Aufgaben gespeichert")
                st.success("Aufgaben aus Protokoll gespeichert.")
                st.rerun()
        else:
            st.info("Noch keine Aufgabenvorschläge. Protokolltext einfügen oder Plaud-Datei hochladen und auswerten.")

        if not protocol_contacts.empty:
            edited_protocol_contacts = data_editor("Kontaktvorschläge aus Protokoll", protocol_contacts, CONTACT_COLUMNS, "protocol_contacts_editor", {"Relevanz": RELEVANCE})
            if st.button("Kontakte aus Protokoll speichern"):
                save_table(CONTACTS_FILE, append_unique(contacts, edited_protocol_contacts, "Name"), CONTACT_COLUMNS)
                for fingerprint, source_title in st.session_state.get("protocol_fingerprints", []):
                    record_import(fingerprint, "Protokoll", source_title, "Kontakte gespeichert")
                st.success("Kontakte aus Protokoll gespeichert.")
                st.rerun()

    with st.expander("Import-Historie"):
        if not st.checkbox("Import-Historie laden", value=False):
            st.info("Die Historie wird erst geladen, wenn der Haken gesetzt ist.")
            return
        imports = load_imports()
        if imports.empty:
            st.info("Noch keine Importe gespeichert.")
        else:
            st.dataframe(imports[["source_type", "title", "imported_at", "notes"]], width="stretch", hide_index=True)


def sync_files_panel(funding: pd.DataFrame, tasks: pd.DataFrame, contacts: pd.DataFrame) -> None:
    st.subheader("Dokumente & Auswertung")
    st.caption("Hier werden synchronisierte Dokumente angezeigt, durchsucht und direkt in Aufgaben oder Kontakte überführt.")

    synced_files = load_synced_files()
    if synced_files.empty:
        st.info("Noch keine synchronisierten Dateien gefunden. Starte zuerst den lokalen Sync-Helfer auf deinem Mac.")
        return

    visible = synced_files.copy()
    show_processed = st.checkbox("Bereits übernommene Dateien anzeigen", value=False)
    if not show_processed:
        visible = visible[visible["status"].str.lower() != "ausgewertet"]

    search = st.text_input("Dokumente filtern", placeholder="z. B. BDA, Wiesenhofer, Förderantrag")
    if search:
        mask = visible.apply(lambda row: search.lower() in " ".join(row.astype(str)).lower(), axis=1)
        visible = visible[mask]

    st.caption(f"{len(visible)} offene Datei(en) sichtbar. Bereits übernommene Dateien bleiben über den Schalter erreichbar.")
    owner = st.text_input("Standard-Verantwortlich", value="Projektteam", key="sync_owner")
    participants = st.text_area("Teilnehmer / Kontext", height=80, key="sync_participants")

    open_for_batch = visible[visible["status"].str.lower() != "ausgewertet"] if not visible.empty else visible
    if not open_for_batch.empty and st.button("Alle offenen Dateien auswerten", type="primary"):
        all_tasks = []
        all_contacts = []
        summaries = []
        fingerprints = []
        file_fingerprints = []
        duplicate_titles = []
        for _, source_row in open_for_batch.iterrows():
            source_title = str(source_row.get("filename", "Synchronisierte Datei"))
            source_path = str(source_row.get("source_path", ""))
            source_file_fingerprint = str(source_row.get("fingerprint", ""))
            source_content = load_synced_file_content(source_file_fingerprint)
            if not source_content.strip():
                continue
            fingerprint = import_fingerprint("Sync-Protokoll", source_title, source_content, source_path + participants)
            if import_exists(fingerprint):
                duplicate_titles.append(source_title)
                update_synced_file_status(source_file_fingerprint, "ausgewertet", "Bereits früher übernommen")
                continue
            tasks_for_source = extract_task_suggestions("Sync-Protokoll", source_title, source_content, owner, funding)
            contacts_for_source = extract_contact_suggestions("Sync-Protokoll", source_title, source_content, participants=participants, existing_contacts=contacts)
            all_tasks.append(tasks_for_source)
            all_contacts.append(contacts_for_source)
            summaries.append(protocol_summary(source_title, date.today().isoformat(), participants, source_content, tasks_for_source))
            fingerprints.append((fingerprint, source_title))
            file_fingerprints.append(source_file_fingerprint)

        st.session_state["sync_fingerprints"] = fingerprints
        st.session_state["sync_file_fingerprints"] = file_fingerprints
        st.session_state["sync_duplicates"] = duplicate_titles
        st.session_state["sync_task_suggestions"] = pd.concat(all_tasks, ignore_index=True).drop_duplicates(subset=["Aufgabe"], keep="last") if all_tasks else pd.DataFrame(columns=TASK_COLUMNS)
        st.session_state["sync_contact_suggestions"] = pd.concat(all_contacts, ignore_index=True).drop_duplicates(subset=["Name"], keep="last") if all_contacts else pd.DataFrame(columns=CONTACT_COLUMNS)
        st.session_state["sync_summary"] = "\n\n---\n\n".join(summaries)

    st.dataframe(
        visible[["filename", "synced_at", "file_type", "status", "notes"]],
        width="stretch",
        hide_index=True,
    )

    if visible.empty:
        st.warning("Für diesen Filter wurden keine Dateien gefunden.")
        return

    selected_fingerprint = st.selectbox(
        "Datei auswählen",
        visible["fingerprint"].tolist(),
        format_func=lambda value: visible.loc[visible["fingerprint"] == value, "filename"].iloc[0],
    )
    selected_row = visible[visible["fingerprint"] == selected_fingerprint].iloc[0]
    selected_content = load_synced_file_content(selected_fingerprint)

    if selected_content:
        with st.expander("Textvorschau"):
            st.text_area("Inhalt", selected_content[:12000], height=260, disabled=True)
        st.download_button(
            "Text herunterladen",
            selected_content,
            file_name=f"{Path(import_title).stem}.txt",
            mime="text/plain",
        )
    else:
        st.warning("Diese Datei enthält keinen lesbaren Text. Bitte als TXT, Markdown, SRT oder VTT im Mac-Ordner speichern.")
    import_title = str(selected_row.get("filename", "Synchronisierte Datei"))
    source_path = str(selected_row.get("source_path", ""))

    if st.button("Ausgewählte Datei auswerten", type="primary"):
        fingerprint = import_fingerprint("Sync-Protokoll", import_title, selected_content, source_path + participants)
        st.session_state["sync_import_fingerprint"] = fingerprint
        st.session_state["sync_duplicate"] = import_exists(fingerprint)
        st.session_state["sync_selected_title"] = import_title
        st.session_state["sync_selected_file_fingerprint"] = selected_fingerprint
        st.session_state["sync_fingerprints"] = [(fingerprint, import_title)]
        st.session_state["sync_file_fingerprints"] = [selected_fingerprint]
        if st.session_state["sync_duplicate"]:
            st.session_state["sync_task_suggestions"] = pd.DataFrame(columns=TASK_COLUMNS)
            st.session_state["sync_contact_suggestions"] = pd.DataFrame(columns=CONTACT_COLUMNS)
            st.session_state["sync_summary"] = ""
            update_synced_file_status(selected_fingerprint, "ausgewertet", "Bereits früher übernommen")
        else:
            sync_tasks = extract_task_suggestions("Sync-Protokoll", import_title, selected_content, owner, funding)
            sync_contacts = extract_contact_suggestions("Sync-Protokoll", import_title, selected_content, participants=participants, existing_contacts=contacts)
            st.session_state["sync_task_suggestions"] = sync_tasks
            st.session_state["sync_contact_suggestions"] = sync_contacts
            st.session_state["sync_summary"] = protocol_summary(import_title, date.today().isoformat(), participants, selected_content, sync_tasks)

    if st.session_state.get("sync_duplicate"):
        st.warning("Diese Datei wurde bereits ausgewertet. Es wurden keine neuen Vorschläge erzeugt.")
    if st.session_state.get("sync_duplicates"):
        st.warning("Bereits importiert: " + ", ".join(st.session_state["sync_duplicates"]))

    summary = st.session_state.get("sync_summary", "")
    if summary:
        st.text_area("Zusammenfassung", summary, height=240)
        st.download_button(
            "Zusammenfassung als Markdown herunterladen",
            summary,
            file_name=f"sync-auswertung-{date.today().isoformat()}.md",
            mime="text/markdown",
        )

    sync_tasks = st.session_state.get("sync_task_suggestions", pd.DataFrame(columns=TASK_COLUMNS))
    sync_contacts = st.session_state.get("sync_contact_suggestions", pd.DataFrame(columns=CONTACT_COLUMNS))

    if not sync_tasks.empty:
        edited_sync_tasks = data_editor("Aufgabenvorschläge aus synchronisierter Datei", sync_tasks, TASK_COLUMNS, "sync_tasks_editor", {"Priorität": PRIORITIES, "Status": TASK_STATUS})
        if st.button("Aufgaben aus synchronisierter Datei speichern"):
            save_table(TASKS_FILE, append_unique(tasks, edited_sync_tasks, "Aufgabe"), TASK_COLUMNS)
            for fingerprint, source_title in st.session_state.get("sync_fingerprints", []):
                record_import(fingerprint, "Sync-Protokoll", source_title, "Aufgaben gespeichert")
            for file_fingerprint in st.session_state.get("sync_file_fingerprints", []):
                update_synced_file_status(file_fingerprint, "ausgewertet", "Aufgaben gespeichert")
            st.success("Aufgaben aus synchronisierter Datei gespeichert.")
            st.rerun()
    else:
        st.info("Noch keine Aufgabenvorschläge. Datei auswählen und auswerten.")

    if not sync_contacts.empty:
        edited_sync_contacts = data_editor("Kontaktvorschläge aus synchronisierter Datei", sync_contacts, CONTACT_COLUMNS, "sync_contacts_editor", {"Relevanz": RELEVANCE})
        if st.button("Kontakte aus synchronisierter Datei speichern"):
            save_table(CONTACTS_FILE, append_unique(contacts, edited_sync_contacts, "Name"), CONTACT_COLUMNS)
            for fingerprint, source_title in st.session_state.get("sync_fingerprints", []):
                record_import(fingerprint, "Sync-Protokoll", source_title, "Kontakte gespeichert")
            for file_fingerprint in st.session_state.get("sync_file_fingerprints", []):
                update_synced_file_status(file_fingerprint, "ausgewertet", "Kontakte gespeichert")
            st.success("Kontakte aus synchronisierter Datei gespeichert.")
            st.rerun()


def main() -> None:
    if st is None:
        raise RuntimeError("Streamlit ist nicht installiert. Bitte zuerst `pip install -r requirements.txt` ausführen.")

    st.set_page_config(page_title="Riegersburg Sanierungsprogramm", layout="wide")

    settings = load_settings()
    if not check_login(settings):
        return

    show_header(settings)

    with st.sidebar:
        sidebar_admin(settings)

    page = st.radio(
        "Bereich",
        ["Überblick", "Förderstellen", "Aufgaben", "Kontakte", "Import / Uploads", "Dokumente & Auswertung", "Bericht"],
        index=0,
        horizontal=True,
    )

    funding = load_table(FUNDING_FILE, FUNDING_COLUMNS)
    tasks = load_table(TASKS_FILE, TASK_COLUMNS)
    contacts = (
        load_table(CONTACTS_FILE, CONTACT_COLUMNS)
        if page in {"Kontakte", "Import / Uploads", "Dokumente & Auswertung", "Bericht"}
        else pd.DataFrame(columns=CONTACT_COLUMNS)
    )

    tasks = real_tasks(tasks)
    task_status = normalized_column(tasks, "Status")
    task_priority = normalized_column(tasks, "Priorität")
    open_tasks = open_task_rows(tasks)
    high_priority = tasks[(task_priority == "hoch") & (task_status != "erledigt")] if not tasks.empty else tasks
    active_funding = funding[~funding["Status"].str.lower().isin(["bewilligt", "abgelehnt"])] if not funding.empty else funding
    acute_tasks = acute_task_rows(tasks)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Förderlinien", len(funding))
    metric_cols[1].metric("aktive Linien", len(active_funding))
    metric_cols[2].metric("offene Aufgaben", len(open_tasks))
    metric_cols[3].metric("hohe Priorität", len(high_priority))

    if page == "Überblick":
        if not acute_tasks.empty:
            st.subheader("Akut zu erledigen")
            done_now = []
            for index, row in acute_tasks.iterrows():
                cols = st.columns([0.15, 2.85])
                task_name = str(row.get("Aufgabe", ""))
                if cols[0].checkbox("Erledigt", key=f"acute_done_{index}", label_visibility="collapsed"):
                    done_now.append(task_name)
                summary_bits = [
                    str(row.get("Verantwortlich", "")).strip(),
                    str(row.get("Frist", "")).strip(),
                    str(row.get("Bezug zu Förderstelle", "")).strip(),
                ]
                summary = " | ".join(bit for bit in summary_bits if bit)
                expander_title = f"{task_name}    {summary}" if summary else task_name
                with cols[1].expander(expander_title):
                    show_task_detail(row)
                    if st.button("Diese Aufgabe als erledigt markieren", key=f"acute_done_button_{task_key(row)}"):
                        save_table(TASKS_FILE, mark_tasks_done(tasks, [task_name]), TASK_COLUMNS)
                        st.success("Aufgabe wurde erledigt gesetzt.")
                        st.rerun()
            if done_now and st.button("Markierte akute Aufgaben als erledigt speichern", type="primary"):
                save_table(TASKS_FILE, mark_tasks_done(tasks, done_now), TASK_COLUMNS)
                st.success("Markierte Aufgaben wurden erledigt gesetzt.")
                st.rerun()

        if acute_tasks.empty:
            st.info("Keine akuten Aufgaben. Weitere offene Aufgaben finden Sie im Bereich Aufgaben.")

    elif page == "Förderstellen":
        edited = data_editor(
            "Förderstellen verwalten",
            funding,
            FUNDING_COLUMNS,
            "funding_editor",
            {"Ebene": LEVELS, "Status": FUNDING_STATUS},
        )
        if st.button("Förderstellen speichern", type="primary"):
            save_table(FUNDING_FILE, edited, FUNDING_COLUMNS)
            st.success("Förderstellen gespeichert.")
            st.rerun()

    elif page == "Aufgaben":
        st.subheader("Aufgaben und nächste Schritte verwalten")
        show_completed_tasks = st.checkbox("Erledigte Aufgaben anzeigen", value=False)
        if show_completed_tasks:
            visible_tasks = tasks
            st.caption("Erledigte Aufgaben sind sichtbar. Zum Löschen eine Zeile in der Spalte Löschen anhaken und anschließend Aufgaben speichern.")
        else:
            visible_tasks = open_task_rows(tasks)
            st.caption("Erledigte Aufgaben werden ausgeblendet. Zum Nachsehen den Schalter Erledigte Aufgaben anzeigen aktivieren.")
        if not visible_tasks.empty:
            selected_task = st.selectbox("Aufgabe für Details auswählen", visible_tasks["Aufgabe"].tolist(), key="task_page_detail_select")
            show_task_detail(visible_tasks[visible_tasks["Aufgabe"] == selected_task].iloc[0])
        edited = task_data_editor(visible_tasks)
        if st.button("Aufgaben speichern", type="primary"):
            final_tasks = edited if show_completed_tasks else pd.concat([completed_task_rows(tasks), edited], ignore_index=True)
            save_table(TASKS_FILE, final_tasks, TASK_COLUMNS)
            st.success("Aufgaben gespeichert.")
            st.rerun()

    elif page == "Kontakte":
        edited = data_editor(
            "Ansprechpartner verwalten",
            contacts,
            CONTACT_COLUMNS,
            "contacts_editor",
            {"Relevanz": RELEVANCE},
        )
        if st.button("Kontakte speichern", type="primary"):
            save_table(CONTACTS_FILE, edited, CONTACT_COLUMNS)
            st.success("Kontakte gespeichert.")
            st.rerun()

    elif page == "Import / Uploads":
        import_panel(funding, tasks, contacts)

    elif page == "Dokumente & Auswertung":
        sync_files_panel(funding, tasks, contacts)

    elif page == "Bericht":
        st.subheader("Kompakter Projektbericht")
        if not st.session_state.get("report_loaded"):
            if st.button("Projektbericht erstellen", type="primary"):
                st.session_state["report_loaded"] = True
                st.rerun()
            st.info("Der Bericht wird erst erstellt, wenn du ihn brauchst.")
            return
        report = markdown_report(funding, tasks, contacts)
        action_cols = st.columns([1, 1, 2])
        action_cols[0].download_button(
            "Markdown herunterladen",
            report,
            file_name=f"riegersburg-projektbericht-{date.today().isoformat()}.md",
            mime="text/markdown",
            width="stretch",
        )
        if PDF_AVAILABLE:
            action_cols[1].download_button(
                "PDF herunterladen",
                pdf_report(report),
                file_name=f"riegersburg-projektbericht-{date.today().isoformat()}.pdf",
                mime="application/pdf",
                width="stretch",
            )
        else:
            st.warning("PDF-Export ist erst nach Installation von `reportlab` verfügbar.")
        st.markdown(report)
        with st.expander("Markdown-Text anzeigen"):
            st.text_area("Markdown", report, height=320)


if __name__ == "__main__":
    main()
