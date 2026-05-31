import json

from .models import PaperMetadata
from .utils import boolify, clean_json_text, stringify


def metadata_from_dict(data: dict) -> PaperMetadata:
    if not isinstance(data, dict):
        data = {}
    return PaperMetadata(
        is_paper=boolify(data.get("is_paper", True)),
        title=stringify(data.get("title")),
        title_zh=stringify(data.get("title_zh") or data.get("chinese_title")),
        authors=stringify(data.get("authors")),
        authors_zh=stringify(data.get("authors_zh") or data.get("chinese_authors")),
        first_author=stringify(data.get("first_author")),
        corresponding_author=stringify(
            data.get("corresponding_author")
            or data.get("corresponding_authors")
            or data.get("contact_author")
        ),
        last_author=stringify(data.get("last_author")),
        corresponding_author_affiliation=stringify(
            data.get("corresponding_author_affiliation")
            or data.get("corresponding_affiliation")
            or data.get("last_author_affiliation")
        ),
        corresponding_author_affiliation_zh=stringify(
            data.get("corresponding_author_affiliation_zh")
            or data.get("corresponding_affiliation_zh")
            or data.get("last_author_affiliation_zh")
        ),
        publisher=stringify(data.get("publisher")),
        publisher_zh=stringify(data.get("publisher_zh") or data.get("chinese_publisher")),
        published_time=stringify(
            data.get("published_time") or data.get("publication_time")
        ),
        abstract_zh=stringify(data.get("abstract_zh") or data.get("chinese_abstract")),
        abstract_en=stringify(data.get("abstract_en") or data.get("english_abstract")),
        plain_language_summary=stringify(
            data.get("plain_language_summary")
            or data.get("plain_summary")
            or data.get("summary_for_layperson")
        ),
    )


def parse_metadata_json(text: str) -> PaperMetadata:
    return metadata_from_dict(json.loads(clean_json_text(text)))
