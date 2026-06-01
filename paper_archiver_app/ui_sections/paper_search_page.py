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

def build_paper_search_page(self, journal_tab: QWidget) -> None:
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
    self.archive_papers_organize_button = QPushButton("一键整理归档")
    self.archive_papers_open_button = QPushButton("打开选中论文文件夹")
    self.archive_papers_count_label = QLabel("共 0 篇论文")
    archive_controls.addWidget(self.archive_papers_refresh_button)
    archive_controls.addWidget(self.archive_papers_organize_button)
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
