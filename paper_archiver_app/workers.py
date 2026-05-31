from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from .api_client import build_prompt, call_openai_compatible_api, extract_document_text, validate_openai_compatible_api
from .archive_store import archive_paper, find_duplicate, metadata_is_complete, pdf_hash, write_metadata_file
from .external_lookup import google_scholar_author_url, query_journal_partitions
from .metadata import metadata_from_dict
from .models import ApiConfig, PaperItem, PaperMetadata
from .utils import stringify


class ParseWorker(QThread):
    itemFinished = pyqtSignal(int, int, object)
    failed = pyqtSignal(int, int, str, str)
    finished = pyqtSignal()

    def __init__(
        self,
        pdf_paths: list[Path],
        config: ApiConfig,
        archive_root: Path,
        initial_tags_by_path: dict[str, list[str]] | None = None,
    ):
        super().__init__()
        self.pdf_paths = pdf_paths
        self.config = config
        self.archive_root = archive_root
        self.initial_tags_by_path = initial_tags_by_path or {}

    def run(self) -> None:
        total = len(self.pdf_paths)
        for index, pdf_path in enumerate(self.pdf_paths, start=1):
            try:
                initial_tags = self.initial_tags_by_path.get(str(pdf_path), [])
                old_folder, old_payload = find_duplicate(
                    self.archive_root, paper_hash=pdf_hash(pdf_path), pdf_path=pdf_path
                )
                if old_folder and old_payload:
                    old_metadata = metadata_from_dict(old_payload)
                    if initial_tags:
                        merged_tags = unique_tags(old_metadata.tags + initial_tags)
                        if merged_tags != old_metadata.tags:
                            old_payload["tags"] = merged_tags
                            write_metadata_file(old_folder, old_payload)
                            old_metadata = metadata_from_dict(old_payload)
                    if metadata_is_complete(old_metadata):
                        item = PaperItem(
                            pdf_path=pdf_path,
                            metadata=old_metadata,
                            prompt=stringify(old_payload.get("model_prompt")),
                            folder=old_folder,
                            duplicate=True,
                            json_payload=old_payload,
                            note=stringify(old_payload.get("manual_notes")),
                            skipped_model=True,
                        )
                        self.itemFinished.emit(index, total, item)
                        continue

                text = extract_document_text(pdf_path)
                prompt = build_prompt(text, pdf_path.name)
                metadata = call_openai_compatible_api(prompt, self.config, text)
                if initial_tags:
                    metadata.tags = unique_tags(metadata.tags + initial_tags)
                folder, payload, duplicate, changed_fields = archive_paper(
                    pdf_path, self.archive_root, metadata, prompt
                )
                duplicate = duplicate or bool(old_folder and old_payload)
                item = PaperItem(
                    pdf_path=pdf_path,
                    metadata=metadata_from_dict(payload),
                    prompt=stringify(payload.get("model_prompt")) or prompt,
                    folder=folder,
                    duplicate=duplicate,
                    json_payload=payload,
                    note=stringify(payload.get("manual_notes")),
                    changed_fields=changed_fields,
                )
                self.itemFinished.emit(index, total, item)
            except Exception as exc:
                self.failed.emit(index, total, str(pdf_path), str(exc))
        self.finished.emit()

def unique_tags(tags: list[str]) -> list[str]:
    merged: list[str] = []
    for tag in tags:
        clean_tag = str(tag).strip()
        if clean_tag and clean_tag not in merged:
            merged.append(clean_tag)
    return merged

class ValidateWorker(QThread):
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, config: ApiConfig):
        super().__init__()
        self.config = config

    def run(self) -> None:
        try:
            self.succeeded.emit(validate_openai_compatible_api(self.config))
        except Exception as exc:
            self.failed.emit(str(exc))

class ScholarWorker(QThread):
    succeeded = pyqtSignal(str)
    failed = pyqtSignal()

    def __init__(self, metadata: PaperMetadata):
        super().__init__()
        self.metadata = metadata

    def run(self) -> None:
        try:
            url = google_scholar_author_url(self.metadata)
        except Exception:
            url = ""
        if url:
            self.succeeded.emit(url)
        else:
            self.failed.emit()

class JournalLookupWorker(QThread):
    succeeded = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, journal_name: str):
        super().__init__()
        self.journal_name = journal_name

    def run(self) -> None:
        try:
            result, url = query_journal_partitions(self.journal_name)
            self.succeeded.emit(result, url)
        except Exception as exc:
            self.failed.emit(str(exc))

