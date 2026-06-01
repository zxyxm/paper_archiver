def apply_main_window_styles(self) -> None:
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
