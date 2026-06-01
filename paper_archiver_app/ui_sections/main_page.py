from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .drop_area import DropArea


def build_main_page(self, main_tab: QWidget) -> None:
    root = QVBoxLayout(main_tab)
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
