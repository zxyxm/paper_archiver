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

from ..constants import DEFAULT_ARCHIVE_ROOT, PROVIDER_PRESETS


def build_settings_page(self, settings_tab: QWidget) -> None:
    settings_layout = QVBoxLayout(settings_tab)
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
