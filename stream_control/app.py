from __future__ import annotations

import asyncio
import sys

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from stream_control.core.config import ConfigStore
from stream_control.core.logging_setup import configure_app_logging
from stream_control.core.paths import AppPaths
from stream_control.ui.main_window import MainWindow
from stream_control.ui.theme import APP_STYLESHEET


def main() -> int:
    configure_app_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Stream Control")
    app.setOrganizationName("StreamControl")
    app.setStyleSheet(APP_STYLESHEET)

    paths = AppPaths.build()
    config_store = ConfigStore(paths)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow(config_store=config_store, app_paths=paths)
    window.showMaximized()

    app.aboutToQuit.connect(window.shutdown)

    with loop:
        loop.run_forever()

    return 0
