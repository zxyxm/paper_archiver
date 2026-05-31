import json
from dataclasses import asdict

from .models import PaperItem


def model_interaction_text(item: PaperItem | None) -> str:
    if not item or not item.prompt:
        return ""
    return (
        "System:\n"
        "你是论文元数据和摘要抽取助手，只输出严格 JSON。\n\n"
        "User:\n"
        f"{item.prompt}"
    )
