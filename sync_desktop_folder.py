from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from sqlalchemy import create_engine, inspect, text

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "sync_config.json"
EXAMPLE_CONFIG_FILE = BASE_DIR / "sync_config.example.json"
SYNC_COLUMNS = ["fingerprint", "filename", "source_path", "synced_at", "file_type", "content", "status", "notes"]
DEFAULT_EXTENSIONS = [".txt", ".md", ".srt", ".vtt", ".docx", ".pdf"]
DEFAULT_FOLDER = "/Users/rauby/Desktop/Fortbestand der Riegerbsurg "


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print("Die lokale Sync-Einstellung fehlt noch.")
        print("Bitte den Supabase database_url Link einfügen. Er wird nur lokal in sync_config.json gespeichert.")
        database_url = input("database_url: ").strip()
        folder_path = input(f"Ordnerpfad [{DEFAULT_FOLDER}]: ").strip() or DEFAULT_FOLDER
        config = {
            "database_url": database_url,
            "folder_path": folder_path,
            "extensions": DEFAULT_EXTENSIONS,
        }
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        print("")
        print("sync_config.json wurde angelegt.")
        return config
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def normalize_database_url(value: str) -> str:
    value = value.strip()
    if value.startswith("postgresql://"):
        value = "postgresql+psycopg2://" + value.removeprefix("postgresql://")
    if value.startswith("postgresql+psycopg2://") and "sslmode=" not in value:
        separator = "&" if "?" in value else "?"
        value = value + separator + "sslmode=require"
    return value


def ensure_sync_table(engine) -> None:
    inspector = inspect(engine)
    if inspector.has_table("sync_files"):
        return
    with engine.begin() as connection:
        column_sql = ", ".join(f'"{column}" TEXT' for column in SYNC_COLUMNS)
        connection.execute(text(f'CREATE TABLE "sync_files" ({column_sql})'))


def read_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_content = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml_content)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        text_value = "".join(parts).strip()
        if text_value:
            paragraphs.append(text_value)
    return "\n".join(paragraphs)


def read_pdf_text(path: Path) -> str:
    if PdfReader is None:
        return ""
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages).strip()


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return read_docx_text(path)
    if path.suffix.lower() == ".pdf":
        return read_pdf_text(path)

    raw = path.read_bytes()
    for encoding in ["utf-8", "utf-8-sig", "latin-1"]:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def fingerprint_for(path: Path, content: str) -> str:
    normalized = "\n".join([path.name.lower(), " ".join(content.split()).lower()])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sync_file(engine, path: Path) -> str:
    content = read_text(path)
    if not content.strip():
        return "unreadable"
    fingerprint = fingerprint_for(path, content)
    with engine.begin() as connection:
        existing = connection.execute(
            text('SELECT COUNT(*) FROM "sync_files" WHERE fingerprint = :fingerprint'),
            {"fingerprint": fingerprint},
        ).scalar()
        if existing:
            return "skipped"
        connection.execute(
            text(
                'INSERT INTO "sync_files" '
                "(fingerprint, filename, source_path, synced_at, file_type, content, status, notes) "
                "VALUES (:fingerprint, :filename, :source_path, :synced_at, :file_type, :content, :status, :notes)"
            ),
            {
                "fingerprint": fingerprint,
                "filename": path.name,
                "source_path": str(path),
                "synced_at": datetime.now().isoformat(timespec="seconds"),
                "file_type": path.suffix.lower().lstrip("."),
                "content": content,
                "status": "neu",
                "notes": "Automatisch vom Mac-Ordner synchronisiert",
            },
        )
    return "uploaded"


def main() -> None:
    config = load_config()
    database_url = normalize_database_url(config.get("database_url", ""))
    folder = Path(config.get("folder_path", "")).expanduser()
    extensions = {extension.lower() for extension in config.get("extensions", DEFAULT_EXTENSIONS)}

    if not database_url:
        raise ValueError("In sync_config.json fehlt database_url.")
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Ordner nicht gefunden: {folder}")

    engine = create_engine(database_url, pool_pre_ping=True)
    ensure_sync_table(engine)

    uploaded = 0
    skipped = 0
    unreadable = 0
    scanned = 0
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in extensions:
            continue
        scanned += 1
        result = sync_file(engine, path)
        if result == "uploaded":
            uploaded += 1
            print(f"Neu übertragen: {path.name}")
        elif result == "skipped":
            skipped += 1
        else:
            unreadable += 1
            print(f"Nicht lesbar: {path.name}")

    print("")
    print("Synchronisierung fertig.")
    print(f"Geprüfte Dateien: {scanned}")
    print(f"Neu übertragen: {uploaded}")
    print(f"Bereits vorhanden: {skipped}")
    print(f"Nicht lesbar: {unreadable}")


if __name__ == "__main__":
    main()
