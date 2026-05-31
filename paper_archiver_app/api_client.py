import re
from pathlib import Path

import requests

from .metadata import parse_metadata_json
from .models import ApiConfig, PaperMetadata

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - shown in UI at runtime
    fitz = None


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
        "- corresponding_author: 通讯作者姓名；无法确认则留空。\n"
        "- corresponding_author_affiliation: 通讯作者单位；无法确认则留空。\n"
        "- corresponding_author_affiliation_zh: 通讯作者单位中文翻译；无法可靠翻译时可照填原文。\n"
        "- publisher: 期刊/会议/出版社原文名称。\n"
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

