from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncClose

from stream_control.core.config import ConfigStore
from stream_control.core.credentials import CredentialStore
from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths
from stream_control.core.platform import is_macos
from stream_control.plugins.builtin import build_builtin_plugins
from stream_control.plugins.context import PluginContext
from stream_control.plugins.host import PluginHost
from stream_control.ui.widgets.common import configure_readonly_line


class MainWindow(QMainWindow):
    def __init__(self, config_store: ConfigStore, app_paths: AppPaths) -> None:
        super().__init__()
        self._config_store = config_store
        self._app_paths = app_paths
        self.config: AppConfig = config_store.load()
        self._did_shutdown = False
        self._hotkeys_paused_for_text_entry = False

        self.setWindowTitle("Stream Control")
        self.setMinimumSize(1120, 760)
        self.resize(1360, 860)
        self._build_shell()

        self.plugin_context = PluginContext(
            app_config=self.config,
            app_paths=app_paths,
            qt_parent=self,
            save_callback=self._save_config,
            credential_store=CredentialStore(),
        )
        self.plugin_host = PluginHost(self.plugin_context, build_builtin_plugins())
        self.plugin_host.activate_plugins()
        self._install_hotkey_focus_guard()
        self._install_hotkey_activity_guard()
        self._populate_navigation()
        self._restore_window_state()
        self._save_config()

    def _build_shell(self) -> None:
        central = QWidget(self)
        central.setObjectName("appShell")
        self.setCentralWidget(central)

        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        self.splitter = splitter
        shell.addWidget(splitter, 1)

        sidebar = QFrame(central)
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(228)
        sidebar.setMaximumWidth(320)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(20, 20, 20, 20)
        sidebar_layout.setSpacing(18)

        brand_card = QFrame(sidebar)
        brand_card.setObjectName("brandCard")
        brand_layout = QVBoxLayout(brand_card)
        brand_layout.setContentsMargins(18, 18, 18, 18)
        brand_layout.setSpacing(8)

        brand_pill = QLabel("Plugin-first control", brand_card)
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

        config_label = QLabel("Config file", sidebar)
        config_label.setObjectName("sidebarMeta")
        sidebar_layout.addWidget(config_label)

        config_path = QLineEdit(str(self._app_paths.config_file), sidebar)
        config_path.setObjectName("sidebarPath")
        config_path.setReadOnly(True)
        config_path.setToolTip(str(self._app_paths.config_file))
        configure_readonly_line(config_path)
        sidebar_layout.addWidget(config_path)

        content = QWidget(central)
        content.setObjectName("contentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(0)

        self.stack = QStackedWidget(content)
        self.stack.setObjectName("contentStack")
        content_layout.addWidget(self.stack)

        splitter.addWidget(sidebar)
        splitter.addWidget(content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 1110])

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

    def _install_hotkey_focus_guard(self) -> None:
        if not is_macos():
            return
        hotkey_service = self.plugin_context.get_service("hotkeys.service")
        if hotkey_service is None or not hotkey_service.runtime_hotkeys_supported():
            return
        app = QApplication.instance()
        if app is None:
            return
        app.focusChanged.connect(self._handle_focus_change)

    def _install_hotkey_activity_guard(self) -> None:
        if not is_macos():
            return
        hotkey_service = self.plugin_context.get_service("hotkeys.service")
        if hotkey_service is None or not hotkey_service.runtime_hotkeys_supported():
            return
        app = QApplication.instance()
        if app is None:
            return
        app.applicationStateChanged.connect(self._handle_application_state_change)
        self._handle_application_state_change(app.applicationState())

    def _handle_application_state_change(self, state: Qt.ApplicationState) -> None:
        if not is_macos():
            return
        hotkey_service = self.plugin_context.get_service("hotkeys.service")
        if hotkey_service is None:
            return
        if state == Qt.ApplicationState.ApplicationActive:
            hotkey_service.suspend("foreground_app")
            return
        hotkey_service.resume("foreground_app")

    def _handle_focus_change(self, _old: QWidget | None, now: QWidget | None) -> None:
        if not is_macos():
            return
        hotkey_service = self.plugin_context.get_service("hotkeys.service")
        if hotkey_service is None:
            return
        should_pause = self._should_pause_hotkeys_for_widget(now)
        if should_pause and not self._hotkeys_paused_for_text_entry:
            hotkey_service.suspend("text_entry")
            self._hotkeys_paused_for_text_entry = True
            return
        if not should_pause and self._hotkeys_paused_for_text_entry:
            hotkey_service.resume("text_entry")
            self._hotkeys_paused_for_text_entry = False

    def _should_pause_hotkeys_for_widget(self, widget: QWidget | None) -> bool:
        if widget is None or widget.window() is not self:
            return False
        if isinstance(widget, QLineEdit):
            return not widget.isReadOnly()
        if isinstance(widget, (QPlainTextEdit, QTextEdit)):
            return not widget.isReadOnly()
        if isinstance(widget, QComboBox):
            return widget.isEditable()
        if isinstance(widget, QAbstractSpinBox):
            return not widget.isReadOnly()
        return False

    def _save_config(self) -> None:
        self._config_store.save(self.config)

    def shutdown(self) -> None:
        if self._did_shutdown:
            return
        self._did_shutdown = True
        self.plugin_host.shutdown()

    async def shutdown_async(self) -> None:
        if self._did_shutdown:
            return
        self._did_shutdown = True
        await self.plugin_host.shutdown_async()

    @asyncClose
    async def closeEvent(self, event: QCloseEvent) -> None:
        self._store_window_state()
        self._save_config()
        await self.shutdown_async()
        super().closeEvent(event)

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

    def _restore_window_state(self) -> None:
        ui_settings = self.config.plugin_settings("ui")
        geometry = str(ui_settings.get("geometry", "")).strip()
        if geometry:
            restored = self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            if restored:
                return
        self._center_on_primary_screen()

    def _store_window_state(self) -> None:
        ui_settings = self.config.plugin_settings("ui")
        ui_settings["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.config.set_plugin_settings("ui", ui_settings)

    def _center_on_primary_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        frame = self.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        self.move(frame.topLeft())
