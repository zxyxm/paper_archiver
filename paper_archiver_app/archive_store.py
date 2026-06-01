import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from .metadata import metadata_from_dict, tags_from_value
from .models import PaperMetadata
from .utils import boolify, compact_text, normalized_title, stringify

ARCHIVE_SCHEMA_VERSION = 2
ARCHIVED_DOCUMENT_SUFFIXES = {".pdf", ".caj"}


@dataclass
class ArchiveMaintenanceSummary:
    scanned: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    changed_fields: dict[str, int] = field(default_factory=dict)

    def record_changes(self, changed_fields: set[str]) -> None:
        if not changed_fields:
            return
        self.updated += 1
        for name in changed_fields:
            self.changed_fields[name] = self.changed_fields.get(name, 0) + 1

    @property
    def failed(self) -> int:
        return len(self.errors)


def metadata_field_names() -> list[str]:
    return [field_info.name for field_info in fields(PaperMetadata)]


def current_metadata_defaults() -> dict:
    return asdict(PaperMetadata())


def pdf_hash(pdf_path: Path) -> str:
    digest = hashlib.sha256()
    with pdf_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def read_metadata_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}

def write_metadata_file(folder: Path, payload: dict) -> None:
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with (folder / "metadata.json").open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

def archive_document_paths(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in ARCHIVED_DOCUMENT_SUFFIXES
    )

def first_existing_document(folder: Path, payload: dict) -> Path | None:
    for key in ("source_pdf", "original_pdf", "pdf_path", "file_path"):
        candidate = stringify(payload.get(key))
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists() and path.is_file():
            return path
    documents = archive_document_paths(folder)
    return documents[0] if documents else None

def normalize_metadata_payload(folder: Path, payload: dict) -> tuple[dict, set[str]]:
    original = dict(payload or {})
    normalized = dict(original)
    metadata = metadata_from_dict(original)
    metadata_values = asdict(metadata)
    defaults = current_metadata_defaults()
    changed_fields: set[str] = set()

    for name in metadata_field_names():
        value = metadata_values.get(name, defaults.get(name))
        if name == "tags":
            value = tags_from_value(value)
        if normalized.get(name) != value:
            normalized[name] = value
            changed_fields.add(name)

    for name, value in {
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "manual_notes": stringify(original.get("manual_notes") or original.get("notes")),
        "model_prompt": stringify(original.get("model_prompt") or original.get("prompt")),
    }.items():
        if normalized.get(name) != value:
            normalized[name] = value
            changed_fields.add(name)

    document_path = first_existing_document(folder, normalized)
    if document_path:
        source_pdf = stringify(normalized.get("source_pdf"))
        if not source_pdf or not Path(source_pdf).exists():
            normalized["source_pdf"] = str(document_path)
            changed_fields.add("source_pdf")
        if not stringify(normalized.get("original_pdf")):
            normalized["original_pdf"] = stringify(original.get("original_pdf")) or str(document_path)
            changed_fields.add("original_pdf")
        if not stringify(normalized.get("paper_hash")):
            try:
                normalized["paper_hash"] = pdf_hash(document_path)
                changed_fields.add("paper_hash")
            except OSError:
                pass

    if not stringify(normalized.get("created_at")):
        normalized["created_at"] = (
            stringify(original.get("updated_at"))
            or datetime.now().isoformat(timespec="seconds")
        )
        changed_fields.add("created_at")

    if changed_fields:
        normalized["schema_migrated_at"] = datetime.now().isoformat(timespec="seconds")
        changed_fields.add("schema_migrated_at")

    return normalized, changed_fields

def organize_archive_payloads(root_dir: Path) -> ArchiveMaintenanceSummary:
    summary = ArchiveMaintenanceSummary()
    if not root_dir.exists():
        return summary
    for metadata_file in root_dir.glob("*/metadata.json"):
        summary.scanned += 1
        try:
            payload = read_metadata_file(metadata_file)
            normalized, changed_fields = normalize_metadata_payload(
                metadata_file.parent, payload
            )
            if changed_fields:
                write_metadata_file(metadata_file.parent, normalized)
                summary.record_changes(changed_fields)
            else:
                summary.skipped += 1
        except (OSError, json.JSONDecodeError) as exc:
            summary.errors.append(f"{metadata_file}: {exc}")
    return summary

def iter_archive_payloads(root_dir: Path):
    if not root_dir.exists():
        return
    for metadata_file in root_dir.glob("*/metadata.json"):
        try:
            yield metadata_file.parent, read_metadata_file(metadata_file)
        except (OSError, json.JSONDecodeError):
            continue

def archived_paper_rows(root_dir: Path) -> list[tuple[Path, PaperMetadata]]:
    rows: list[tuple[Path, PaperMetadata]] = []
    for folder, payload in iter_archive_payloads(root_dir):
        metadata = metadata_from_dict(payload)
        if metadata.is_paper:
            rows.append((folder, metadata))
    rows.sort(
        key=lambda row: (
            row[1].published_time or "",
            row[1].publisher or row[1].publisher_zh or "",
            row[1].first_author or "",
            row[1].title or row[1].title_zh or "",
        ),
        reverse=True,
    )
    return rows

def metadata_is_complete(metadata: PaperMetadata) -> bool:
    if not metadata.is_paper:
        return bool(
            (metadata.title or metadata.title_zh)
            and metadata.plain_language_summary
        )
    required_groups = [
        (metadata.title, metadata.title_zh),
        (metadata.authors, metadata.authors_zh),
        (metadata.first_author,),
        (metadata.corresponding_author_affiliation, metadata.corresponding_author_affiliation_zh),
        (metadata.publisher, metadata.publisher_zh),
        (metadata.published_time,),
        (metadata.abstract_zh, metadata.abstract_en),
        (metadata.plain_language_summary,),
    ]
    return all(any(stringify(value) for value in group) for group in required_groups)

def payload_pdf_matches(payload: dict, paper_hash: str = "", pdf_path: Path | None = None) -> bool:
    if paper_hash and payload.get("paper_hash") == paper_hash:
        return True
    candidate_paths = [
        stringify(payload.get("source_pdf")),
        stringify(payload.get("original_pdf")),
        stringify(payload.get("pdf_path")),
        stringify(payload.get("file_path")),
    ]
    resolved_pdf = None
    if pdf_path:
        try:
            resolved_pdf = pdf_path.resolve()
        except OSError:
            resolved_pdf = pdf_path
    for candidate in candidate_paths:
        if not candidate:
            continue
        archived_pdf = Path(candidate)
        if resolved_pdf:
            try:
                if archived_pdf.resolve() == resolved_pdf:
                    return True
            except OSError:
                pass
        if paper_hash and archived_pdf.exists():
            try:
                if pdf_hash(archived_pdf) == paper_hash:
                    return True
            except OSError:
                pass
    return False

def find_duplicate(
    root_dir: Path,
    metadata: PaperMetadata | None = None,
    paper_hash: str = "",
    pdf_path: Path | None = None,
):
    wanted_title = normalized_title(metadata.title if metadata else "")
    wanted_title_zh = normalized_title(metadata.title_zh if metadata else "")
    if not (wanted_title or wanted_title_zh or paper_hash or pdf_path):
        return None, None
    for folder, payload in iter_archive_payloads(root_dir):
        if payload_pdf_matches(payload, paper_hash, pdf_path):
            return folder, payload
        payload_metadata = metadata_from_dict(payload)
        payload_titles = {
            normalized_title(payload_metadata.title),
            normalized_title(payload_metadata.title_zh),
        }
        if (wanted_title and wanted_title in payload_titles) or (
            wanted_title_zh and wanted_title_zh in payload_titles
        ):
            return folder, payload
    return None, None

def merge_metadata_payload(
    payload: dict,
    metadata: PaperMetadata,
    prompt: str,
    pdf_path: Path,
    paper_hash: str,
    existing_note: str = "",
) -> tuple[dict, set[str]]:
    merged = dict(payload or {})
    old_metadata = metadata_from_dict(merged)
    new_values = asdict(metadata)
    old_values = asdict(old_metadata)
    changed_fields: set[str] = set()
    for key, new_value in new_values.items():
        old_value = old_values.get(key)
        if key == "tags":
            merged_tags: list[str] = []
            for tag in (old_value or []) + (new_value or []):
                if tag and tag not in merged_tags:
                    merged_tags.append(tag)
            if new_value:
                merged[key] = merged_tags
                if merged_tags != old_value:
                    changed_fields.add(key)
            else:
                merged[key] = old_value or payload.get(key, [])
            continue
        if isinstance(new_value, bool):
            if new_value != old_value:
                merged[key] = new_value
                changed_fields.add(key)
            continue
        new_text = stringify(new_value)
        old_text = stringify(old_value)
        if new_text and new_text != old_text:
            merged[key] = new_text
            changed_fields.add(key)
        else:
            merged.setdefault(key, old_text)
    merged["paper_hash"] = paper_hash
    merged["original_pdf"] = str(pdf_path)
    merged["model_prompt"] = prompt
    if existing_note:
        merged["manual_notes"] = existing_note
    else:
        merged.setdefault("manual_notes", stringify(payload.get("manual_notes")))
    return merged, changed_fields

def archive_paper(
    pdf_path: Path,
    root_dir: Path,
    metadata: PaperMetadata,
    prompt: str = "",
    existing_note: str = "",
) -> tuple[Path, dict, bool, set[str]]:
    root_dir.mkdir(parents=True, exist_ok=True)
    paper_hash = pdf_hash(pdf_path)
    duplicate_folder, duplicate_payload = find_duplicate(
        root_dir, metadata, paper_hash, pdf_path
    )
    if duplicate_folder and duplicate_payload:
        payload, changed_fields = merge_metadata_payload(
            duplicate_payload, metadata, prompt, pdf_path, paper_hash, existing_note
        )
        if changed_fields or payload != duplicate_payload:
            write_metadata_file(duplicate_folder, payload)
        return duplicate_folder, payload, True, changed_fields

    title = compact_text(metadata.title or metadata.title_zh, pdf_path.stem, 72)
    first_author_source = metadata.first_author or metadata.authors.split(",")[0]
    first_author = compact_text(first_author_source, "unknown-author", 32)
    corresponding_author_source = metadata.corresponding_author or metadata.last_author
    corresponding_author = compact_text(
        corresponding_author_source, "unknown-corresponding-author", 32
    )
    published_time = metadata.published_time
    if not published_time:
        year_match = re.search(r"(19|20)\d{2}", metadata.published_time)
        published_time = year_match.group(0) if year_match else "unknown-time"
    folder_name = "=".join(
        compact_text(part, fallback, max_len)
        for part, fallback, max_len in (
            (published_time, "unknown-time", 24),
            (first_author, "unknown-author", 32),
            (corresponding_author, "unknown-corresponding-author", 32),
            (title, pdf_path.stem, 72),
        )
    )
    folder_name = compact_text(folder_name, pdf_path.stem, 156)
    folder = root_dir / folder_name
    for index in range(2, 1000):
        if not folder.exists():
            break
        folder = root_dir / f"{folder_name} ({index})"
    folder.mkdir(parents=True, exist_ok=False)

    pdf_name = compact_text(pdf_path.stem, "paper", 80) + pdf_path.suffix.lower()
    target_pdf = folder / pdf_name
    if pdf_path.resolve() != target_pdf.resolve():
        shutil.copy2(pdf_path, target_pdf)

    payload = asdict(metadata)
    payload.update(
        {
            "paper_hash": paper_hash,
            "source_pdf": str(target_pdf),
            "original_pdf": str(pdf_path),
            "model_prompt": prompt,
            "manual_notes": existing_note,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    write_metadata_file(folder, payload)
    return folder, payload, False, set()

def archive_statistics(root_dir: Path) -> dict:
    folders = 0
    pdfs = 0
    jsons = 0
    notes = 0
    papers = 0
    non_papers = 0
    for folder, payload in iter_archive_payloads(root_dir):
        folders += 1
        jsons += 1
        pdfs += len(archive_document_paths(folder))
        if stringify(payload.get("manual_notes")):
            notes += 1
        if boolify(payload.get("is_paper", True)):
            papers += 1
        else:
            non_papers += 1
    return {
        "folders": folders,
        "pdfs": pdfs,
        "jsons": jsons,
        "papers": papers,
        "non_papers": non_papers,
        "notes": notes,
    }

def rewrite_archive_tags(root_dir: Path, old_tag: str = "", new_tag: str = "") -> int:
    changed_count = 0
    for folder, _metadata in archived_paper_rows(root_dir):
        payload = read_metadata_file(folder / "metadata.json")
        old_tags = tags_from_value(payload.get("tags", []))
        rewritten_tags: list[str] = []
        for clean_tag in old_tags:
            replacement = new_tag if old_tag and clean_tag == old_tag else clean_tag
            if replacement and replacement not in rewritten_tags:
                rewritten_tags.append(replacement)
        if rewritten_tags == old_tags:
            continue
        payload["tags"] = rewritten_tags
        write_metadata_file(folder, payload)
        changed_count += 1
    return changed_count

