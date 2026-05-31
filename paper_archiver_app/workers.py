from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from .api_client import build_prompt, call_openai_compatible_api, extract_pdf_text, validate_openai_compatible_api
from .archive_store import archive_paper, find_duplicate, pdf_hash
from .external_lookup import google_scholar_author_url, query_journal_partitions
from .metadata import metadata_from_dict
from .models import ApiConfig, PaperItem, PaperMetadata
from .utils import stringify


class ParseWorker(QThread):
    itemFinished = pyqtSignal(int, int, object)
    failed = pyqtSignal(int, int, str, str)
    finished = pyqtSignal()

    def __init__(self, pdf_paths: list[Path], config: ApiConfig, archive_root: Path):
        super().__init__()
        self.pdf_paths = pdf_paths
        self.config = config
        self.archive_root = archive_root

    def run(self) -> None:
        total = len(self.pdf_paths)
        for index, pdf_path in enumerate(self.pdf_paths, start=1):
            try:
                old_folder, old_payload = find_duplicate(
                    self.archive_root, paper_hash=pdf_hash(pdf_path), pdf_path=pdf_path
                )
                text = extract_pdf_text(pdf_path)
                prompt = build_prompt(text)
                metadata = call_openai_compatible_api(prompt, self.config)
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

