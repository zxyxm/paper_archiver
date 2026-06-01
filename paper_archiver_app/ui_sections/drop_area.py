from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QLabel

from ..api_client import SUPPORTED_DOCUMENT_SUFFIXES


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
