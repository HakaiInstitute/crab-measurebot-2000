import asyncio
import sys
from pathlib import Path

import qasync
from PySide6.QtWidgets import QApplication, QFileDialog
from tortoise import Tortoise

from crab_measurebot_2000.main import MainWindow


async def _run(app: QApplication) -> bool:
    image_dir = QFileDialog.getExistingDirectory(None, "Open Image Directory")
    if not image_dir:
        return False
    image_dir = Path(image_dir)
    db_path = image_dir / "measurebot.db"
    await Tortoise.init(
        db_url=f"sqlite://{db_path}",
        modules={"models": ["crab_measurebot_2000.main"]},
    )
    await Tortoise.generate_schemas()
    window = MainWindow(image_dir)
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
