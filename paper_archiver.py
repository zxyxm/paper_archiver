import hashlib
import html
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests
from PyQt5.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - shown in UI at runtime
    fitz = None


APP_DIR = Path(__file__).resolve().parent
DEFAULT_ARCHIVE_ROOT = APP_DIR / "archive"
CONFIG_FILE = APP_DIR / "config.json"

PROVIDER_PRESETS = {
    "deepseek": {
        "name": "DeepSeek API",
        "base_url": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "auth_header": "Authorization",
    },
    "xiaomi": {
        "name": "小米 MiMo Reasoning",
        "base_url": os.environ.get(
            "XIAOMI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
        ),
        "model": os.environ.get("XIAOMI_MODEL", "mimo-v2.5-pro"),
        "api_key_env": "XIAOMI_API_KEY",
        "model_env": "XIAOMI_MODEL",
        "base_url_env": "XIAOMI_BASE_URL",
        "auth_header": os.environ.get("XIAOMI_AUTH_HEADER", "api-key"),
    },
    "custom": {
        "name": "自定义 OpenAI 兼容",
        "base_url": os.environ.get("OPENAI_COMPAT_BASE_URL", ""),
        "model": os.environ.get("OPENAI_COMPAT_MODEL", ""),
        "api_key_env": "OPENAI_COMPAT_API_KEY",
        "model_env": "OPENAI_COMPAT_MODEL",
        "base_url_env": "OPENAI_COMPAT_BASE_URL",
        "auth_header": os.environ.get("OPENAI_COMPAT_AUTH_HEADER", "Authorization"),
    },
}


@dataclass
class PaperMetadata:
    is_paper: bool = True
    title: str = ""
    title_zh: str = ""
    authors: str = ""
    authors_zh: str = ""
    first_author: str = ""
    last_author: str = ""
    corresponding_author_affiliation: str = ""
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


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(data: dict) -> None:
    with CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def clean_json_text(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if match:
        text = match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text


def stringify(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def boolify(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text not in {"false", "0", "no", "不是", "非论文"}


def metadata_from_dict(data: dict) -> PaperMetadata:
    return PaperMetadata(
        is_paper=boolify(data.get("is_paper", True)),
        title=stringify(data.get("title")),
        title_zh=stringify(data.get("title_zh") or data.get("chinese_title")),
        authors=stringify(data.get("authors")),
        authors_zh=stringify(data.get("authors_zh") or data.get("chinese_authors")),
        first_author=stringify(data.get("first_author")),
        last_author=stringify(data.get("last_author")),
        corresponding_author_affiliation=stringify(
            data.get("corresponding_author_affiliation")
            or data.get("corresponding_affiliation")
            or data.get("last_author_affiliation")
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


def compact_text(value: str, fallback: str = "untitled", max_len: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\r\n\t]+', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value[:max_len].strip(" ._") or fallback)[:max_len]


def normalized_title(value: str) -> str:
    return re.sub(r"\W+", "", (value or "").lower(), flags=re.U)


def pdf_hash(pdf_path: Path) -> str:
    digest = hashlib.sha256()
    with pdf_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_pdf_text(pdf_path: Path, pages: int = 8) -> str:
    if fitz is None:
        raise RuntimeError("缺少 PyMuPDF，请先安装：pip install PyMuPDF")

    with fitz.open(str(pdf_path)) as doc:
        chunks = []
        for index in range(min(pages, doc.page_count)):
            chunks.append(doc.load_page(index).get_text("text"))
    text = "\n".join(chunks).strip()
    if not text:
        raise RuntimeError("未能从 PDF 提取文字，可能是扫描版论文。")
    return text[:30000]


def build_prompt(pdf_text: str) -> str:
    return (
        "请从下面 PDF 的首页和前几页文字中判断它是否为学术论文，并识别论文信息。"
        "只返回严格 JSON，不要解释，不要输出 Markdown。\n"
        "必须返回这些字段：\n"
        "- is_paper: true/false，判断该 PDF 是否是学术论文。\n"
        "- title: 原文论文题目。\n"
        "- title_zh: 论文题目的中文翻译；如果原题目已经是中文，也照填中文题目。\n"
        "- authors: 全部作者，字符串数组或用逗号分隔的字符串。\n"
        "- authors_zh: 作者中文译名；无法可靠翻译时可照填原文。\n"
        "- first_author: 第一作者姓名。\n"
        "- last_author: 最后一个作者姓名；如果最后一个作者不是通讯作者，也按最后作者填写。\n"
        "- corresponding_author_affiliation: 通讯作者单位；无法确认则留空。\n"
        "- publisher: 期刊/会议/出版社原文名称。\n"
        "- publisher_zh: 期刊/会议/出版社中文翻译；无法可靠翻译时可照填原文。\n"
        "- published_time: 原文可确认的年份或完整日期，无法确认则留空。\n"
        "- abstract_zh: 中文摘要，概括研究目的、方法、主要发现和意义。\n"
        "- abstract_en: English abstract summarizing objective, methods, findings and significance.\n"
        "- plain_language_summary: 用大白话中文说明这篇论文主要做了什么。\n\n"
        "如果字段无法从文本中确认，请使用空字符串，不要编造。\n\n"
        f"PDF 文本：\n{pdf_text}"
    )


def api_headers(config: ApiConfig) -> dict:
    auth_header = config.auth_header.strip() or "Authorization"
    auth_value = (
        f"Bearer {config.api_key}"
        if auth_header.lower() == "authorization"
        else config.api_key
    )
    return {auth_header: auth_value, "Content-Type": "application/json"}


def call_openai_compatible_api(prompt: str, config: ApiConfig) -> PaperMetadata:
    base_url = normalize_chat_completions_url(config.base_url)
    if not base_url:
        raise RuntimeError("缺少 API 地址。")
    if not config.model:
        raise RuntimeError("缺少模型名称。")
    if not config.api_key:
        raise RuntimeError("缺少 API Key。")

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": "你是论文元数据和摘要抽取助手，只输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_completion_tokens": 2048,
        "response_format": {"type": "json_object"},
    }
    response = requests.post(
        base_url, headers=api_headers(config), json=payload, timeout=120
    )
    if response.status_code == 400 and "response_format" in response.text:
        payload.pop("response_format", None)
        response = requests.post(
            base_url, headers=api_headers(config), json=payload, timeout=120
        )
    if response.status_code == 400 and "max_completion_tokens" in response.text:
        payload["max_tokens"] = payload.pop("max_completion_tokens")
        response = requests.post(
            base_url, headers=api_headers(config), json=payload, timeout=120
        )
    if response.status_code >= 400:
        raise RuntimeError(format_api_error(response))

    api_payload = response.json()
    message = api_payload["choices"][0]["message"]
    content = message.get("content") or message.get("reasoning_content") or ""
    return parse_metadata_json(content)


def validate_openai_compatible_api(config: ApiConfig) -> str:
    base_url = normalize_chat_completions_url(config.base_url)
    if not base_url:
        raise RuntimeError("缺少 API 地址。")
    if not config.model:
        raise RuntimeError("缺少模型名称。")
    if not config.api_key:
        raise RuntimeError("缺少 API Key。")

    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": "请只回复 OK。"}],
        "temperature": 0.0,
        "max_completion_tokens": 16,
    }
    response = requests.post(
        base_url, headers=api_headers(config), json=payload, timeout=30
    )
    if response.status_code == 400 and "max_completion_tokens" in response.text:
        payload["max_tokens"] = payload.pop("max_completion_tokens")
        response = requests.post(
            base_url, headers=api_headers(config), json=payload, timeout=30
        )
    if response.status_code >= 400:
        raise RuntimeError(format_api_error(response))
    payload = response.json()
    model = payload.get("model") or config.model
    return f"API Key 有效，模型响应正常：{model}"


def format_api_error(response: requests.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            error = data.get("error", data)
            if isinstance(error, dict):
                message = error.get("message") or error.get("detail") or str(error)
            else:
                message = str(error)
        else:
            message = str(data)
    except ValueError:
        message = response.text.strip()
    return f"HTTP {response.status_code}: {message[:500]}"


def normalize_chat_completions_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return ""
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return base_url


def read_metadata_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


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


def find_duplicate(root_dir: Path, metadata: PaperMetadata | None = None):
    wanted_title = normalized_title(metadata.title if metadata else "")
    wanted_title_zh = normalized_title(metadata.title_zh if metadata else "")
    if not (wanted_title or wanted_title_zh):
        return None, None
    for folder, payload in iter_archive_payloads(root_dir):
        payload_titles = {
            normalized_title(stringify(payload.get("title"))),
            normalized_title(stringify(payload.get("title_zh"))),
        }
        if (wanted_title and wanted_title in payload_titles) or (
            wanted_title_zh and wanted_title_zh in payload_titles
        ):
            return folder, payload
    return None, None


def archive_paper(
    pdf_path: Path,
    root_dir: Path,
    metadata: PaperMetadata,
    prompt: str = "",
    existing_note: str = "",
) -> tuple[Path, dict, bool]:
    root_dir.mkdir(parents=True, exist_ok=True)
    paper_hash = pdf_hash(pdf_path)
    duplicate_folder, duplicate_payload = find_duplicate(root_dir, metadata)
    if duplicate_folder and duplicate_payload:
        return duplicate_folder, duplicate_payload, True

    title = compact_text(metadata.title or metadata.title_zh, pdf_path.stem, 72)
    first_author_source = metadata.first_author or metadata.authors.split(",")[0]
    first_author = compact_text(first_author_source, "unknown-author", 32)
    year_match = re.search(r"(19|20)\d{2}", metadata.published_time)
    year = year_match.group(0) if year_match else "unknown-year"
    folder_name = compact_text(f"{year} {first_author} {title}", pdf_path.stem, 118)
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
    return folder, payload, False


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


def google_scholar_author_url(metadata: PaperMetadata) -> str:
    query_parts = [
        metadata.first_author or metadata.authors.split(",")[0],
        metadata.corresponding_author_affiliation,
        metadata.title,
    ]
    query = " ".join(part for part in query_parts if part).strip()
    if not query:
        return ""
    search_url = (
        "https://scholar.google.com/citations?view_op=search_authors&hl=zh-CN&mauthors="
        + quote_plus(query)
    )
    response = requests.get(
        search_url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    if response.status_code >= 400:
        return ""
    match = re.search(r'href="(/citations\?user=[^"]+)"', response.text)
    if not match:
        return ""
    return "https://scholar.google.com" + html.unescape(match.group(1)).replace("&amp;", "&")


def strip_html_text(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>|</li>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    return re.sub(r"\n{2,}", "\n", value).strip()


def compact_lines(text: str) -> list[str]:
    noisy = ("layui.", "function()", "showecharts_", "shadeClose", "onmouseout", "onclick")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not any(token in line for token in noisy)
    ]


def query_journal_partitions(journal_name: str) -> tuple[str, str]:
    query = journal_name.strip()
    if not query:
        raise RuntimeError("请输入期刊名称。")

    search_url = (
        "https://www.letpub.com.cn/index.php?page=journalapp&view=search&searchname="
        + quote_plus(query)
    )
    response = requests.get(search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    response.encoding = "utf-8"
    if response.status_code >= 400:
        raise RuntimeError(f"LetPub 查询失败：HTTP {response.status_code}")

    match = re.search(
        r"index\.php\?journalid=(\d+)&amp;page=journalapp&amp;view=detail",
        response.text,
    ) or re.search(r"journalid=(\d+)&page=journalapp&view=detail", response.text)
    if not match:
        return (
            "没有在 LetPub 公开页面里定位到期刊详情。可以点击下方链接手动检索。\n"
            f"{search_url}",
            search_url,
        )

    detail_url = (
        f"https://www.letpub.com.cn/index.php?journalid={match.group(1)}"
        "&page=journalapp&view=detail"
    )
    detail = requests.get(detail_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    detail.encoding = "utf-8"
    if detail.status_code >= 400:
        raise RuntimeError(f"LetPub 详情页读取失败：HTTP {detail.status_code}")

    text = strip_html_text(detail.text)
    lines = compact_lines(text)
    title = query
    h1_matches = re.findall(r"(?is)<h1[^>]*>(.*?)</h1>", detail.text)
    for raw_title in h1_matches:
        candidate = strip_html_text(raw_title).replace("期刊收藏夹", "").strip()
        if normalized_title(query) in normalized_title(candidate):
            title = compact_text(candidate, query, 120)
            break

    result_lines = [f"期刊：{title}", f"来源：LetPub 公开期刊详情页", f"链接：{detail_url}", ""]
    for label in ("2024-2025最新影响因子", "实时影响因子", "五年影响因子", "期刊ISSN"):
        for index, line in enumerate(lines):
            if label in line:
                result_lines.append(line)
                if index + 1 < len(lines) and label == "期刊ISSN":
                    result_lines.append(lines[index + 1])
                break

    wos_match = re.search(r"WOS期刊JCR分区\s*（\s*([^）]+)\s*）\s*WOS分区等级：\s*([^\n]+)", text)
    if wos_match:
        result_lines.extend(["", f"JCR/WOS 分区：{wos_match.group(2).strip()}"])
        result_lines.append(f"JCR/WOS 年份：{wos_match.group(1).strip()}")

    jif_lines = [line for line in lines if line.startswith("学科：") and " Q" in line]
    if jif_lines:
        result_lines.append("")
        result_lines.append("JIF/JCI 学科分区：")
        result_lines.extend(jif_lines[:8])

    cas_sections = []
    start_index = next((i for i, line in enumerate(lines) if "WOS期刊JCR分区" in line), 0)
    for index, line in enumerate(lines[start_index:], start=start_index):
        if line == "《新锐期刊分区表》" or (
            line == "期刊分区表" and index + 1 < len(lines) and "升级版" in lines[index + 1]
        ):
            cas_sections.extend(lines[index : index + 8])
            cas_sections.append("")
    if cas_sections:
        result_lines.append("中科院分区表：")
        result_lines.extend(cas_sections[:24])
    else:
        result_lines.append("中科院分区表：未在公开页面中解析到明确分区。")

    result_lines.append("提示：分区数据会更新，正式用途建议以机构订阅的中科院分区表和 Clarivate JCR 为准。")
    return "\n".join(result_lines), detail_url


def model_interaction_text(item: PaperItem | None) -> str:
    if not item or not item.prompt:
        return ""
    return (
        "System:\n"
        "你是论文元数据和摘要抽取助手，只输出严格 JSON。\n\n"
        "User:\n"
        f"{item.prompt}"
    )


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
                text = extract_pdf_text(pdf_path)
                prompt = build_prompt(text)
                metadata = call_openai_compatible_api(prompt, self.config)
                folder, payload, duplicate = archive_paper(
                    pdf_path, self.archive_root, metadata, prompt
                )
                item = PaperItem(
                    pdf_path=pdf_path,
                    metadata=metadata_from_dict(payload),
                    prompt=stringify(payload.get("model_prompt")) or prompt,
                    folder=folder,
                    duplicate=duplicate,
                    json_payload=payload,
                    note=stringify(payload.get("manual_notes")),
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


class DropArea(QLabel):
    pdfDropped = pyqtSignal(list)

    def __init__(self):
        super().__init__("拖入一个或多个 PDF 到这里\n或点击“导入 PDF / 批量导入”")
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumHeight(110)
        self.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #6b7280;
                border-radius: 8px;
                color: #374151;
                background: #f8fafc;
                font-size: 16px;
            }
            """
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(url.toLocalFile().lower().endswith(".pdf") for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith(".pdf")
        ]
        if paths:
            self.pdfDropped.emit(paths)
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("论文识别归档软件")
        self.resize(1120, 900)
        self.config_data = load_config()
        self.paper_items: list[PaperItem] = []
        self.current_paper_index = 0
        self.worker: ParseWorker | None = None
        self.validate_worker: ValidateWorker | None = None
        self.scholar_worker: ScholarWorker | None = None
        self.journal_worker: JournalLookupWorker | None = None
        self.current_journal_url = ""

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        main_tab = QWidget()
        other_tab = QWidget()
        journal_tab = QWidget()
        tabs.addTab(main_tab, "主页面")
        tabs.addTab(journal_tab, "论文检索")
        tabs.addTab(other_tab, "其他")

        root = QVBoxLayout(main_tab)
        self.drop_area = DropArea()
        self.drop_area.pdfDropped.connect(self.set_pdfs)
        root.addWidget(self.drop_area)

        controls = QHBoxLayout()
        self.import_button = QPushButton("导入 PDF")
        self.batch_import_button = QPushButton("批量导入 PDF")
        self.parse_button = QPushButton("大模型解析")
        self.scholar_button = QPushButton("Google Scholar 主页")
        controls.addWidget(self.import_button)
        controls.addWidget(self.batch_import_button)
        controls.addWidget(self.parse_button)
        controls.addWidget(self.scholar_button)
        controls.addStretch()
        root.addLayout(controls)

        self.status = QLabel("请选择或拖入 PDF。")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.status)
        root.addWidget(self.progress)

        paper_nav_box = QGroupBox("当前论文")
        paper_nav_layout = QHBoxLayout(paper_nav_box)
        self.prev_paper_button = QPushButton("上一篇")
        self.next_paper_button = QPushButton("下一篇")
        self.paper_position_label = QLabel("暂无论文")
        self.paper_position_label.setAlignment(Qt.AlignCenter)
        self.existing_notice = QLabel("")
        self.existing_notice.setStyleSheet("color: #b45309; font-weight: 600;")
        paper_nav_layout.addWidget(self.prev_paper_button)
        paper_nav_layout.addWidget(self.next_paper_button)
        paper_nav_layout.addWidget(self.paper_position_label, 1)
        paper_nav_layout.addWidget(self.existing_notice, 2)
        root.addWidget(paper_nav_box)

        api_box = QGroupBox("大模型 API")
        api_layout = QGridLayout(api_box)
        self.provider_combo = QComboBox()
        for key, preset in PROVIDER_PRESETS.items():
            self.provider_combo.addItem(preset["name"], key)
        self.base_url_edit = QLineEdit()
        self.model_edit = QLineEdit()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.auth_header_edit = QLineEdit()
        self.validate_key_button = QPushButton("验证 API Key")
        self.save_key_button = QPushButton("保存 API Key")
        self.validate_key_button.setFixedWidth(110)
        self.save_key_button.setFixedWidth(110)
        api_buttons = QHBoxLayout()
        api_buttons.addWidget(self.validate_key_button)
        api_buttons.addWidget(self.save_key_button)
        api_buttons.addStretch()
        api_layout.addWidget(QLabel("服务"), 0, 0)
        api_layout.addWidget(self.provider_combo, 0, 1)
        api_layout.addWidget(QLabel("API 地址"), 0, 2)
        api_layout.addWidget(self.base_url_edit, 0, 3)
        api_layout.addWidget(QLabel("模型"), 1, 0)
        api_layout.addWidget(self.model_edit, 1, 1)
        api_layout.addWidget(QLabel("API Key"), 1, 2)
        api_layout.addWidget(self.api_key_edit, 1, 3)
        api_layout.addWidget(QLabel("认证头"), 2, 0)
        api_layout.addWidget(self.auth_header_edit, 2, 1)
        api_layout.addLayout(api_buttons, 2, 2, 1, 2)
        root.addWidget(api_box)

        archive_box = QGroupBox("归档目录")
        archive_layout = QHBoxLayout(archive_box)
        self.archive_root_edit = QLineEdit(str(DEFAULT_ARCHIVE_ROOT))
        self.choose_archive_button = QPushButton("选择目录")
        self.open_archive_button = QPushButton("打开目录")
        self.archive_stats_button = QPushButton("日志")
        archive_layout.addWidget(self.archive_root_edit)
        archive_layout.addWidget(self.choose_archive_button)
        archive_layout.addWidget(self.open_archive_button)
        archive_layout.addWidget(self.archive_stats_button)
        root.addWidget(archive_box)

        fields_box = QGroupBox("论文信息编辑")
        form = QFormLayout(fields_box)
        self.title_edit = QLineEdit()
        self.title_zh_edit = QLineEdit()
        self.authors_edit = QLineEdit()
        self.authors_zh_edit = QLineEdit()
        self.first_author_edit = QLineEdit()
        self.last_author_edit = QLineEdit()
        self.corresponding_affiliation_edit = QLineEdit()
        self.publisher_edit = QLineEdit()
        self.publisher_zh_edit = QLineEdit()
        self.time_edit = QLineEdit()
        self.save_info_button = QPushButton("保存论文信息")
        form.addRow("原文题目", self.title_edit)
        form.addRow("中文题目", self.title_zh_edit)
        form.addRow("作者", self.authors_edit)
        form.addRow("作者中文", self.authors_zh_edit)
        form.addRow("第一作者", self.first_author_edit)
        form.addRow("最后作者", self.last_author_edit)
        form.addRow("通讯作者单位", self.corresponding_affiliation_edit)
        form.addRow("出版社/期刊/会议", self.publisher_edit)
        form.addRow("出版社/期刊/会议中文", self.publisher_zh_edit)
        form.addRow("发表时间", self.time_edit)
        form.addRow("", self.save_info_button)
        root.addWidget(fields_box)

        summary_box = QGroupBox("摘要与笔记")
        summary_layout = QGridLayout(summary_box)
        self.abstract_zh_edit = QTextEdit()
        self.abstract_en_edit = QTextEdit()
        self.plain_summary_edit = QTextEdit()
        self.note_edit = QTextEdit()
        self.save_note_button = QPushButton("保存人工笔记")
        for editor in (
            self.abstract_zh_edit,
            self.abstract_en_edit,
            self.plain_summary_edit,
            self.note_edit,
        ):
            editor.setMinimumHeight(90)
        summary_layout.addWidget(QLabel("中文摘要"), 0, 0)
        summary_layout.addWidget(QLabel("English Abstract"), 0, 1)
        summary_layout.addWidget(self.abstract_zh_edit, 1, 0)
        summary_layout.addWidget(self.abstract_en_edit, 1, 1)
        summary_layout.addWidget(QLabel("大白话"), 2, 0)
        summary_layout.addWidget(QLabel("人工笔记"), 2, 1)
        summary_layout.addWidget(self.plain_summary_edit, 3, 0)
        summary_layout.addWidget(self.note_edit, 3, 1)
        summary_layout.addWidget(self.save_note_button, 4, 1)
        root.addWidget(summary_box)

        journal_layout = QVBoxLayout(journal_tab)
        journal_controls = QHBoxLayout()
        self.journal_query_edit = QLineEdit()
        self.journal_query_edit.setPlaceholderText("输入期刊名，例如 Scientific Reports")
        self.journal_use_current_button = QPushButton("使用当前期刊")
        self.journal_search_button = QPushButton("查询分区")
        self.journal_open_button = QPushButton("打开来源页面")
        journal_controls.addWidget(self.journal_query_edit, 1)
        journal_controls.addWidget(self.journal_use_current_button)
        journal_controls.addWidget(self.journal_search_button)
        journal_controls.addWidget(self.journal_open_button)
        journal_layout.addLayout(journal_controls)
        self.journal_result = QTextEdit()
        self.journal_result.setReadOnly(True)
        self.journal_result.setPlaceholderText("这里显示期刊的中科院分区、JCR/WOS 分区和来源链接。")
        journal_layout.addWidget(self.journal_result)

        other_layout = QVBoxLayout(other_tab)
        other_buttons = QHBoxLayout()
        self.show_json_button = QPushButton("显示 JSON 内容")
        self.show_prompt_button = QPushButton("显示模型交互")
        other_buttons.addWidget(self.show_json_button)
        other_buttons.addWidget(self.show_prompt_button)
        other_buttons.addStretch()
        other_layout.addLayout(other_buttons)
        self.json_preview = QTextEdit()
        self.json_preview.setReadOnly(True)
        self.prompt_preview = QTextEdit()
        self.prompt_preview.setReadOnly(True)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        other_layout.addWidget(QLabel("JSON 内容"))
        other_layout.addWidget(self.json_preview)
        other_layout.addWidget(QLabel("模型提示词 / 交互内容"))
        other_layout.addWidget(self.prompt_preview)
        other_layout.addWidget(QLabel("日志"))
        other_layout.addWidget(self.log)

        self.import_button.clicked.connect(self.choose_pdf)
        self.batch_import_button.clicked.connect(self.choose_pdfs)
        self.parse_button.clicked.connect(self.parse_pdfs)
        self.choose_archive_button.clicked.connect(self.choose_archive_root)
        self.open_archive_button.clicked.connect(self.open_archive_root)
        self.archive_stats_button.clicked.connect(self.show_archive_stats)
        self.validate_key_button.clicked.connect(self.validate_api_key)
        self.save_key_button.clicked.connect(self.save_api_key)
        self.provider_combo.currentIndexChanged.connect(self.apply_provider_preset)
        self.prev_paper_button.clicked.connect(lambda: self.move_paper(-1))
        self.next_paper_button.clicked.connect(lambda: self.move_paper(1))
        self.save_info_button.clicked.connect(self.save_current_info)
        self.save_note_button.clicked.connect(self.save_note)
        self.show_json_button.clicked.connect(self.show_current_json)
        self.show_prompt_button.clicked.connect(self.show_current_prompt)
        self.scholar_button.clicked.connect(self.open_google_scholar)
        self.journal_use_current_button.clicked.connect(self.use_current_journal)
        self.journal_search_button.clicked.connect(self.lookup_journal)
        self.journal_open_button.clicked.connect(self.open_journal_source)
        self.apply_provider_preset()
        self.update_current_view()

        self.setStyleSheet(
            """
            QMainWindow { background: #ffffff; }
            QPushButton {
                padding: 8px 12px;
                border-radius: 6px;
                border: 1px solid #cbd5e1;
                background: #ffffff;
            }
            QPushButton:hover { background: #f1f5f9; }
            QGroupBox {
                font-weight: 600;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 14px;
            }
            QLineEdit, QTextEdit, QComboBox {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px;
            }
            """
        )

    def log_message(self, message: str) -> None:
        self.log.append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")

    def selected_provider_key(self) -> str:
        return self.provider_combo.currentData()

    def apply_provider_preset(self) -> None:
        key = self.selected_provider_key()
        preset = PROVIDER_PRESETS[key]
        saved = self.config_data.get("api_configs", {}).get(key, {})
        self.base_url_edit.setText(
            saved.get("base_url") or os.environ.get(preset["base_url_env"], preset["base_url"])
        )
        self.model_edit.setText(
            saved.get("model") or os.environ.get(preset["model_env"], preset["model"])
        )
        self.api_key_edit.setText(
            saved.get("api_key") or os.environ.get(preset["api_key_env"], "")
        )
        self.auth_header_edit.setText(saved.get("auth_header") or preset["auth_header"])

    def current_api_config(self) -> ApiConfig:
        return ApiConfig(
            provider=self.selected_provider_key(),
            base_url=self.base_url_edit.text().strip(),
            model=self.model_edit.text().strip(),
            api_key=self.api_key_edit.text().strip(),
            auth_header=self.auth_header_edit.text().strip() or "Authorization",
        )

    def save_api_key(self) -> None:
        config = self.current_api_config()
        self.config_data.setdefault("api_configs", {})[config.provider] = asdict(config)
        save_config(self.config_data)
        self.log_message(f"已保存 {PROVIDER_PRESETS[config.provider]['name']} API 配置。")
        QMessageBox.information(self, "保存成功", "API Key 和当前 API 配置已保存到 config.json。")

    def validate_api_key(self) -> None:
        config = self.current_api_config()
        provider_name = PROVIDER_PRESETS[config.provider]["name"]
        self.validate_key_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText(f"正在验证 {provider_name} API Key...")
        self.log_message(f"开始验证 API：{provider_name}")
        self.validate_worker = ValidateWorker(config)
        self.validate_worker.succeeded.connect(self.on_validate_succeeded)
        self.validate_worker.failed.connect(self.on_validate_failed)
        self.validate_worker.start()

    def on_validate_succeeded(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.validate_key_button.setEnabled(True)
        self.status.setText(message)
        self.log_message(message)
        QMessageBox.information(self, "验证成功", message)

    def on_validate_failed(self, reason: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.validate_key_button.setEnabled(True)
        self.status.setText("API Key 验证失败。")
        self.log_message(f"API Key 验证失败：{reason}")
        QMessageBox.critical(self, "验证失败", f"API 配置可能不匹配。\n\n{reason}")

    def choose_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF", "", "PDF 文件 (*.pdf)")
        if path:
            self.set_pdfs([path])

    def choose_pdfs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "批量选择 PDF", "", "PDF 文件 (*.pdf)")
        if paths:
            self.set_pdfs(paths)

    def set_pdfs(self, paths: list[str]) -> None:
        self.paper_items = [PaperItem(pdf_path=Path(path)) for path in paths]
        self.current_paper_index = 0
        self.drop_area.setText(f"已导入 {len(paths)} 个 PDF")
        self.status.setText(f"已导入 {len(paths)} 个 PDF，点击“大模型解析”后会自动归档。")
        self.log_message(f"已导入 PDF 数量：{len(paths)}")
        self.update_current_view()

    def choose_archive_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择归档根目录", self.archive_root_edit.text()
        )
        if folder:
            self.archive_root_edit.setText(folder)

    def archive_root(self) -> Path:
        return Path(self.archive_root_edit.text().strip() or DEFAULT_ARCHIVE_ROOT)

    def open_archive_root(self) -> None:
        root = self.archive_root()
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root.resolve())))
        self.log_message(f"已打开归档目录：{root}")

    def parse_pdfs(self) -> None:
        if not self.paper_items:
            QMessageBox.warning(self, "缺少 PDF", "请先导入或拖入 PDF。")
            return
        self.sync_current_from_fields()
        config = self.current_api_config()
        self.progress.setRange(0, 0)
        self.parse_button.setEnabled(False)
        self.status.setText(
            f"正在解析 {len(self.paper_items)} 个 PDF，调用 {PROVIDER_PRESETS[config.provider]['name']}..."
        )
        self.worker = ParseWorker(
            [item.pdf_path for item in self.paper_items], config, self.archive_root()
        )
        self.worker.itemFinished.connect(self.on_item_finished)
        self.worker.failed.connect(self.on_item_failed)
        self.worker.finished.connect(self.on_parse_finished)
        self.worker.start()

    def on_item_finished(self, index: int, total: int, item: PaperItem) -> None:
        self.paper_items[index - 1] = item
        self.current_paper_index = index - 1
        flag = "重复，已读取旧 JSON 和人工笔记" if item.duplicate else "解析完成"
        self.log_message(
            f"[{index}/{total}] {item.pdf_path.name}：{flag}；是否论文：{'是' if item.metadata.is_paper else '否'}"
        )
        self.status.setText(f"已处理 {index}/{total}：{item.pdf_path.name}")
        self.update_current_view()

    def on_item_failed(self, index: int, total: int, pdf_path: str, reason: str) -> None:
        self.log_message(f"[{index}/{total}] 解析失败：{pdf_path}；{reason}")
        self.status.setText(f"解析失败 {index}/{total}：{Path(pdf_path).name}")

    def on_parse_finished(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.parse_button.setEnabled(True)
        count = len(self.paper_items)
        paper_count = sum(1 for item in self.paper_items if item.metadata.is_paper)
        self.status.setText(f"批量处理完成：共 {count} 个 PDF，识别为论文 {paper_count} 个。")
        self.log_message(f"批量处理完成：共 {count} 个 PDF，识别为论文 {paper_count} 个。")
        self.update_current_view()

    def current_item(self) -> PaperItem | None:
        if not self.paper_items:
            return None
        self.current_paper_index = max(0, min(self.current_paper_index, len(self.paper_items) - 1))
        return self.paper_items[self.current_paper_index]

    def move_paper(self, offset: int) -> None:
        if not self.paper_items:
            return
        self.sync_current_from_fields()
        self.current_paper_index = (self.current_paper_index + offset) % len(self.paper_items)
        self.update_current_view()

    def update_current_view(self) -> None:
        item = self.current_item()
        if not item:
            self.paper_position_label.setText("暂无论文")
            self.existing_notice.setText("")
            self.apply_metadata(PaperMetadata(), "")
            self.show_current_json()
            self.show_current_prompt()
            return
        metadata = item.metadata
        title = metadata.title or metadata.title_zh or item.pdf_path.name
        self.paper_position_label.setText(
            f"{self.current_paper_index + 1}/{len(self.paper_items)}  {title}"
        )
        self.existing_notice.setText(
            f"已存在此论文，已读取论文信息/摘要/笔记：{item.folder}"
            if item.duplicate and item.folder
            else ""
        )
        self.apply_metadata(metadata, item.note)
        self.show_current_json()
        self.show_current_prompt()

    def apply_metadata(self, metadata: PaperMetadata, note: str) -> None:
        self.title_edit.setText(metadata.title)
        self.title_zh_edit.setText(metadata.title_zh)
        self.authors_edit.setText(metadata.authors)
        self.authors_zh_edit.setText(metadata.authors_zh)
        self.first_author_edit.setText(metadata.first_author)
        self.last_author_edit.setText(metadata.last_author)
        self.corresponding_affiliation_edit.setText(metadata.corresponding_author_affiliation)
        self.publisher_edit.setText(metadata.publisher)
        self.publisher_zh_edit.setText(metadata.publisher_zh)
        self.time_edit.setText(metadata.published_time)
        self.abstract_zh_edit.setPlainText(metadata.abstract_zh)
        self.abstract_en_edit.setPlainText(metadata.abstract_en)
        self.plain_summary_edit.setPlainText(metadata.plain_language_summary)
        self.note_edit.setPlainText(note)

    def current_metadata(self) -> PaperMetadata:
        return PaperMetadata(
            is_paper=True,
            title=self.title_edit.text().strip(),
            title_zh=self.title_zh_edit.text().strip(),
            authors=self.authors_edit.text().strip(),
            authors_zh=self.authors_zh_edit.text().strip(),
            first_author=self.first_author_edit.text().strip(),
            last_author=self.last_author_edit.text().strip(),
            corresponding_author_affiliation=self.corresponding_affiliation_edit.text().strip(),
            publisher=self.publisher_edit.text().strip(),
            publisher_zh=self.publisher_zh_edit.text().strip(),
            published_time=self.time_edit.text().strip(),
            abstract_zh=self.abstract_zh_edit.toPlainText().strip(),
            abstract_en=self.abstract_en_edit.toPlainText().strip(),
            plain_language_summary=self.plain_summary_edit.toPlainText().strip(),
        )

    def sync_current_from_fields(self) -> None:
        item = self.current_item()
        if not item:
            return
        is_paper = item.metadata.is_paper
        item.metadata = self.current_metadata()
        item.metadata.is_paper = is_paper
        item.note = self.note_edit.toPlainText().strip()

    def archive_current(self) -> None:
        item = self.current_item()
        if not item:
            QMessageBox.warning(self, "缺少 PDF", "请先导入或拖入 PDF。")
            return
        self.sync_current_from_fields()
        if not (item.metadata.title or item.metadata.title_zh):
            QMessageBox.warning(self, "缺少题目", "请先解析或手动填写论文题目。")
            return
        try:
            folder, payload, duplicate = archive_paper(
                item.pdf_path,
                self.archive_root(),
                item.metadata,
                item.prompt,
                item.note,
            )
        except Exception as exc:
            QMessageBox.critical(self, "归档失败", str(exc))
            return
        item.folder = folder
        item.json_payload = payload
        item.duplicate = duplicate
        item.note = stringify(payload.get("manual_notes")) or item.note
        if duplicate:
            message = f"检测到重复论文，未新增归档，已读取旧记录和人工笔记：\n{folder}"
        else:
            message = f"PDF 和 metadata.json 已保存到：\n{folder}"
        self.status.setText(message.replace("\n", " "))
        self.log_message(message.replace("\n", " "))
        QMessageBox.information(self, "归档结果", message)
        self.update_current_view()

    def save_current_info(self) -> None:
        item = self.current_item()
        if not item:
            QMessageBox.warning(self, "缺少论文", "请先导入或解析论文。")
            return
        self.sync_current_from_fields()
        if not (item.metadata.title or item.metadata.title_zh):
            QMessageBox.warning(self, "缺少题目", "请先解析或手动填写论文题目。")
            return
        if not item.folder:
            self.archive_current()
            return
        payload = item.json_payload or read_metadata_file(item.folder / "metadata.json")
        payload.update(asdict(item.metadata))
        payload["manual_notes"] = item.note
        payload.setdefault("model_prompt", item.prompt)
        write_metadata_file(item.folder, payload)
        item.json_payload = payload
        self.log_message(f"已保存论文信息：{item.folder / 'metadata.json'}")
        QMessageBox.information(self, "保存成功", "论文信息、摘要和笔记已写入 metadata.json。")
        self.update_current_view()

    def save_note(self) -> None:
        item = self.current_item()
        if not item:
            QMessageBox.warning(self, "缺少论文", "请先导入或解析论文。")
            return
        self.sync_current_from_fields()
        if not item.folder:
            self.archive_current()
            item = self.current_item()
            if not item or not item.folder:
                return
        payload = item.json_payload or asdict(item.metadata)
        payload.update(asdict(item.metadata))
        payload["manual_notes"] = item.note
        payload.setdefault("model_prompt", item.prompt)
        write_metadata_file(item.folder, payload)
        item.json_payload = payload
        self.log_message(f"已保存人工笔记：{item.folder / 'metadata.json'}")
        QMessageBox.information(self, "保存成功", "人工笔记已写入对应 metadata.json。")
        self.update_current_view()

    def show_current_json(self) -> None:
        item = self.current_item()
        if not item:
            self.json_preview.setPlainText("")
            return
        payload = item.json_payload or asdict(item.metadata)
        payload = dict(payload)
        payload["manual_notes"] = item.note
        payload["model_prompt"] = item.prompt
        self.json_preview.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def show_current_prompt(self) -> None:
        item = self.current_item()
        self.prompt_preview.setPlainText(model_interaction_text(item))

    def show_archive_stats(self) -> None:
        stats = archive_statistics(self.archive_root())
        message = (
            f"归档文件夹：{self.archive_root()}\n"
            f"论文记录：{stats['folders']}\n"
            f"PDF 文件：{stats['pdfs']}\n"
            f"JSON 文件：{stats['jsons']}\n"
            f"识别为论文：{stats['papers']}\n"
            f"识别为非论文：{stats['non_papers']}\n"
            f"带人工笔记：{stats['notes']}"
        )
        self.log_message(message.replace("\n", "；"))
        QMessageBox.information(self, "归档日志", message)

    def use_current_journal(self) -> None:
        item = self.current_item()
        if not item:
            QMessageBox.warning(self, "缺少论文", "请先导入或解析论文。")
            return
        self.sync_current_from_fields()
        journal = item.metadata.publisher or item.metadata.publisher_zh
        self.journal_query_edit.setText(journal)
        if journal:
            self.lookup_journal()

    def lookup_journal(self) -> None:
        journal = self.journal_query_edit.text().strip()
        if not journal:
            QMessageBox.warning(self, "缺少期刊名", "请输入期刊名称。")
            return
        self.journal_search_button.setEnabled(False)
        self.journal_result.setPlainText(f"正在查询：{journal}")
        self.journal_worker = JournalLookupWorker(journal)
        self.journal_worker.succeeded.connect(self.on_journal_lookup_succeeded)
        self.journal_worker.failed.connect(self.on_journal_lookup_failed)
        self.journal_worker.start()

    def on_journal_lookup_succeeded(self, result: str, url: str) -> None:
        self.journal_search_button.setEnabled(True)
        self.current_journal_url = url
        self.journal_result.setPlainText(result)
        self.log_message(f"期刊分区查询完成：{url}")

    def on_journal_lookup_failed(self, reason: str) -> None:
        self.journal_search_button.setEnabled(True)
        self.current_journal_url = ""
        self.journal_result.setPlainText(f"查询失败：{reason}")
        QMessageBox.warning(self, "查询失败", reason)

    def open_journal_source(self) -> None:
        url = self.current_journal_url
        if not url:
            query = self.journal_query_edit.text().strip()
            url = (
                "https://www.letpub.com.cn/index.php?page=journalapp&view=search&searchname="
                + quote_plus(query)
                if query
                else "https://www.letpub.com.cn/index.php?page=journalapp"
            )
        QDesktopServices.openUrl(QUrl(url))

    def open_google_scholar(self) -> None:
        item = self.current_item()
        if not item:
            QMessageBox.warning(self, "缺少论文", "请先导入或解析论文。")
            return
        self.sync_current_from_fields()
        self.scholar_button.setEnabled(False)
        self.status.setText("正在查找 Google Scholar 作者主页...")
        self.scholar_worker = ScholarWorker(item.metadata)
        self.scholar_worker.succeeded.connect(self.on_scholar_found)
        self.scholar_worker.failed.connect(self.on_scholar_not_found)
        self.scholar_worker.start()

    def on_scholar_found(self, url: str) -> None:
        self.scholar_button.setEnabled(True)
        self.status.setText("已找到 Google Scholar 作者主页。")
        self.log_message(f"Google Scholar：{url}")
        QDesktopServices.openUrl(QUrl(url))

    def on_scholar_not_found(self) -> None:
        self.scholar_button.setEnabled(True)
        self.status.setText("未找到 Google Scholar 作者主页。")
        QMessageBox.information(self, "未找到", "没有找到可确认的 Google Scholar 作者主页。")


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
