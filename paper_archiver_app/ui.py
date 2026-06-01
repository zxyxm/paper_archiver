import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QFileDialog,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QTableWidgetItem,
)

from .api_client import SUPPORTED_DOCUMENT_SUFFIXES, normalize_chat_completions_url
from .archive_store import (
    archive_paper,
    archive_statistics,
    archived_paper_rows,
    find_duplicate,
    organize_archive_payloads,
    pdf_hash,
    read_metadata_file,
    rewrite_archive_tags as rewrite_archive_tags_in_store,
    write_metadata_file,
)
from .config_store import load_config, save_config
from .constants import DEFAULT_ARCHIVE_ROOT, PROVIDER_PRESETS
from .ui_sections import (
    apply_main_window_styles,
    build_main_window,
    connect_main_window_signals,
)
from .metadata import metadata_from_dict
from .models import ApiConfig, PaperItem, PaperMetadata
from .preview import model_interaction_text
from .utils import stringify
from .workers import JournalLookupWorker, ParseWorker, ScholarWorker, ValidateWorker


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

        build_main_window(self)
        connect_main_window_signals(self)
        self.apply_provider_preset()
        self.update_current_view()
        self.load_archive_papers_table()
        apply_main_window_styles(self)

    def create_menu_bar(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction("导入 PDF/CAJ", self.choose_pdf)
        file_menu.addAction("批量导入 PDF/CAJ", self.choose_pdfs)
        file_menu.addAction("打开归档目录", self.open_archive_root)

        window_menu = self.menuBar().addMenu("窗口")
        for index, title in enumerate(("主页面", "论文检索", "设置", "其他")):
            window_menu.addAction(title, lambda _checked=False, page=index: self.set_current_page(page))

        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(
            "关于",
            lambda: QMessageBox.information(
                self,
                "关于",
                "论文识别归档软件\n用于 PDF/CAJ 元数据识别、归档、检索和标签管理。",
            ),
        )

        settings_menu = self.menuBar().addMenu("设置")
        settings_menu.addAction("API 设置", lambda: self.set_current_page(2))
        settings_menu.addAction("归档目录", lambda: self.set_current_page(2))

    def set_current_page(self, index: int) -> None:
        if 0 <= index < self.pages.count():
            self.nav_list.setCurrentRow(index)
            self.pages.setCurrentIndex(index)

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
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择论文文件",
            "",
            "论文文件 (*.pdf *.caj);;PDF 文件 (*.pdf);;CAJ 文件 (*.caj)",
        )
        if path:
            self.set_pdfs([path])

    def choose_pdfs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "批量选择论文文件",
            "",
            "论文文件 (*.pdf *.caj);;PDF 文件 (*.pdf);;CAJ 文件 (*.caj)",
        )
        if paths:
            self.set_pdfs(paths)

    def set_pdfs(self, paths: list[str], tags_by_path: dict[str, list[str]] | None = None) -> None:
        self.sync_current_from_fields()
        tags_by_path = tags_by_path or {}
        added_items = []
        seen = {
            str(item.pdf_path.resolve()) if item.pdf_path.exists() else str(item.pdf_path)
            for item in self.paper_items
        }
        skipped_count = 0
        for path in paths:
            pdf_path = Path(path)
            key = str(pdf_path.resolve()) if pdf_path.exists() else str(pdf_path)
            if key in seen:
                skipped_count += 1
                continue
            seen.add(key)
            item = PaperItem(pdf_path=pdf_path)
            item.metadata.tags = tags_by_path.get(str(pdf_path), [])
            added_items.append(item)
        if not added_items:
            self.status.setText("没有新增文件，重复文件已跳过。")
            return
        first_new_index = len(self.paper_items)
        self.paper_items.extend(added_items)
        self.current_paper_index = first_new_index
        existing_count = 0
        for item in added_items:
            initial_tags = list(item.metadata.tags)
            if self.load_existing_for_item(item):
                existing_count += 1
                item.metadata.tags = self.merge_tags(item.metadata.tags, initial_tags)
                if initial_tags and item.folder:
                    payload = item.json_payload or read_metadata_file(item.folder / "metadata.json")
                    payload.update(asdict(item.metadata))
                    write_metadata_file(item.folder, payload)
                    item.json_payload = payload
        self.refresh_pdf_list()
        self.drop_area.setText(f"已导入 {len(self.paper_items)} 个论文文件")
        notice = f"，其中 {existing_count} 篇已读取旧 JSON" if existing_count else ""
        skipped = f"，跳过重复 {skipped_count} 个" if skipped_count else ""
        self.status.setText(
            f"本次新增 {len(added_items)} 个论文文件{notice}{skipped}，点击“大模型解析”后会优先读取已有归档。"
        )
        self.log_message(f"新增论文文件数量：{len(added_items)}{notice}{skipped}")
        self.update_current_view()

    def handle_dropped_folders(self, paths: list[str]) -> None:
        updated_json_count = 0
        unchanged_json_count = 0
        imported_pdf_count = 0
        tags_by_pdf: dict[str, list[str]] = {}
        pdf_paths: list[str] = []

        for folder_text in paths:
            folder = Path(folder_text)
            tag = folder.name.strip()
            if not tag:
                continue
            metadata_files = sorted(folder.rglob("metadata.json"))
            if metadata_files:
                for metadata_file in metadata_files:
                    try:
                        payload = read_metadata_file(metadata_file)
                    except (OSError, json.JSONDecodeError):
                        continue
                    metadata = metadata_from_dict(payload)
                    if not metadata.is_paper:
                        continue
                    new_tags = self.merge_tags(metadata.tags, [tag])
                    if new_tags == metadata.tags:
                        unchanged_json_count += 1
                        continue
                    payload["tags"] = new_tags
                    write_metadata_file(metadata_file.parent, payload)
                    self.update_loaded_item_from_payload(metadata_file.parent, payload)
                    updated_json_count += 1
                continue

            for suffix in SUPPORTED_DOCUMENT_SUFFIXES:
                for pdf_path in sorted(folder.rglob(f"*{suffix}")):
                    pdf_paths.append(str(pdf_path))
                    tags_by_pdf.setdefault(str(pdf_path), []).append(tag)
                    imported_pdf_count += 1

        if pdf_paths:
            self.set_pdfs(pdf_paths, tags_by_pdf)
        if updated_json_count or unchanged_json_count:
            self.load_archive_papers_table()
            self.update_current_view()

        message_parts = []
        if updated_json_count:
            message_parts.append(f"已给 {updated_json_count} 篇归档论文追加文件夹标签")
        if unchanged_json_count:
            message_parts.append(f"{unchanged_json_count} 篇已包含该标签")
        if imported_pdf_count:
            message_parts.append(f"已导入 {imported_pdf_count} 个论文文件，并预置文件夹标签")
        message = "；".join(message_parts) or "没有找到可处理的论文 JSON、PDF 或 CAJ。"
        self.status.setText(message)
        self.log_message(message)

    def merge_tags(self, current_tags: list[str], new_tags: list[str]) -> list[str]:
        merged: list[str] = []
        for tag in current_tags + new_tags:
            clean_tag = str(tag).strip()
            if clean_tag and clean_tag not in merged:
                merged.append(clean_tag)
        return merged

    def update_loaded_item_from_payload(self, folder: Path, payload: dict) -> None:
        for item in self.paper_items:
            if item.folder and item.folder.resolve() == folder.resolve():
                item.metadata = metadata_from_dict(payload)
                item.json_payload = payload
                item.note = stringify(payload.get("manual_notes"))
                break

    def refresh_pdf_list(self) -> None:
        self.pdf_list.blockSignals(True)
        self.pdf_list.clear()
        for index, item in enumerate(self.paper_items, start=1):
            self.pdf_list.addItem(f"{index}. {item.pdf_path.name}")
        self.pdf_list.setCurrentRow(self.current_paper_index if self.paper_items else -1)
        self.pdf_list.blockSignals(False)

    def remove_current_pdf(self) -> None:
        if not self.paper_items:
            QMessageBox.information(self, "没有文件", "准备区里没有可删除的论文文件。")
            return
        item = self.current_item()
        if not item:
            return
        removed_name = item.pdf_path.name
        del self.paper_items[self.current_paper_index]
        if self.paper_items:
            self.current_paper_index = min(self.current_paper_index, len(self.paper_items) - 1)
        else:
            self.current_paper_index = 0
        self.refresh_pdf_list()
        self.drop_area.setText(
            f"已导入 {len(self.paper_items)} 个论文文件" if self.paper_items else "拖入一个或多个 PDF/CAJ 到这里\n或点击“导入 PDF/CAJ / 批量导入”"
        )
        self.status.setText(f"已从准备区删除：{removed_name}")
        self.log_message(f"已从准备区删除论文文件：{removed_name}")
        self.update_current_view()

    def load_existing_for_item(self, item: PaperItem) -> bool:
        try:
            folder, payload = find_duplicate(
                self.archive_root(), paper_hash=pdf_hash(item.pdf_path), pdf_path=item.pdf_path
            )
        except OSError:
            return False
        if not folder or not payload:
            return False
        item.metadata = metadata_from_dict(payload)
        item.prompt = stringify(payload.get("model_prompt"))
        item.folder = folder
        item.duplicate = True
        item.json_payload = payload
        item.note = stringify(payload.get("manual_notes"))
        item.changed_fields = set()
        return True

    def on_pdf_list_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self.paper_items):
            return
        self.sync_current_from_fields()
        self.current_paper_index = row
        self.update_current_view()

    def choose_archive_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择归档根目录", self.archive_root_edit.text()
        )
        if folder:
            self.archive_root_edit.setText(folder)
            self.load_archive_papers_table()

    def archive_root(self) -> Path:
        return Path(self.archive_root_edit.text().strip() or DEFAULT_ARCHIVE_ROOT)

    def open_archive_root(self) -> None:
        root = self.archive_root()
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root.resolve())))
        self.log_message(f"已打开归档目录：{root}")

    def load_archived_paper_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择已归档论文文件夹", str(self.archive_root())
        )
        if not folder:
            return
        paper_folder = Path(folder)
        metadata_file = paper_folder / "metadata.json"
        if not metadata_file.exists():
            QMessageBox.warning(self, "缺少 metadata.json", "请选择包含 metadata.json 的论文归档文件夹。")
            return
        try:
            payload = read_metadata_file(metadata_file)
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "读取失败", str(exc))
            return
        pdf_path = None
        for key in ("source_pdf", "original_pdf", "pdf_path", "file_path"):
            candidate = stringify(payload.get(key))
            if candidate and Path(candidate).exists() and Path(candidate).suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES:
                pdf_path = Path(candidate)
                break
        if pdf_path is None:
            pdf_candidates = sorted(
                candidate
                for suffix in SUPPORTED_DOCUMENT_SUFFIXES
                for candidate in paper_folder.glob(f"*{suffix}")
            )
            if pdf_candidates:
                pdf_path = pdf_candidates[0]
        if pdf_path is None:
            QMessageBox.warning(self, "缺少论文文件", "该归档文件夹里没有找到可读取的 PDF/CAJ 文件。")
            return
        self.sync_current_from_fields()
        item = PaperItem(
            pdf_path=pdf_path,
            metadata=metadata_from_dict(payload),
            prompt=stringify(payload.get("model_prompt")),
            folder=paper_folder,
            duplicate=True,
            json_payload=payload,
            note=stringify(payload.get("manual_notes")),
        )
        self.paper_items.append(item)
        self.current_paper_index = len(self.paper_items) - 1
        self.refresh_pdf_list()
        self.drop_area.setText(f"已导入 {len(self.paper_items)} 个论文文件")
        self.status.setText(f"已读取归档论文信息：{paper_folder}")
        self.log_message(f"已读取归档论文信息：{paper_folder}")
        self.update_current_view()

    def parse_pdfs(self) -> None:
        if not self.paper_items:
            QMessageBox.warning(self, "缺少论文文件", "请先导入或拖入 PDF/CAJ。")
            return
        self.sync_current_from_fields()
        config = self.current_api_config()
        self.progress.setRange(0, 0)
        self.parse_button.setEnabled(False)
        self.status.setText(
            f"正在解析 {len(self.paper_items)} 个论文文件，调用 {PROVIDER_PRESETS[config.provider]['name']}..."
        )
        initial_tags_by_path = {
            str(item.pdf_path): item.metadata.tags
            for item in self.paper_items
            if item.metadata.tags
        }
        self.worker = ParseWorker(
            [item.pdf_path for item in self.paper_items],
            config,
            self.archive_root(),
            initial_tags_by_path,
        )
        self.worker.itemFinished.connect(self.on_item_finished)
        self.worker.failed.connect(self.on_item_failed)
        self.worker.finished.connect(self.on_parse_finished)
        self.worker.start()

    def on_item_finished(self, index: int, total: int, item: PaperItem) -> None:
        self.paper_items[index - 1] = item
        self.current_paper_index = index - 1
        if item.skipped_model:
            flag = "已有完整归档，跳过大模型读取"
        elif item.duplicate:
            changed_count = len(item.changed_fields)
            flag = f"重复，已读取旧 JSON，并用大模型结果更新 {changed_count} 个字段"
        else:
            flag = "解析完成"
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
        self.status.setText(f"批量处理完成：共 {count} 个论文文件，识别为论文 {paper_count} 个。")
        self.log_message(f"批量处理完成：共 {count} 个论文文件，识别为论文 {paper_count} 个。")
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
            self.apply_metadata(PaperMetadata(), "", set())
            self.show_current_json()
            self.show_current_prompt()
            return
        if self.pdf_list.currentRow() != self.current_paper_index:
            self.pdf_list.blockSignals(True)
            self.pdf_list.setCurrentRow(self.current_paper_index)
            self.pdf_list.blockSignals(False)
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
        self.apply_metadata(metadata, item.note, item.changed_fields)
        self.show_current_json()
        self.show_current_prompt()

    def apply_metadata(
        self, metadata: PaperMetadata, note: str, changed_fields: set[str] | None = None
    ) -> None:
        self.title_edit.setText(metadata.title)
        self.title_zh_edit.setText(metadata.title_zh)
        self.authors_edit.setText(metadata.authors)
        self.authors_zh_edit.setText(metadata.authors_zh)
        self.first_author_edit.setText(metadata.first_author)
        self.corresponding_author_edit.setText(metadata.corresponding_author)
        self.corresponding_affiliation_edit.setText(metadata.corresponding_author_affiliation)
        self.corresponding_affiliation_zh_edit.setText(metadata.corresponding_author_affiliation_zh)
        self.publisher_edit.setText(metadata.publisher)
        self.time_edit.setText(metadata.published_time)
        self.abstract_zh_edit.setPlainText(metadata.abstract_zh)
        self.abstract_en_edit.setPlainText(metadata.abstract_en)
        self.plain_summary_edit.setPlainText(metadata.plain_language_summary)
        self.note_edit.setPlainText(note)
        self.tags_edit.setText(", ".join(metadata.tags))
        self.highlight_changed_fields(changed_fields or set())

    def highlight_changed_fields(self, changed_fields: set[str]) -> None:
        widgets = {
            "title": self.title_edit,
            "title_zh": self.title_zh_edit,
            "authors": self.authors_edit,
            "authors_zh": self.authors_zh_edit,
            "first_author": self.first_author_edit,
            "corresponding_author": self.corresponding_author_edit,
            "corresponding_author_affiliation": self.corresponding_affiliation_edit,
            "corresponding_author_affiliation_zh": self.corresponding_affiliation_zh_edit,
            "publisher": self.publisher_edit,
            "published_time": self.time_edit,
            "abstract_zh": self.abstract_zh_edit,
            "abstract_en": self.abstract_en_edit,
            "plain_language_summary": self.plain_summary_edit,
            "tags": self.tags_edit,
        }
        for field, widget in widgets.items():
            if field in changed_fields:
                widget.setStyleSheet(
                    "background: #fee2e2; border: 1px solid #ef4444; border-radius: 6px; padding: 6px;"
                )
            else:
                widget.setStyleSheet("")

    def current_metadata(self) -> PaperMetadata:
        return PaperMetadata(
            is_paper=True,
            title=self.title_edit.text().strip(),
            title_zh=self.title_zh_edit.text().strip(),
            authors=self.authors_edit.text().strip(),
            authors_zh=self.authors_zh_edit.text().strip(),
            first_author=self.first_author_edit.text().strip(),
            corresponding_author=self.corresponding_author_edit.text().strip(),
            last_author=self.corresponding_author_edit.text().strip(),
            corresponding_author_affiliation=self.corresponding_affiliation_edit.text().strip(),
            corresponding_author_affiliation_zh=self.corresponding_affiliation_zh_edit.text().strip(),
            publisher=self.publisher_edit.text().strip(),
            published_time=self.time_edit.text().strip(),
            abstract_zh=self.abstract_zh_edit.toPlainText().strip(),
            abstract_en=self.abstract_en_edit.toPlainText().strip(),
            plain_language_summary=self.plain_summary_edit.toPlainText().strip(),
            tags=self.current_tags(),
        )

    def current_tags(self) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for raw_tag in self.tags_edit.text().replace("，", ",").replace("、", ",").split(","):
            tag = raw_tag.strip()
            if tag and tag not in seen:
                tags.append(tag)
                seen.add(tag)
        return tags

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
            QMessageBox.warning(self, "缺少论文文件", "请先导入或拖入 PDF/CAJ。")
            return
        self.sync_current_from_fields()
        if not (item.metadata.title or item.metadata.title_zh):
            QMessageBox.warning(self, "缺少题目", "请先解析或手动填写论文题目。")
            return
        try:
            folder, payload, duplicate, changed_fields = archive_paper(
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
        item.changed_fields = changed_fields
        item.note = stringify(payload.get("manual_notes")) or item.note
        if duplicate:
            message = f"检测到重复论文，未新增归档，已读取旧记录和人工笔记：\n{folder}"
        else:
            message = f"原文件和 metadata.json 已保存到：\n{folder}"
        self.status.setText(message.replace("\n", " "))
        self.log_message(message.replace("\n", " "))
        QMessageBox.information(self, "归档结果", message)
        self.update_current_view()
        self.load_archive_papers_table()

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
        self.load_archive_papers_table()

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
        self.load_archive_papers_table()

    def show_current_json(self) -> None:
        item = self.current_item()
        if not item:
            self.json_preview.setPlainText("")
            return
        payload = item.json_payload or asdict(item.metadata)
        payload = dict(payload)
        payload["manual_notes"] = item.note
        payload["model_prompt"] = item.prompt
        if item.changed_fields:
            payload["updated_fields"] = sorted(item.changed_fields)
        self.json_preview.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def show_current_prompt(self) -> None:
        item = self.current_item()
        self.prompt_preview.setPlainText(model_interaction_text(item))

    def show_archive_stats(self) -> None:
        stats = archive_statistics(self.archive_root())
        message = (
            f"归档文件夹：{self.archive_root()}\n"
            f"论文记录：{stats['folders']}\n"
            f"论文文件：{stats['pdfs']}\n"
            f"JSON 文件：{stats['jsons']}\n"
            f"识别为论文：{stats['papers']}\n"
            f"识别为非论文：{stats['non_papers']}\n"
            f"带人工笔记：{stats['notes']}"
        )
        self.log_message(message.replace("\n", "；"))
        QMessageBox.information(self, "归档日志", message)

    def organize_archive_papers(self) -> None:
        root = self.archive_root()
        if not root.exists():
            QMessageBox.information(self, "归档目录不存在", f"找不到归档目录：\n{root}")
            return
        summary = organize_archive_payloads(root)
        for item in self.paper_items:
            if item.folder and (item.folder / "metadata.json").exists():
                payload = read_metadata_file(item.folder / "metadata.json")
                item.metadata = metadata_from_dict(payload)
                item.json_payload = payload
                item.note = stringify(payload.get("manual_notes"))
        self.update_current_view()
        self.load_archive_papers_table()

        changed_parts = [
            f"{name}×{count}"
            for name, count in sorted(summary.changed_fields.items())
        ]
        changed_text = "、".join(changed_parts) if changed_parts else "无"
        message = (
            f"已扫描 {summary.scanned} 个 metadata.json。\n"
            f"已整理 {summary.updated} 个，跳过 {summary.skipped} 个，失败 {summary.failed} 个。\n"
            f"补齐/更新字段：{changed_text}"
        )
        if summary.errors:
            message += "\n\n失败示例：\n" + "\n".join(summary.errors[:5])
        self.status.setText(
            f"归档整理完成：更新 {summary.updated} 个，跳过 {summary.skipped} 个，失败 {summary.failed} 个。"
        )
        self.log_message(message.replace("\n", " "))
        QMessageBox.information(self, "一键整理归档完成", message)

    def load_archive_papers_table(self) -> None:
        rows = archived_paper_rows(self.archive_root())
        tag_counts: dict[str, int] = {}
        for _folder, metadata in rows:
            for tag in metadata.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        all_tags = sorted(tag_counts)
        self.update_archive_tag_filter_options(all_tags)
        self.update_archive_all_tags_list(tag_counts)

        query = self.archive_search_edit.text().strip().lower()
        selected_tag = self.archive_tag_filter_combo.currentData() or ""
        if query or selected_tag:
            rows = [
                (folder, metadata)
                for folder, metadata in rows
                if self.archive_row_matches(metadata, query, selected_tag)
            ]

        self.archive_papers_table.setSortingEnabled(False)
        self.archive_papers_table.setRowCount(len(rows))
        for row_index, (folder, metadata) in enumerate(rows):
            tags_text = ", ".join(metadata.tags)
            values = [
                metadata.publisher or metadata.publisher_zh,
                metadata.published_time,
                metadata.first_author,
                self.first_author_zh(metadata),
                metadata.corresponding_author,
                self.corresponding_author_zh(metadata),
                metadata.corresponding_author_affiliation,
                metadata.corresponding_author_affiliation_zh,
                metadata.title,
                metadata.title_zh,
                tags_text,
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.UserRole, str(folder))
                self.archive_papers_table.setItem(row_index, column_index, item)
        self.archive_papers_count_label.setText(f"共 {len(rows)} 篇论文")
        self.archive_papers_table.setSortingEnabled(True)
        self.archive_papers_table.resizeRowsToContents()
        self.log_message(f"已刷新归档论文表格：{len(rows)} 篇")

    def first_author_zh(self, metadata: PaperMetadata) -> str:
        return self.author_zh_at(metadata, 0)

    def corresponding_author_zh(self, metadata: PaperMetadata) -> str:
        author = metadata.corresponding_author.strip()
        if not author:
            return ""
        authors = self.split_author_names(metadata.authors)
        authors_zh = self.split_author_names(metadata.authors_zh)
        if not authors or not authors_zh:
            return ""
        for index, candidate in enumerate(authors):
            if candidate == author and index < len(authors_zh):
                return authors_zh[index]
        return ""

    def author_zh_at(self, metadata: PaperMetadata, index: int) -> str:
        authors_zh = self.split_author_names(metadata.authors_zh)
        return authors_zh[index] if index < len(authors_zh) else ""

    def split_author_names(self, text: str) -> list[str]:
        separators_normalized = (
            text.replace("，", ",")
            .replace("、", ",")
            .replace("；", ",")
            .replace(";", ",")
        )
        return [part.strip() for part in separators_normalized.split(",") if part.strip()]

    def update_archive_tag_filter_options(self, tags: list[str]) -> None:
        current_tag = self.archive_tag_filter_combo.currentData() or ""
        self.archive_tag_filter_combo.blockSignals(True)
        self.archive_tag_filter_combo.clear()
        self.archive_tag_filter_combo.addItem("全部标签", "")
        for tag in tags:
            self.archive_tag_filter_combo.addItem(tag, tag)
        index = self.archive_tag_filter_combo.findData(current_tag)
        self.archive_tag_filter_combo.setCurrentIndex(index if index >= 0 else 0)
        self.archive_tag_filter_combo.blockSignals(False)

    def update_archive_all_tags_list(self, tag_counts: dict[str, int]) -> None:
        current_tag = ""
        current_item = self.archive_all_tags_list.currentItem()
        if current_item:
            current_tag = current_item.data(Qt.UserRole) or ""
        self.archive_all_tags_list.blockSignals(True)
        self.archive_all_tags_list.clear()
        selected_row = -1
        for row, tag in enumerate(sorted(tag_counts)):
            item = QListWidgetItem(f"{tag} ({tag_counts[tag]})")
            item.setData(Qt.UserRole, tag)
            item.setToolTip(tag)
            self.archive_all_tags_list.addItem(item)
            if tag == current_tag:
                selected_row = row
        if selected_row >= 0:
            self.archive_all_tags_list.setCurrentRow(selected_row)
        self.archive_all_tags_list.blockSignals(False)

    def on_archive_tag_clicked(self, item: QListWidgetItem) -> None:
        tag = item.data(Qt.UserRole) or ""
        self.archive_tag_new_name_edit.setText(tag)
        index = self.archive_tag_filter_combo.findData(tag)
        if index >= 0:
            self.archive_tag_filter_combo.setCurrentIndex(index)

    def rename_selected_archive_tag(self) -> None:
        item = self.archive_all_tags_list.currentItem()
        if not item:
            QMessageBox.information(self, "未选择标签", "请先在“所有标签”里选择一个标签。")
            return
        old_tag = str(item.data(Qt.UserRole) or "").strip()
        new_tag = self.archive_tag_new_name_edit.text().strip()
        if not old_tag:
            return
        if not new_tag:
            QMessageBox.warning(self, "缺少新标签名", "请输入新的标签名。")
            return

        changed_count = self.rewrite_archive_tags(old_tag, new_tag)
        if changed_count:
            self.log_message(f"已将标签“{old_tag}”改为“{new_tag}”，同名标签已自动合并：{changed_count} 篇")
            self.status.setText(f"已编辑标签：{old_tag} -> {new_tag}，影响 {changed_count} 篇论文")
        else:
            self.status.setText("标签未发生变化。")
        self.archive_tag_new_name_edit.setText(new_tag)
        self.load_archive_papers_table()

    def merge_archive_same_name_tags(self) -> None:
        changed_count = self.rewrite_archive_tags()
        if changed_count:
            self.log_message(f"已合并同名标签：{changed_count} 篇")
            self.status.setText(f"已合并同名标签，更新 {changed_count} 篇论文")
        else:
            self.status.setText("没有需要合并的同名标签。")
        self.load_archive_papers_table()

    def rewrite_archive_tags(self, old_tag: str = "", new_tag: str = "") -> int:
        changed_count = rewrite_archive_tags_in_store(
            self.archive_root(), old_tag, new_tag
        )
        if changed_count:
            for item in self.paper_items:
                if item.folder and (item.folder / "metadata.json").exists():
                    payload = read_metadata_file(item.folder / "metadata.json")
                    self.update_loaded_item_from_payload(item.folder, payload)
        return changed_count

    def archive_row_matches(
        self, metadata: PaperMetadata, query: str, selected_tag: str
    ) -> bool:
        if selected_tag and selected_tag not in metadata.tags:
            return False
        if not query:
            return True
        searchable = " ".join(
            [
                metadata.publisher,
                metadata.publisher_zh,
                metadata.published_time,
                metadata.first_author,
                metadata.corresponding_author,
                metadata.corresponding_author_affiliation,
                metadata.corresponding_author_affiliation_zh,
                metadata.title,
                metadata.title_zh,
                metadata.authors,
                metadata.authors_zh,
                " ".join(metadata.tags),
            ]
        ).lower()
        return query in searchable

    def clear_archive_filters(self) -> None:
        self.archive_search_edit.clear()
        self.archive_tag_filter_combo.setCurrentIndex(0)
        self.load_archive_papers_table()

    def open_selected_archive_paper(self) -> None:
        row = self.archive_papers_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "未选择论文", "请先在表格中选择一篇论文。")
            return
        item = self.archive_papers_table.item(row, 0)
        if not item:
            return
        folder = Path(item.data(Qt.UserRole) or "")
        if not folder.exists():
            QMessageBox.warning(self, "文件夹不存在", f"找不到归档文件夹：\n{folder}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

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
