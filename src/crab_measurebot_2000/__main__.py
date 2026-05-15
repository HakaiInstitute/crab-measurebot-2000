import asyncio
import sys
from pathlib import Path

import qasync
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
)
from tortoise import Tortoise

from crab_measurebot_2000.app import MainWindow


class WelcomeDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CrabMeasureBot 2000")
        self.setMinimumWidth(440)
        self._selected_dir: Path | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 32, 32, 32)

        title = QLabel("CrabMeasureBot 2000")
        title.setStyleSheet("font-size:20px;font-weight:bold;color:#fff;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        desc = QLabel(
            "Open a folder containing images to start measuring.\n"
            "Measurements are saved automatically in the folder."
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#aaa;font-size:12px;")

        btn = QPushButton("Open Image Directory…")
        btn.setStyleSheet(
            "background:#1a2a1a;border:1px solid #2a4a2a;color:#00c864;"
            "font-size:12px;padding:10px 20px;border-radius:4px;"
        )
        btn.clicked.connect(self._pick_directory)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(btn)

        self.setStyleSheet("background:#111;")

    def _pick_directory(self):
        path = QFileDialog.getExistingDirectory(
            self, "Open Image Directory", str(Path.home())
        )
        if path:
            self._selected_dir = Path(path)
            self.accept()

    @property
    def selected_dir(self) -> Path | None:
        return self._selected_dir


async def _run(app: QApplication) -> bool:
    dialog = WelcomeDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    image_dir = dialog.selected_dir
    assert image_dir is not None
    db_path = image_dir / "measurebot.db"
    await Tortoise.init(
        db_url=f"sqlite://{db_path}",
        modules={"models": ["crab_measurebot_2000.app"]},
        _enable_global_fallback=True,
    )
    await Tortoise.generate_schemas()
    window = MainWindow(image_dir)
    window.destroyed.connect(app.quit)
    window.show()
    return True


def run() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        started = loop.run_until_complete(_run(app))
        if started:
            loop.run_forever()
            loop.run_until_complete(Tortoise.close_connections())


if __name__ == "__main__":
    run()
