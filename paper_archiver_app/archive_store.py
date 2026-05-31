import hashlib
import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .metadata import metadata_from_dict
from .models import PaperMetadata
from .utils import boolify, compact_text, normalized_title, stringify


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
        pdfs += len(list(folder.glob("*.pdf")))
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

