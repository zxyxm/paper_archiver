from PyQt5.QtWidgets import QHBoxLayout, QListWidget, QStackedWidget, QWidget

from .main_page import build_main_page
from .other_page import build_other_page
from .paper_search_page import build_paper_search_page
from .settings_page import build_settings_page


def build_main_window(self) -> None:
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
    build_main_page(self, main_tab)
    build_paper_search_page(self, journal_tab)
    build_settings_page(self, settings_tab)
    build_other_page(self, other_tab)
