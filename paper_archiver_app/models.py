from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PaperMetadata:
    is_paper: bool = True
    title: str = ""
    title_zh: str = ""
    authors: str = ""
    authors_zh: str = ""
    first_author: str = ""
    corresponding_author: str = ""
    last_author: str = ""
    corresponding_author_affiliation: str = ""
    corresponding_author_affiliation_zh: str = ""
    publisher: str = ""
    publisher_zh: str = ""
    published_time: str = ""
    abstract_zh: str = ""
    abstract_en: str = ""
    plain_language_summary: str = ""

@dataclass
class ApiConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    auth_header: str = "Authorization"

@dataclass
class PaperItem:
    pdf_path: Path
    metadata: PaperMetadata = field(default_factory=PaperMetadata)
    prompt: str = ""
    folder: Path | None = None
    duplicate: bool = False
    json_payload: dict = field(default_factory=dict)
    note: str = ""
    changed_fields: set[str] = field(default_factory=set)
