import html
import re
from urllib.parse import quote_plus

import requests

from .models import PaperMetadata


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

