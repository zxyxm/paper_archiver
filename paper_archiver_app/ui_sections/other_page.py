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

def build_other_page(self, other_tab: QWidget) -> None:
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
