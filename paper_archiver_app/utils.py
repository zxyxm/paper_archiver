import re


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

def compact_text(value: str, fallback: str = "untitled", max_len: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\r\n\t]+', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value[:max_len].strip(" ._") or fallback)[:max_len]

def normalized_title(value: str) -> str:
    return re.sub(r"\W+", "", (value or "").lower(), flags=re.U)

