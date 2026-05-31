import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .api_client import SUPPORTED_DOCUMENT_SUFFIXES, normalize_chat_completions_url
from .archive_store import (
    archive_paper,
    archive_statistics,
    archived_paper_rows,
    find_duplicate,
    pdf_hash,
    read_metadata_file,
    write_metadata_file,
)
from .config_store import load_config, save_config
from .constants import DEFAULT_ARCHIVE_ROOT, PROVIDER_PRESETS
from .metadata import metadata_from_dict
from .models import ApiConfig, PaperItem, PaperMetadata
from .preview import model_interaction_text
from .utils import stringify
from .workers import JournalLookupWorker, ParseWorker, ScholarWorker, ValidateWorker


class DropArea(QLabel):
    pdfDropped = pyqtSignal(list)
    folderDropped = pyqtSignal(list)

    def __init__(self):
        super().__init__("拖入一个或多个 PDF/CAJ 或文件夹到这里\n文件夹名会作为论文标签写入")
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
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        if any(Path(path).suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES or Path(path).is_dir() for path in paths):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        pdf_paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES
        ]
        folder_paths = [path for path in paths if Path(path).is_dir()]
        if pdf_paths:
            self.pdfDropped.emit(pdf_paths)
        if folder_paths:
            self.folderDropped.emit(folder_paths)
        if pdf_paths or folder_paths:
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

        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.setCentralWidget(shell)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setFixedWidth(168)
        self.nav_list.addItems(["主页面", "论文检索", "设置", "其他"])
        self.nav_list.setCurrentRow(0)
        shell_layout.addWidget(self.nav_list)

        self.pages = QStackedWidget()
        shell_layout.addWidget(self.pages, 1)

        main_tab = QWidget()
        settings_tab = QWidget()
        other_tab = QWidget()
        journal_tab = QWidget()
        self.pages.addWidget(main_tab)
        self.pages.addWidget(journal_tab)
        self.pages.addWidget(settings_tab)
        self.pages.addWidget(other_tab)
        self.nav_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.create_menu_bar()

        root = QVBoxLayout(main_tab)
        settings_layout = QVBoxLayout(settings_tab)
        self.drop_area = DropArea()
        self.drop_area.pdfDropped.connect(self.set_pdfs)
        self.drop_area.folderDropped.connect(self.handle_dropped_folders)
        root.addWidget(self.drop_area)
        self.pdf_list = QListWidget()
        self.pdf_list.setMaximumHeight(120)
        self.pdf_list.currentRowChanged.connect(self.on_pdf_list_row_changed)
        root.addWidget(self.pdf_list)

        controls = QHBoxLayout()
        self.import_button = QPushButton("导入 PDF/CAJ")
        self.batch_import_button = QPushButton("批量导入 PDF/CAJ")
        self.remove_pdf_button = QPushButton("删除当前文件")
        self.parse_button = QPushButton("大模型解析")
        self.parse_button.setObjectName("parseButton")
        self.scholar_button = QPushButton("Google Scholar 主页")
        controls.addWidget(self.import_button)
        controls.addWidget(self.batch_import_button)
        controls.addWidget(self.remove_pdf_button)
        controls.addWidget(self.parse_button)
        controls.addWidget(self.scholar_button)
        controls.addStretch()
        root.addLayout(controls)

        self.status = QLabel("请选择或拖入 PDF/CAJ。")
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
        settings_layout.addWidget(api_box)

        archive_box = QGroupBox("归档目录")
        archive_layout = QHBoxLayout(archive_box)
        self.archive_root_edit = QLineEdit(str(DEFAULT_ARCHIVE_ROOT))
        self.choose_archive_button = QPushButton("选择目录")
        self.open_archive_button = QPushButton("打开目录")
        self.open_paper_folder_button = QPushButton("打开论文文件夹")
        self.archive_stats_button = QPushButton("日志")
        archive_layout.addWidget(self.archive_root_edit)
        archive_layout.addWidget(self.choose_archive_button)
        archive_layout.addWidget(self.open_archive_button)
        archive_layout.addWidget(self.open_paper_folder_button)
        archive_layout.addWidget(self.archive_stats_button)
        settings_layout.addWidget(archive_box)
        settings_layout.addStretch()

        edit_scroll = QScrollArea()
        edit_scroll.setWidgetResizable(True)
        edit_scroll.setFrameShape(QFrame.NoFrame)
        edit_panel = QWidget()
        edit_layout = QVBoxLayout(edit_panel)
        edit_scroll.setWidget(edit_panel)
        root.addWidget(edit_scroll, 1)

        fields_box = QGroupBox("论文信息编辑")
        form = QFormLayout(fields_box)
        self.title_edit = QLineEdit()
        self.title_zh_edit = QLineEdit()
        self.authors_edit = QLineEdit()
        self.authors_zh_edit = QLineEdit()
        self.first_author_edit = QLineEdit()
        self.corresponding_author_edit = QLineEdit()
        self.corresponding_affiliation_edit = QLineEdit()
        self.corresponding_affiliation_zh_edit = QLineEdit()
        self.publisher_edit = QLineEdit()
        self.time_edit = QLineEdit()
        self.save_info_button = QPushButton("保存论文信息")
        author_role_layout = QHBoxLayout()
        author_role_layout.addWidget(QLabel("一作"))
        author_role_layout.addWidget(self.first_author_edit, 1)
        author_role_layout.addWidget(QLabel("通讯作者"))
        author_role_layout.addWidget(self.corresponding_author_edit, 1)
        publish_info_layout = QHBoxLayout()
        publish_info_layout.addWidget(QLabel("出版社/期刊/会议"))
        publish_info_layout.addWidget(self.publisher_edit, 2)
        publish_info_layout.addWidget(QLabel("发表时间"))
        publish_info_layout.addWidget(self.time_edit, 1)
        form.addRow("原文题目", self.title_edit)
        form.addRow("中文题目", self.title_zh_edit)
        form.addRow("作者", self.authors_edit)
        form.addRow("作者中文", self.authors_zh_edit)
        form.addRow("作者信息", author_role_layout)
        form.addRow("通讯作者单位", self.corresponding_affiliation_edit)
        form.addRow("通讯作者单位中文", self.corresponding_affiliation_zh_edit)
        form.addRow("", publish_info_layout)
        form.addRow("", self.save_info_button)
        edit_layout.addWidget(fields_box)

        summary_box = QGroupBox("摘要与笔记")
        summary_layout = QGridLayout(summary_box)
        self.abstract_zh_edit = QTextEdit()
        self.abstract_en_edit = QTextEdit()
        self.plain_summary_edit = QTextEdit()
        self.note_edit = QTextEdit()
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("输入自定义标签，用逗号分隔，例如 综述, 代谢组, 待精读")
        self.save_note_button = QPushButton("保存人工笔记")
        for editor in (
            self.abstract_zh_edit,
            self.abstract_en_edit,
            self.plain_summary_edit,
            self.note_edit,
        ):
            editor.setMinimumHeight(170)
        self.abstract_tabs = QTabWidget()
        self.abstract_tabs.addTab(self.abstract_zh_edit, "中文摘要")
        self.abstract_tabs.addTab(self.abstract_en_edit, "English Abstract")
        self.abstract_tabs.setCurrentIndex(0)
        summary_layout.addWidget(self.abstract_tabs, 0, 0, 1, 2)
        summary_layout.addWidget(QLabel("标签"), 1, 0)
        summary_layout.addWidget(self.tags_edit, 1, 1)
        summary_layout.addWidget(QLabel("大白话"), 2, 0)
        summary_layout.addWidget(QLabel("人工笔记"), 2, 1)
        summary_layout.addWidget(self.plain_summary_edit, 3, 0)
        summary_layout.addWidget(self.note_edit, 3, 1)
        summary_layout.addWidget(self.save_note_button, 4, 1)
        edit_layout.addWidget(summary_box)
        edit_layout.addStretch()

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

        for widget in (
            self.journal_query_edit,
            self.journal_use_current_button,
            self.journal_search_button,
            self.journal_open_button,
            self.journal_result,
        ):
            widget.hide()

        archive_controls = QHBoxLayout()
        self.archive_papers_refresh_button = QPushButton("刷新归档论文")
        self.archive_papers_open_button = QPushButton("打开选中论文文件夹")
        self.archive_papers_count_label = QLabel("共 0 篇论文")
        archive_controls.addWidget(self.archive_papers_refresh_button)
        archive_controls.addWidget(self.archive_papers_open_button)
        archive_controls.addWidget(self.archive_papers_count_label)
        archive_controls.addStretch()
        journal_layout.addLayout(archive_controls)

        archive_filter_layout = QHBoxLayout()
        self.archive_search_edit = QLineEdit()
        self.archive_search_edit.setPlaceholderText("检索题目、中文题目、作者、期刊、通讯作者单位或标签")
        self.archive_tag_filter_combo = QComboBox()
        self.archive_tag_filter_combo.addItem("全部标签", "")
        self.archive_filter_clear_button = QPushButton("清空筛选")
        archive_filter_layout.addWidget(QLabel("检索"))
        archive_filter_layout.addWidget(self.archive_search_edit, 1)
        archive_filter_layout.addWidget(QLabel("标签"))
        archive_filter_layout.addWidget(self.archive_tag_filter_combo)
        archive_filter_layout.addWidget(self.archive_filter_clear_button)
        journal_layout.addLayout(archive_filter_layout)

        archive_tags_box = QGroupBox("所有标签")
        archive_tags_layout = QGridLayout(archive_tags_box)
        self.archive_all_tags_list = QListWidget()
        self.archive_all_tags_list.setObjectName("archiveTagList")
        self.archive_all_tags_list.setMaximumHeight(118)
        self.archive_tag_new_name_edit = QLineEdit()
        self.archive_tag_new_name_edit.setPlaceholderText("输入新标签名，和已有标签同名会自动合并")
        self.archive_tag_rename_button = QPushButton("一键编辑标签")
        self.archive_tag_merge_button = QPushButton("合并同名标签")
        archive_tags_layout.addWidget(self.archive_all_tags_list, 0, 0, 3, 1)
        archive_tags_layout.addWidget(QLabel("新标签名"), 0, 1)
        archive_tags_layout.addWidget(self.archive_tag_new_name_edit, 1, 1)
        archive_tag_buttons = QHBoxLayout()
        archive_tag_buttons.addWidget(self.archive_tag_rename_button)
        archive_tag_buttons.addWidget(self.archive_tag_merge_button)
        archive_tag_buttons.addStretch()
        archive_tags_layout.addLayout(archive_tag_buttons, 2, 1)
        archive_tags_layout.setColumnStretch(0, 1)
        archive_tags_layout.setColumnStretch(1, 2)
        journal_layout.addWidget(archive_tags_box)

        self.archive_papers_table = QTableWidget(0, 11)
        self.archive_papers_table.setHorizontalHeaderLabels(
            [
                "期刊",
                "发表时间",
                "第一作者",
                "第一作者中文",
                "通讯作者",
                "通讯作者中文",
                "通讯作者单位",
                "通讯作者单位中文",
                "题目",
                "中文题目",
                "标签",
            ]
        )
        self.archive_papers_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.archive_papers_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.archive_papers_table.setSelectionMode(QTableWidget.SingleSelection)
        self.archive_papers_table.verticalHeader().setVisible(False)
        self.archive_papers_table.horizontalHeader().setStretchLastSection(True)
        self.archive_papers_table.setSortingEnabled(True)
        self.archive_papers_table.setColumnWidth(0, 230)
        self.archive_papers_table.setColumnWidth(1, 110)
        self.archive_papers_table.setColumnWidth(2, 160)
        self.archive_papers_table.setColumnWidth(3, 160)
        self.archive_papers_table.setColumnWidth(4, 160)
        self.archive_papers_table.setColumnWidth(5, 160)
        self.archive_papers_table.setColumnWidth(6, 260)
        self.archive_papers_table.setColumnWidth(7, 260)
        self.archive_papers_table.setColumnWidth(8, 320)
        self.archive_papers_table.setColumnWidth(9, 320)
        self.archive_papers_table.setColumnWidth(10, 180)
        journal_layout.addWidget(self.archive_papers_table)

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
        self.remove_pdf_button.clicked.connect(self.remove_current_pdf)
        self.parse_button.clicked.connect(self.parse_pdfs)
        self.choose_archive_button.clicked.connect(self.choose_archive_root)
        self.open_archive_button.clicked.connect(self.open_archive_root)
        self.open_paper_folder_button.clicked.connect(self.load_archived_paper_folder)
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
        self.archive_papers_refresh_button.clicked.connect(self.load_archive_papers_table)
        self.archive_papers_open_button.clicked.connect(self.open_selected_archive_paper)
        self.archive_search_edit.textChanged.connect(self.load_archive_papers_table)
        self.archive_tag_filter_combo.currentIndexChanged.connect(self.load_archive_papers_table)
        self.archive_filter_clear_button.clicked.connect(self.clear_archive_filters)
        self.archive_all_tags_list.itemClicked.connect(self.on_archive_tag_clicked)
        self.archive_tag_rename_button.clicked.connect(self.rename_selected_archive_tag)
        self.archive_tag_merge_button.clicked.connect(self.merge_archive_same_name_tags)
        self.archive_papers_table.cellDoubleClicked.connect(
            lambda _row, _column: self.open_selected_archive_paper()
        )
        self.apply_provider_preset()
        self.update_current_view()
        self.load_archive_papers_table()

        self.setStyleSheet(
            """
            QMainWindow { background: #ffffff; }
            QMenuBar {
                background: #f8fafc;
                border-bottom: 1px solid #e5e7eb;
                padding: 3px 8px;
            }
            QMenuBar::item {
                padding: 6px 10px;
                background: transparent;
            }
            QMenuBar::item:selected { background: #e5e7eb; border-radius: 4px; }
            QListWidget#navList {
                border: none;
                border-right: 1px solid #e5e7eb;
                background: #f8fafc;
                padding: 8px;
            }
            QListWidget#navList::item {
                padding: 10px 12px;
                border-radius: 6px;
                color: #334155;
            }
            QListWidget#navList::item:selected {
                background: #e2e8f0;
                color: #0f172a;
            }
            QListWidget#archiveTagList {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background: #ffffff;
            }
            QListWidget#archiveTagList::item { padding: 5px 8px; }
            QPushButton {
                padding: 8px 12px;
                border-radius: 6px;
                border: 1px solid #cbd5e1;
                background: #ffffff;
            }
            QPushButton:hover { background: #f1f5f9; }
            QPushButton#parseButton {
                color: #ffffff;
                background: #2563eb;
                border: 1px solid #1d4ed8;
                font-weight: 700;
            }
            QPushButton#parseButton:hover { background: #1d4ed8; }
            QPushButton#parseButton:disabled {
                color: #dbeafe;
                background: #93c5fd;
                border-color: #93c5fd;
            }
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
        changed_count = 0
        for folder, _metadata in archived_paper_rows(self.archive_root()):
            payload = read_metadata_file(folder / "metadata.json")
            raw_value = payload.get("tags", [])
            if isinstance(raw_value, list):
                raw_tags = raw_value
            else:
                raw_tags = str(raw_value or "").replace("，", ",").replace("、", ",").split(",")
            old_tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
            rewritten_tags: list[str] = []
            for clean_tag in old_tags:
                replacement = new_tag if old_tag and clean_tag == old_tag else clean_tag
                if replacement and replacement not in rewritten_tags:
                    rewritten_tags.append(replacement)
            if rewritten_tags == old_tags:
                continue
            payload["tags"] = rewritten_tags
            write_metadata_file(folder, payload)
            self.update_loaded_item_from_payload(folder, payload)
            changed_count += 1
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
