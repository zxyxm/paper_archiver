import re
from pathlib import Path

import requests

from .metadata import parse_metadata_json
from .models import ApiConfig, PaperMetadata

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - shown in UI at runtime
    fitz = None


SUPPORTED_DOCUMENT_SUFFIXES = {".pdf", ".caj"}


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

def extract_caj_text(caj_path: Path, max_chars: int = 30000) -> str:
    data = caj_path.read_bytes()
    candidates: list[str] = []
    for encoding in ("utf-8", "gb18030", "utf-16-le", "utf-16-be"):
        text = data.decode(encoding, errors="ignore")
        text = normalize_extracted_text(text)
        if text:
            candidates.append(text)
    text = max(candidates, key=readable_text_score, default="")
    if readable_text_score(text) < 120:
        raise RuntimeError("未能从 CAJ 提取可读文字，请先用知网阅读器导出为 PDF 后再解析。")
    return text[:max_chars]

def extract_document_text(path: Path, pages: int = 8) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path, pages)
    if suffix == ".caj":
        return extract_caj_text(path)
    raise RuntimeError(f"不支持的文件类型：{suffix or path.name}")

def normalize_extracted_text(text: str) -> str:
    text = text.replace("\x00", "")
    runs = re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。；：、“”‘’（）《》【】\[\]\-_/ %.!?]{2,}", text)
    return "\n".join(run.strip() for run in runs if run.strip())

def readable_text_score(text: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    return chinese * 2 + letters

def build_prompt(document_text: str, source_name: str = "") -> str:
    source_hint = f"文档名称：{source_name}\n" if source_name else ""
    return (
        "请从下面文档的首页和前几页文字中判断它是否为学术论文，并识别论文信息。"
        "只返回严格 JSON，不要解释，不要输出 Markdown。\n"
        "必须返回这些字段：\n"
        "- is_paper: true/false，判断该文档是否是学术论文、学位论文或学术报告。\n"
        "- title: 原文论文题目；如果不是论文，填写可识别的文档标题或主题。\n"
        "- title_zh: 论文题目的中文翻译；如果原题目已经是中文，也照填中文题目；如果不是论文，填写中文标题或主题。\n"
        "- authors: 全部作者，字符串数组或用逗号分隔的字符串。\n"
        "- authors_zh: 作者中文译名；无法可靠翻译时可照填原文。\n"
        "- first_author: 第一作者姓名。\n"
        "- corresponding_author: 通讯作者姓名；无法确认则留空。\n"
        "- corresponding_author_affiliation: 通讯作者单位；无法确认则留空。\n"
        "- corresponding_author_affiliation_zh: 通讯作者单位中文翻译；无法可靠翻译时可照填原文。\n"
        "- publisher: 期刊/会议/出版社原文名称。\n"
        "- published_time: 原文可确认的年份或完整日期，无法确认则留空。\n"
        "- abstract_zh: 中文摘要，概括研究目的、方法、主要发现和意义；如果不是论文可留空。\n"
        "- abstract_en: English abstract summarizing objective, methods, findings and significance; leave empty if not a paper.\n"
        "- plain_language_summary: 用大白话中文说明这篇论文主要做了什么；如果不是论文，也要说明文档大概是什么内容。\n\n"
        "- tags: 适合检索的中文标签数组。\n\n"
        "如果是中国知网 CAJ 或中文学位论文，请特别注意："
        "学位论文也算学术论文；识别它是博士、硕士、学士还是其他层次学位论文；"
        "如果没有通讯作者信息，把作者单位、培养单位或学位授予单位写入 corresponding_author_affiliation，"
        "并把中文单位写入 corresponding_author_affiliation_zh；"
        "plain_language_summary 第一行必须说明学位层次，例如“本文是硕士学位论文，主题是...”。"
        "tags 里加入对应的“博士学位论文”“硕士学位论文”或“学士学位论文”。\n"
        "如果不是学术论文，论文专用字段请留空，但 plain_language_summary 不能留空，"
        "第一行必须用“本文是...”开头，先说明它是什么（例如报告、说明书、课件、通知、书籍章节等），"
        "后面再用大白话概括主要内容。\n"
        "如果字段无法从文本中确认，请使用空字符串，不要编造。\n\n"
        f"{source_hint}文档文本：\n{document_text}"
    )

def api_headers(config: ApiConfig) -> dict:
    auth_header = config.auth_header.strip() or "Authorization"
    auth_value = (
        f"Bearer {config.api_key}"
        if auth_header.lower() == "authorization"
        else config.api_key
    )
    return {auth_header: auth_value, "Content-Type": "application/json"}

def call_openai_compatible_api(
    prompt: str, config: ApiConfig, source_text: str = ""
) -> PaperMetadata:
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
    return normalize_chinese_thesis_metadata(parse_metadata_json(content), source_text)

def normalize_chinese_thesis_metadata(metadata: PaperMetadata, source_text: str) -> PaperMetadata:
    degree_level = detect_degree_level(source_text)
    if not degree_level:
        return metadata

    metadata.is_paper = True
    degree_tag = f"{degree_level}学位论文"
    if degree_tag not in metadata.tags:
        metadata.tags.append(degree_tag)

    affiliation = (
        metadata.corresponding_author_affiliation
        or metadata.corresponding_author_affiliation_zh
        or thesis_affiliation_from_text(source_text)
        or metadata.publisher
        or metadata.publisher_zh
    )
    if affiliation:
        if not metadata.corresponding_author_affiliation:
            metadata.corresponding_author_affiliation = affiliation
        if not metadata.corresponding_author_affiliation_zh:
            metadata.corresponding_author_affiliation_zh = affiliation
        if not metadata.publisher:
            metadata.publisher = affiliation

    first_line = f"本文是{degree_tag}，主题是{metadata.title_zh or metadata.title or '该研究'}。"
    summary = metadata.plain_language_summary.strip()
    existing_first_line = summary.splitlines()[0] if summary.splitlines() else ""
    if degree_tag not in existing_first_line:
        metadata.plain_language_summary = (
            first_line if not summary else f"{first_line}\n{summary}"
        )
    return metadata

def detect_degree_level(text: str) -> str:
    for level in ("博士", "硕士", "学士"):
        if re.search(rf"{level}\s*(专业)?学位论文|{level}研究生|{level}毕业论文", text):
            return level
    if re.search(r"专业学位硕士|工程硕士|教育硕士|医学硕士|工商管理硕士|公共卫生硕士", text):
        return "硕士"
    return ""

def thesis_affiliation_from_text(text: str) -> str:
    patterns = [
        r"(?:培养单位|学位授予单位|作者单位|所在单位|学校|院系)[:：\s]+([^\n]{2,60})",
        r"([^\n]{2,40}(?:大学|学院|研究院|研究所|医院))[^\n]{0,20}(?:硕士|博士|学士)?学位论文",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ，,。；;:：")
    return ""

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

