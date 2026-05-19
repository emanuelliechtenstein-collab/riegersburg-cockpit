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
    return create_engine(database_url(), pool_pre_ping=True)


def load_table(path: Path, columns: list[str]) -> pd.DataFrame:
    ensure_database()
    table_name, table_columns = table_spec(path, columns)
    engine = database_engine()
    with engine.connect() as connection:
        table = pd.read_sql_query(text(f'SELECT * FROM "{table_name}"'), connection, dtype=str).fillna("")
    columns = table_columns
    for column in columns:
        if column not in table.columns:
            table[column] = ""
    return table[columns]


def load_settings() -> dict[str, str]:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_settings(settings: dict[str, str]) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def save_table(path: Path, table: pd.DataFrame, columns: list[str]) -> None:
    ensure_database()
    backup_database()
    output = table[columns].copy()
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


def ensure_database() -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    settings = load_settings()
    engine = database_engine()
    inspector = inspect(engine)

    with engine.begin() as connection:
        for table_name, columns in [spec for spec in TABLES.values()]:
            if not inspector.has_table(table_name):
                pd.DataFrame(columns=columns).to_sql(table_name, connection, if_exists="replace", index=False)
        if not inspector.has_table("importe"):
            pd.DataFrame(columns=IMPORT_COLUMNS).to_sql("importe", connection, if_exists="replace", index=False)

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
        return pd.read_sql_query(text('SELECT * FROM "importe" ORDER BY imported_at DESC'), connection, dtype=str).fillna("")


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
    status = str(row.get(status_column, "")).lower()
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


def clean_import_line(line: str) -> str:
    line = re.sub(r"^\s*[-*•\d.)\]]+\s*", "", line.strip())
    return re.sub(r"\s+", " ", line).strip()


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
    for raw_line in body.splitlines():
        line = clean_import_line(raw_line)
        if len(line) < 12:
            continue
        lowered = line.lower()
        bullet_like = bool(re.match(r"^\s*[-*•\d.)\]]+", raw_line))
        if not bullet_like and not any(word in lowered for word in action_words):
            continue
        task_text = line[:220]
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


def extract_contact_suggestions(source_type: str, title: str, body: str, sender_name: str = "", sender_email: str = "", participants: str = "") -> pd.DataFrame:
    rows = []
    seen = set()

    def add_contact(name: str, email: str = "", note: str = "") -> None:
        clean_name = clean_import_line(name).strip(" ,;")
        if not clean_name and email:
            clean_name = email.split("@")[0]
        if len(clean_name) < 3:
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

    for email in sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", body))):
        add_contact("", email, f"Im Text gefunden: {title}")

    person_pattern = r"\b(?:Dr\.|Mag\.|DI|Dipl\.-Ing\.|LH|BM)\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]+)?"
    for name in sorted(set(re.findall(person_pattern, body + "\n" + participants))):
        add_contact(name, "", f"Im Text/Teilnehmerkreis gefunden: {title}")

    for participant in re.split(r"[,;\n]", participants):
        add_contact(participant, "", f"Teilnehmer aus Protokoll: {title}")

    return pd.DataFrame(rows, columns=CONTACT_COLUMNS)


def read_uploaded_text(uploaded_file) -> str:
    content = uploaded_file.getvalue()
    for encoding in ["utf-8", "utf-8-sig", "latin-1"]:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def parse_raw_email(raw_text: str, uploaded_file=None) -> dict[str, str]:
    if uploaded_file is not None and uploaded_file.name.lower().endswith(".eml"):
        message = BytesParser(policy=policy.default).parsebytes(uploaded_file.getvalue())
    else:
        message = Parser(policy=policy.default).parsestr(raw_text)

    subject = str(message.get("subject", "") or "")
    sender_name, sender_email = parseaddr(str(message.get("from", "") or ""))
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
                body = part.get_content()
                break
    else:
        try:
            body = message.get_content()
        except Exception:
            body = raw_text

    if not subject and not sender_email and not body.strip():
        body = raw_text

    return {
        "subject": subject,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "date": mail_date,
        "body": str(body or raw_text),
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
    urgent_tasks = add_urgency(tasks).head(8)
    open_tasks = tasks[tasks["Status"].str.lower() != "erledigt"] if not tasks.empty else tasks
    active_funding = funding[~funding["Status"].str.lower().isin(["bewilligt", "abgelehnt"])] if not funding.empty else funding

    lines = [
        "# Riegersburg Sanierungsprogramm - Projektbericht",
        "",
        f"Erstellt am: {date.today().strftime('%d.%m.%Y')}",
        "",
        "## Kurzstatus",
        "",
        f"- Förderlinien gesamt: {len(funding)}",
        f"- aktive Förderlinien: {len(active_funding)}",
        f"- Aufgaben gesamt: {len(tasks)}",
        f"- offene Aufgaben: {len(open_tasks)}",
        f"- Kontakte gesamt: {len(contacts)}",
        "",
        "## Status je Förderlinie",
        "",
    ]

    if funding.empty:
        lines.append("Noch keine Förderstellen erfasst.")
    else:
        for _, row in funding.iterrows():
            lines.extend(
                [
                    f"### {row['Name']}",
                    f"- Ebene: {row['Ebene']}",
                    f"- Zuständige Stelle: {row['zuständige Stelle']}",
                    f"- Ansprechpartner: {row['Ansprechpartner']}",
                    f"- Förderpotenzial: {row['geschätztes Förderpotenzial']}",
                    f"- Status: {row['Status']}",
                    f"- Nächste Aktion: {row['nächste Aktion']}",
                    f"- Frist: {row['Frist']}",
                    f"- Notizen: {row['Notizen']}",
                    "",
                ]
            )

    lines.extend(["## Dringende Aufgaben", ""])
    if urgent_tasks.empty:
        lines.append("Keine Aufgaben erfasst.")
    else:
        for _, row in urgent_tasks.iterrows():
            lines.append(
                f"- **{row['Einordnung']}**: {row['Aufgabe']} | verantwortlich: {row['Verantwortlich']} | "
                f"Frist: {row['Frist']} | Bezug: {row['Bezug zu Förderstelle']}"
            )

    lines.extend(["", "## Wichtige Kontakte", ""])
    relevant_contacts = contacts[contacts["Relevanz"].str.lower() == "hoch"] if not contacts.empty else contacts
    if relevant_contacts.empty:
        lines.append("Keine hoch relevanten Kontakte erfasst.")
    else:
        for _, row in relevant_contacts.iterrows():
            lines.append(
                f"- {row['Name']} ({row['Organisation']}) | {row['Funktion']} | nächste Aktion: {row['nächste Aktion']}"
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


def check_login(settings: dict[str, str]) -> bool:
    configured_password = secret_value("app_password") or settings.get("app_password", DEFAULT_PASSWORD)
    if st.session_state.get("authenticated"):
        return True

    st.title("Riegersburg Sanierungsprogramm")
    st.caption("Bitte anmelden, um das gemeinsame Cockpit zu öffnen.")
    password = st.text_input("Passwort", type="password")
    if st.button("Anmelden", type="primary"):
        if hmac.compare_digest(password, configured_password):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Das Passwort stimmt nicht.")
    if "app_password" not in settings:
        st.info("Das voreingestellte Passwort lautet: Riegersburg2030")
    return False


def sidebar_admin(settings: dict[str, str]) -> None:
    st.header("Aktionen")
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
                st.session_state["mail_contact_suggestions"] = extract_contact_suggestions("E-Mail", final_subject, mail_body, final_sender_name, final_sender_email)
            st.session_state["mail_detected"] = {
                "Betreff": final_subject,
                "Absender": final_sender_name,
                "E-Mail": final_sender_email,
                "Datum": parsed_mail["date"] or mail_date.isoformat(),
            }

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
                contacts_for_source = extract_contact_suggestions("Protokoll", source_title, source_text, participants=participants)
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
        imports = load_imports()
        if imports.empty:
            st.info("Noch keine Importe gespeichert.")
        else:
            st.dataframe(imports[["source_type", "title", "imported_at", "notes"]], width="stretch", hide_index=True)


def main() -> None:
    if st is None:
        raise RuntimeError("Streamlit ist nicht installiert. Bitte zuerst `pip install -r requirements.txt` ausführen.")

    st.set_page_config(page_title="Riegersburg Sanierungsprogramm", layout="wide")

    settings = load_settings()
    if not check_login(settings):
        return

    funding = load_table(FUNDING_FILE, FUNDING_COLUMNS)
    tasks = load_table(TASKS_FILE, TASK_COLUMNS)
    contacts = load_table(CONTACTS_FILE, CONTACT_COLUMNS)

    show_header(settings)

    with st.sidebar:
        sidebar_admin(settings)

    open_tasks = tasks[tasks["Status"].str.lower() != "erledigt"] if not tasks.empty else tasks
    high_priority = tasks[tasks["Priorität"].str.lower() == "hoch"] if not tasks.empty else tasks
    active_funding = funding[~funding["Status"].str.lower().isin(["bewilligt", "abgelehnt"])] if not funding.empty else funding

    metric_cols = st.columns(4)
    metric_cols[0].metric("Förderlinien", len(funding))
    metric_cols[1].metric("aktive Linien", len(active_funding))
    metric_cols[2].metric("offene Aufgaben", len(open_tasks))
    metric_cols[3].metric("hohe Priorität", len(high_priority))

    tabs = st.tabs(["Überblick", "Förderstellen", "Aufgaben", "Kontakte", "Import", "Dokumente", "Bericht"])

    with tabs[0]:
        left, right = st.columns([1.1, 1])
        with left:
            st.subheader("Status je Förderlinie")
            if funding.empty:
                st.info("Noch keine Förderstellen erfasst.")
            else:
                st.dataframe(
                    funding[["Name", "Ebene", "Status", "geschätztes Förderpotenzial", "nächste Aktion", "Frist"]],
                    width="stretch",
                    hide_index=True,
                )
        with right:
            st.subheader("Priorisierte nächste Schritte")
            urgent = add_urgency(tasks)
            if urgent.empty:
                st.info("Noch keine Aufgaben erfasst.")
            else:
                st.dataframe(
                    urgent[["Einordnung", "Aufgabe", "Verantwortlich", "Priorität", "Status", "Frist", "Bezug zu Förderstelle"]],
                    width="stretch",
                    hide_index=True,
                )

    with tabs[1]:
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

    with tabs[2]:
        edited = data_editor(
            "Aufgaben und nächste Schritte verwalten",
            tasks,
            TASK_COLUMNS,
            "tasks_editor",
            {"Priorität": PRIORITIES, "Status": TASK_STATUS},
        )
        if st.button("Aufgaben speichern", type="primary"):
            save_table(TASKS_FILE, edited, TASK_COLUMNS)
            st.success("Aufgaben gespeichert.")

    with tabs[3]:
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

    with tabs[4]:
        import_panel(funding, tasks, contacts)

    with tabs[5]:
        st.subheader("Dokumentenordner")
        st.caption("Hier kann die App direkt auf einen lokalen Ordner zugreifen, solange sie auf diesem Mac läuft.")

        document_dir = st.text_input(
            "Ordnerpfad",
            value=settings.get("document_dir", str(DEFAULT_DOCUMENT_DIR)),
            help="Zum Beispiel: /Users/rauby/Desktop/Riegersburg_Cockpit",
        )
        col_a, col_b = st.columns([1, 2])
        with col_a:
            if st.button("Ordnerpfad speichern", type="primary", width="stretch"):
                settings["document_dir"] = document_dir
                save_settings(settings)
                st.success("Ordnerpfad gespeichert.")

        folder = Path(document_dir).expanduser()
        documents = scan_documents(folder)

        if not folder.exists():
            st.warning("Der angegebene Ordner wurde nicht gefunden.")
        elif documents.empty:
            st.info("In diesem Ordner wurden keine Word-, PDF-, Markdown- oder CSV-Dateien gefunden.")
        else:
            st.metric("Gefundene Dokumente", len(documents))
            search = st.text_input("Dateien filtern", placeholder="z. B. BDA, Wiesenhofer, Förderantrag")
            visible_documents = documents
            if search:
                mask = documents.apply(lambda row: search.lower() in " ".join(row.astype(str)).lower(), axis=1)
                visible_documents = documents[mask]

            st.dataframe(
                visible_documents[["Datei", "Typ", "Ordner", "Größe KB"]],
                width="stretch",
                hide_index=True,
            )

            if not visible_documents.empty:
                selected_file = st.selectbox("Datei auswählen", visible_documents["Pfad"].tolist(), format_func=lambda value: Path(value).name)
                selected_path = Path(selected_file)
                st.code(str(selected_path), language=None)
                st.download_button(
                    "Ausgewählte Datei herunterladen",
                    selected_path.read_bytes(),
                    file_name=selected_path.name,
                    mime="application/octet-stream",
                )

    with tabs[6]:
        st.subheader("Kompakter Projektbericht")
        report = markdown_report(funding, tasks, contacts)
        st.text_area("Vorschau", report, height=480)
        st.download_button(
            "Markdown herunterladen",
            report,
            file_name=f"riegersburg-projektbericht-{date.today().isoformat()}.md",
            mime="text/markdown",
        )
        if PDF_AVAILABLE:
            st.download_button(
                "PDF herunterladen",
                pdf_report(report),
                file_name=f"riegersburg-projektbericht-{date.today().isoformat()}.pdf",
                mime="application/pdf",
            )
        else:
            st.warning("PDF-Export ist erst nach Installation von `reportlab` verfügbar.")


if __name__ == "__main__":
    main()
