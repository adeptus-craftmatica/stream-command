from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from stream_control.core.config import ConfigStore
from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths
from stream_control.plugins.builtin import build_builtin_plugins
from stream_control.plugins.context import PluginContext
from stream_control.plugins.host import PluginHost


class MainWindow(QMainWindow):
    def __init__(self, config_store: ConfigStore, app_paths: AppPaths) -> None:
        super().__init__()
        self._config_store = config_store
        self._app_paths = app_paths
        self.config: AppConfig = config_store.load()

        self.setWindowTitle("Stream Control")
        self.resize(1460, 920)
        self._build_shell()

        self.plugin_context = PluginContext(
            app_config=self.config,
            app_paths=app_paths,
            qt_parent=self,
            save_callback=self._save_config,
        )
        self.plugin_host = PluginHost(self.plugin_context, build_builtin_plugins())
        self.plugin_host.activate_plugins()
        self._populate_navigation()
        self._save_config()

    def _build_shell(self) -> None:
        central = QWidget(self)
        central.setObjectName("appShell")
        self.setCentralWidget(central)

        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame(central)
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(250)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(24, 24, 24, 24)
        sidebar_layout.setSpacing(18)

        brand_card = QFrame(sidebar)
        brand_card.setObjectName("brandCard")
        brand_layout = QVBoxLayout(brand_card)
        brand_layout.setContentsMargins(18, 18, 18, 18)
        brand_layout.setSpacing(8)

        brand_pill = QLabel("PLUGIN-FIRST CONTROL", brand_card)
        brand_pill.setObjectName("brandPill")
        brand = QLabel("Stream Control", brand_card)
        brand.setObjectName("brandTitle")
        subtitle = QLabel("Cross-platform stream command center", brand_card)
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        brand_layout.addWidget(brand_pill)
        brand_layout.addWidget(brand)
        brand_layout.addWidget(subtitle)
        sidebar_layout.addWidget(brand_card)

        self.nav = QListWidget(sidebar)
        self.nav.setObjectName("sidebarNav")
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.nav.currentRowChanged.connect(self._change_page)
        sidebar_layout.addWidget(self.nav, 1)

        config_hint = QLabel(f"Config: {self._app_paths.config_file}", sidebar)
        config_hint.setObjectName("sidebarMeta")
        config_hint.setWordWrap(True)
        sidebar_layout.addWidget(config_hint)

        content = QWidget(central)
        content.setObjectName("contentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(0)

        self.stack = QStackedWidget(content)
        self.stack.setObjectName("contentStack")
        content_layout.addWidget(self.stack)

        shell.addWidget(sidebar)
        shell.addWidget(content, 1)

    def _populate_navigation(self) -> None:
        for page in self.plugin_host.navigation_pages():
            item = QListWidgetItem(page.title)
            item.setData(Qt.ItemDataRole.UserRole, page.plugin_id)
            self.nav.addItem(item)
            self.stack.addWidget(self._wrap_page(page.widget))
        if self.nav.count():
            self.nav.setCurrentRow(0)

    def _change_page(self, index: int) -> None:
        if index >= 0:
            self.stack.setCurrentIndex(index)

    def _save_config(self) -> None:
        self._config_store.save(self.config)

    def shutdown(self) -> None:
        self.plugin_host.shutdown()

    def _wrap_page(self, widget: QWidget) -> QScrollArea:
        widget.setObjectName(f"{widget.objectName() or 'pluginPage'}")
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        container = QWidget(self)
        container.setObjectName("pageViewport")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(widget, 1)

        scroll = QScrollArea(self.stack)
        scroll.setObjectName("pageScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(container)
        return scroll
