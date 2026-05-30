import json
import os
import re
import shutil
import sys
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
    QPlainTextEdit,
    QProgressBar,
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
DEEPSEEK_WEB_URL = "https://chat.deepseek.com/"

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
        "name": "自定义 OpenAI兼容",
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
    title: str = ""
    authors: str = ""
    publisher: str = ""
    published_time: str = ""


@dataclass
class ApiConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    auth_header: str = "Authorization"


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


def parse_metadata_json(text: str) -> PaperMetadata:
    data = json.loads(clean_json_text(text))
    authors = data.get("authors", "")
    if isinstance(authors, list):
        authors = ", ".join(str(item).strip() for item in authors if str(item).strip())
    return PaperMetadata(
        title=str(data.get("title", "")).strip(),
        authors=str(authors).strip(),
        publisher=str(data.get("publisher", "")).strip(),
        published_time=str(
            data.get("published_time", data.get("publication_time", ""))
        ).strip(),
    )


def compact_text(value: str, fallback: str = "untitled", max_len: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\r\n\t]+', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value[:max_len].strip(" ._") or fallback)[:max_len]


def extract_pdf_text(pdf_path: Path, pages: int = 3) -> str:
    if fitz is None:
        raise RuntimeError("缺少 PyMuPDF，请先安装：pip install PyMuPDF")

    with fitz.open(str(pdf_path)) as doc:
        chunks = []
        for index in range(min(pages, doc.page_count)):
            chunks.append(doc.load_page(index).get_text("text"))
    text = "\n".join(chunks).strip()
    if not text:
        raise RuntimeError("未能从 PDF 提取文字，可能是扫描版论文。")
    return text[:12000]


def build_prompt(pdf_text: str) -> str:
    return (
        "请从下面论文首页/前几页文字中识别论文信息，只返回 JSON，不要解释。\n"
        "字段必须为：title, authors, publisher, published_time。\n"
        "authors 可以是字符串或字符串数组；publisher 指期刊/会议/出版社；"
        "published_time 使用原文可确认的年份或完整日期，无法确认则留空。\n\n"
        f"论文文本：\n{pdf_text}"
    )


def call_openai_compatible_api(prompt: str, config: ApiConfig) -> PaperMetadata:
    base_url = normalize_chat_completions_url(config.base_url)
    if not base_url:
        raise RuntimeError("缺少 API 地址。")
    if not config.model:
        raise RuntimeError("缺少模型名称。")
    if not config.api_key:
        raise RuntimeError("缺少 API Key。")

    auth_header = config.auth_header.strip() or "Authorization"
    if auth_header.lower() == "authorization":
        auth_value = f"Bearer {config.api_key}"
    else:
        auth_value = config.api_key

    headers = {
        auth_header: auth_value,
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": "你是论文元数据抽取助手，只输出严格 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_completion_tokens": 1024,
        "response_format": {"type": "json_object"},
    }
    response = requests.post(base_url, headers=headers, json=payload, timeout=90)
    if response.status_code == 400 and "response_format" in response.text:
        payload.pop("response_format", None)
        response = requests.post(base_url, headers=headers, json=payload, timeout=90)
    response.raise_for_status()
    payload = response.json()
    message = payload["choices"][0]["message"]
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

    auth_header = config.auth_header.strip() or "Authorization"
    if auth_header.lower() == "authorization":
        auth_value = f"Bearer {config.api_key}"
    else:
        auth_value = config.api_key

    response = requests.post(
        base_url,
        headers={
            auth_header: auth_value,
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "messages": [{"role": "user", "content": "请只回复 OK。"}],
            "temperature": 0.0,
            "max_completion_tokens": 16,
        },
        timeout=30,
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


def unique_folder(base: Path) -> Path:
    if not base.exists():
        return base
    for index in range(2, 1000):
        candidate = base.with_name(f"{base.name} ({index})")
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法创建唯一归档文件夹。")


def archive_paper(pdf_path: Path, root_dir: Path, metadata: PaperMetadata) -> Path:
    root_dir.mkdir(parents=True, exist_ok=True)

    title = compact_text(metadata.title, pdf_path.stem, 72)
    first_author = compact_text(metadata.authors.split(",")[0], "unknown-author", 32)
    year_match = re.search(r"(19|20)\d{2}", metadata.published_time)
    year = year_match.group(0) if year_match else "unknown-year"
    folder_name = compact_text(f"{year} {first_author} {title}", pdf_path.stem, 118)
    folder = unique_folder(root_dir / folder_name)
    folder.mkdir(parents=True, exist_ok=False)

    pdf_name = compact_text(pdf_path.stem, "paper", 80) + pdf_path.suffix.lower()
    target_pdf = folder / pdf_name
    if pdf_path.resolve() != target_pdf.resolve():
        shutil.copy2(pdf_path, target_pdf)

    payload = asdict(metadata)
    payload["source_pdf"] = str(target_pdf)
    payload["original_pdf"] = str(pdf_path)
    with (folder / "metadata.json").open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return folder


class ParseWorker(QThread):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    promptReady = pyqtSignal(str)

    def __init__(self, pdf_path: Path, config: ApiConfig):
        super().__init__()
        self.pdf_path = pdf_path
        self.config = config

    def run(self) -> None:
        try:
            text = extract_pdf_text(self.pdf_path)
            prompt = build_prompt(text)
            self.promptReady.emit(prompt)
            metadata = call_openai_compatible_api(prompt, self.config)
            self.finished.emit(metadata)
        except Exception as exc:
            self.failed.emit(str(exc))


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


class DropArea(QLabel):
    pdfDropped = pyqtSignal(str)

    def __init__(self):
        super().__init__("拖入 PDF 到这里\n或点击“导入 PDF”")
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumHeight(130)
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
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                self.pdfDropped.emit(path)
                event.acceptProposedAction()
                return


class JsonPasteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("粘贴模型返回 JSON")
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("请粘贴模型返回的 JSON，字段：title, authors, publisher, published_time")
        )
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            '{\n  "title": "...",\n  "authors": ["..."],\n'
            '  "publisher": "...",\n  "published_time": "..."\n}'
        )
        layout.addWidget(self.editor)
        row = QHBoxLayout()
        cancel = QPushButton("取消")
        ok = QPushButton("读取 JSON")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)

    def text(self) -> str:
        return self.editor.toPlainText()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("论文识别归档软件")
        self.resize(980, 760)
        self.pdf_path: Path | None = None
        self.worker: ParseWorker | None = None
        self.validate_worker: ValidateWorker | None = None
        self.prompt_text = ""

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.drop_area = DropArea()
        self.drop_area.pdfDropped.connect(self.set_pdf)
        root.addWidget(self.drop_area)

        controls = QHBoxLayout()
        self.import_button = QPushButton("导入 PDF")
        self.parse_button = QPushButton("导入解析")
        self.web_button = QPushButton("打开 DeepSeek 网页")
        self.paste_button = QPushButton("粘贴 JSON 结果")
        self.archive_button = QPushButton("归档到 archive")
        controls.addWidget(self.import_button)
        controls.addWidget(self.parse_button)
        controls.addWidget(self.web_button)
        controls.addWidget(self.paste_button)
        controls.addWidget(self.archive_button)
        root.addLayout(controls)

        self.status = QLabel("请选择或拖入一篇 PDF。")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.status)
        root.addWidget(self.progress)

        api_box = QGroupBox("模型 API")
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
        api_layout.addWidget(self.validate_key_button, 2, 3)
        root.addWidget(api_box)

        archive_box = QGroupBox("归档目录")
        archive_layout = QHBoxLayout(archive_box)
        self.archive_root_edit = QLineEdit(str(DEFAULT_ARCHIVE_ROOT))
        self.choose_archive_button = QPushButton("选择目录")
        archive_layout.addWidget(self.archive_root_edit)
        archive_layout.addWidget(self.choose_archive_button)
        root.addWidget(archive_box)

        fields_box = QGroupBox("论文参数")
        form = QFormLayout(fields_box)
        self.title_edit = QLineEdit()
        self.authors_edit = QLineEdit()
        self.publisher_edit = QLineEdit()
        self.time_edit = QLineEdit()
        form.addRow("题目", self.title_edit)
        form.addRow("作者", self.authors_edit)
        form.addRow("出版社/期刊/会议", self.publisher_edit)
        form.addRow("发表时间", self.time_edit)
        root.addWidget(fields_box)

        bottom = QGridLayout()
        self.prompt_preview = QTextEdit()
        self.prompt_preview.setReadOnly(True)
        self.prompt_preview.setPlaceholderText("解析后这里会显示发送给模型的提示词。")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("操作日志")
        bottom.addWidget(QLabel("模型提示词"), 0, 0)
        bottom.addWidget(QLabel("日志"), 0, 1)
        bottom.addWidget(self.prompt_preview, 1, 0)
        bottom.addWidget(self.log, 1, 1)
        root.addLayout(bottom)

        self.import_button.clicked.connect(self.choose_pdf)
        self.parse_button.clicked.connect(self.parse_pdf)
        self.web_button.clicked.connect(self.open_deepseek_web)
        self.paste_button.clicked.connect(self.paste_json)
        self.archive_button.clicked.connect(self.archive_current)
        self.choose_archive_button.clicked.connect(self.choose_archive_root)
        self.validate_key_button.clicked.connect(self.validate_api_key)
        self.provider_combo.currentIndexChanged.connect(self.apply_provider_preset)
        self.apply_provider_preset()

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
                margin-top: 12px;
                padding-top: 16px;
            }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px;
            }
            """
        )

    def log_message(self, message: str) -> None:
        self.log.append(message)

    def selected_provider_key(self) -> str:
        return self.provider_combo.currentData()

    def apply_provider_preset(self) -> None:
        key = self.selected_provider_key()
        preset = PROVIDER_PRESETS[key]
        base_url = os.environ.get(preset["base_url_env"], preset["base_url"])
        model = os.environ.get(preset["model_env"], preset["model"])
        api_key = os.environ.get(preset["api_key_env"], "")
        self.base_url_edit.setText(base_url)
        self.model_edit.setText(model)
        self.api_key_edit.setText(api_key)
        self.auth_header_edit.setText(preset["auth_header"])

    def current_api_config(self) -> ApiConfig:
        return ApiConfig(
            provider=self.selected_provider_key(),
            base_url=self.base_url_edit.text().strip(),
            model=self.model_edit.text().strip(),
            api_key=self.api_key_edit.text().strip(),
            auth_header=self.auth_header_edit.text().strip() or "Authorization",
        )

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
        QMessageBox.critical(
            self,
            "验证失败",
            "API Key、API 地址、模型名或认证头可能不匹配。\n\n"
            f"{reason}",
        )

    def choose_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF", "", "PDF 文件 (*.pdf)")
        if path:
            self.set_pdf(path)

    def set_pdf(self, path: str) -> None:
        self.pdf_path = Path(path)
        self.status.setText(f"当前 PDF：{self.pdf_path}")
        self.drop_area.setText(self.pdf_path.name)
        self.log_message(f"已导入：{self.pdf_path}")

    def choose_archive_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择归档根目录", self.archive_root_edit.text()
        )
        if folder:
            self.archive_root_edit.setText(folder)

    def parse_pdf(self) -> None:
        if not self.pdf_path:
            QMessageBox.warning(self, "缺少 PDF", "请先导入或拖入 PDF。")
            return
        self.progress.setRange(0, 0)
        config = self.current_api_config()
        self.status.setText(f"正在提取 PDF 文本并调用 {PROVIDER_PRESETS[config.provider]['name']}...")
        self.worker = ParseWorker(self.pdf_path, config)
        self.worker.promptReady.connect(self.on_prompt_ready)
        self.worker.finished.connect(self.on_parse_finished)
        self.worker.failed.connect(self.on_parse_failed)
        self.worker.start()

    def on_prompt_ready(self, prompt: str) -> None:
        self.prompt_text = prompt
        self.prompt_preview.setPlainText(prompt)

    def on_parse_finished(self, metadata: PaperMetadata) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.apply_metadata(metadata)
        self.status.setText("解析完成，可检查字段后归档。")
        self.log_message("模型 API 解析完成。")

    def on_parse_failed(self, reason: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        try:
            if not self.prompt_text:
                self.prompt_text = build_prompt(extract_pdf_text(self.pdf_path))
                self.prompt_preview.setPlainText(self.prompt_text)
            QApplication.clipboard().setText(self.prompt_text)
        except Exception as exc:
            QMessageBox.critical(self, "解析失败", f"{reason}\n\n同时无法提取提示词：{exc}")
            return
        self.status.setText("API 调用失败，已复制提示词，可改用网页或粘贴 JSON。")
        self.log_message(f"API 解析未完成：{reason}")
        QMessageBox.warning(
            self,
            "API 调用失败",
            "已将识别提示词复制到剪贴板。\n"
            "可检查 API Key/API 地址/模型名，或打开网页手动获取 JSON 后粘贴回来。",
        )

    def open_deepseek_web(self) -> None:
        if self.pdf_path and not self.prompt_text:
            try:
                self.prompt_text = build_prompt(extract_pdf_text(self.pdf_path))
                self.prompt_preview.setPlainText(self.prompt_text)
            except Exception as exc:
                self.log_message(f"提示词生成失败：{exc}")
        if self.prompt_text:
            QApplication.clipboard().setText(self.prompt_text)
            self.log_message("已复制模型提示词到剪贴板。")
        webbrowser.open(DEEPSEEK_WEB_URL)

    def paste_json(self) -> None:
        dialog = JsonPasteDialog(self)
        clipboard_text = QApplication.clipboard().text()
        if clipboard_text.strip():
            dialog.editor.setPlainText(clipboard_text)
        if dialog.exec_() == QDialog.Accepted:
            try:
                metadata = parse_metadata_json(dialog.text())
            except Exception as exc:
                QMessageBox.critical(self, "JSON 读取失败", str(exc))
                return
            self.apply_metadata(metadata)
            self.status.setText("JSON 已读取，可检查字段后归档。")
            self.log_message("已从粘贴 JSON 读取论文参数。")

    def apply_metadata(self, metadata: PaperMetadata) -> None:
        self.title_edit.setText(metadata.title)
        self.authors_edit.setText(metadata.authors)
        self.publisher_edit.setText(metadata.publisher)
        self.time_edit.setText(metadata.published_time)

    def current_metadata(self) -> PaperMetadata:
        return PaperMetadata(
            title=self.title_edit.text().strip(),
            authors=self.authors_edit.text().strip(),
            publisher=self.publisher_edit.text().strip(),
            published_time=self.time_edit.text().strip(),
        )

    def archive_current(self) -> None:
        if not self.pdf_path:
            QMessageBox.warning(self, "缺少 PDF", "请先导入或拖入 PDF。")
            return
        metadata = self.current_metadata()
        if not metadata.title:
            QMessageBox.warning(self, "缺少题目", "请先解析或手动填写论文题目。")
            return
        root = Path(self.archive_root_edit.text().strip() or DEFAULT_ARCHIVE_ROOT)
        try:
            folder = archive_paper(self.pdf_path, root, metadata)
        except Exception as exc:
            QMessageBox.critical(self, "归档失败", str(exc))
            return
        self.status.setText(f"归档完成：{folder}")
        self.log_message(f"已归档到：{folder}")
        QMessageBox.information(self, "归档完成", f"PDF 和 metadata.json 已保存到：\n{folder}")


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
