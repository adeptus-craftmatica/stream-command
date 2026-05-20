from __future__ import annotations

from dataclasses import asdict, dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from stream_control.core.credentials import OBS_PASSWORD, STREAMLABS_TOKEN
from stream_control.core.models import ObsSettings, StreamlabsSettings
from stream_control.plugins.base import AppPlugin, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.services.obs_service import ObsService
from stream_control.services.streamlabs_service import StreamlabsService
from stream_control.ui.widgets.common import set_status_label


@dataclass(slots=True)
class IntegrationSimulatorSettings:
    auto_start: bool = False


@dataclass(slots=True)
class IntegrationsPluginConfig:
    obs: ObsSettings = field(default_factory=ObsSettings)
    streamlabs: StreamlabsSettings = field(default_factory=StreamlabsSettings)
    simulator: IntegrationSimulatorSettings = field(default_factory=IntegrationSimulatorSettings)

    def to_dict(
        self,
        *,
        include_obs_password: bool = False,
        include_streamlabs_token: bool = False,
    ) -> dict[str, object]:
        obs = asdict(self.obs)
        streamlabs = asdict(self.streamlabs)
        if not include_obs_password:
            obs.pop("password", None)
        if not include_streamlabs_token:
            streamlabs.pop("token", None)
        return {
            "obs": obs,
            "streamlabs": streamlabs,
            "simulator": asdict(self.simulator),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "IntegrationsPluginConfig":
        simulator_raw = dict(raw.get("simulator", {}))
        simulator = IntegrationSimulatorSettings(
            auto_start=bool(simulator_raw.get("auto_start", False))
        )
        return cls(
            obs=ObsSettings(**raw.get("obs", {})),
            streamlabs=StreamlabsSettings(**raw.get("streamlabs", {})),
            simulator=simulator,
        )


class IntegrationsPage(QWidget):
    settings_changed = Signal()
    request_obs_connect = Signal()
    request_obs_refresh = Signal()
    request_obs_activate = Signal(str)
    request_streamlabs_connect = Signal()
    request_streamlabs_refresh = Signal()
    request_streamlabs_activate = Signal(str)
    request_start_test_session = Signal()
    request_stop_test_session = Signal()

    def __init__(
        self,
        settings: IntegrationsPluginConfig,
        obs_service: ObsService,
        streamlabs_service: StreamlabsService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._obs_service = obs_service
        self._streamlabs_service = streamlabs_service

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title = QLabel("Integrations")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Connect to OBS Studio and Streamlabs Desktop, then promote this app into your live scene switcher.",
            self,
        )
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        layout.addWidget(self._build_simulator_group())

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        grid.addWidget(self._build_obs_group(), 0, 0)
        grid.addWidget(self._build_streamlabs_group(), 0, 1)
        layout.addLayout(grid)
        layout.addStretch(1)

        self._obs_service.connection_changed.connect(self.set_obs_status)
        self._obs_service.scenes_changed.connect(self.update_obs_scenes)
        self._streamlabs_service.connection_changed.connect(self.set_streamlabs_status)
        self._streamlabs_service.scenes_changed.connect(self.update_streamlabs_scenes)
        self._sync_simulator_controls()

    def _build_simulator_group(self) -> QGroupBox:
        group = QGroupBox("Offline Testing")
        layout = QVBoxLayout(group)

        description = QLabel(
            "Use the built-in simulator to test scene switching, page flows, and controls without going live, streaming, or recording.",
            group,
        )
        description.setObjectName("mutedText")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.simulator_auto_start = QCheckBox("Start the simulator automatically on launch", group)
        self.simulator_auto_start.setChecked(self._settings.simulator.auto_start)
        self.simulator_auto_start.toggled.connect(self._store_simulator_settings)
        layout.addWidget(self.simulator_auto_start)

        buttons = QHBoxLayout()
        start_button = QPushButton("Start Test Session", group)
        start_button.setObjectName("primaryButton")
        start_button.clicked.connect(self.request_start_test_session.emit)
        stop_button = QPushButton("Stop Test Session", group)
        stop_button.clicked.connect(self.request_stop_test_session.emit)
        buttons.addWidget(start_button)
        buttons.addWidget(stop_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.simulator_hint = QLabel("", group)
        self.simulator_hint.setObjectName("mutedText")
        self.simulator_hint.setWordWrap(True)
        layout.addWidget(self.simulator_hint)
        return group

    def _build_obs_group(self) -> QGroupBox:
        group = QGroupBox("OBS Studio")
        layout = QVBoxLayout(group)

        form = QFormLayout()
        self.obs_host = QLineEdit(self._settings.obs.host, group)
        self.obs_port = QSpinBox(group)
        self.obs_port.setRange(1, 65535)
        self.obs_port.setValue(self._settings.obs.port)
        self.obs_password = QLineEdit(self._settings.obs.password, group)
        self.obs_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_auto = QCheckBox("Reconnect on launch", group)
        self.obs_auto.setChecked(self._settings.obs.auto_connect)

        self.obs_host.editingFinished.connect(self._store_obs_settings)
        self.obs_port.valueChanged.connect(self._store_obs_settings)
        self.obs_password.editingFinished.connect(self._store_obs_settings)
        self.obs_auto.toggled.connect(self._store_obs_settings)

        form.addRow("Host", self.obs_host)
        form.addRow("Port", self.obs_port)
        form.addRow("Password", self.obs_password)
        form.addRow("", self.obs_auto)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.obs_connect_button = QPushButton("Connect", group)
        self.obs_connect_button.setObjectName("primaryButton")
        self.obs_connect_button.clicked.connect(self.request_obs_connect.emit)
        refresh_button = QPushButton("Refresh Scenes", group)
        refresh_button.clicked.connect(self.request_obs_refresh.emit)
        disconnect_button = QPushButton("Disconnect", group)
        disconnect_button.clicked.connect(self._obs_service.disconnect)
        buttons.addWidget(self.obs_connect_button)
        buttons.addWidget(refresh_button)
        buttons.addWidget(disconnect_button)
        layout.addLayout(buttons)

        self.obs_status = QLabel("OBS is offline.", group)
        set_status_label(self.obs_status, False, "OBS is offline.")
        layout.addWidget(self.obs_status)

        self.obs_mode = QLabel("Mode: Disconnected", group)
        self.obs_mode.setObjectName("mutedText")
        layout.addWidget(self.obs_mode)

        self.obs_scenes = QListWidget(group)
        layout.addWidget(self.obs_scenes)

        activate_button = QPushButton("Make Selected Scene Live", group)
        activate_button.clicked.connect(self._emit_obs_scene_activation)
        layout.addWidget(activate_button)
        return group

    def _build_streamlabs_group(self) -> QGroupBox:
        group = QGroupBox("Streamlabs Desktop")
        layout = QVBoxLayout(group)

        form = QFormLayout()
        self.sl_host = QLineEdit(self._settings.streamlabs.host, group)
        self.sl_port = QSpinBox(group)
        self.sl_port.setRange(1, 65535)
        self.sl_port.setValue(self._settings.streamlabs.port)
        self.sl_token = QLineEdit(self._settings.streamlabs.token, group)
        self.sl_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.sl_auto = QCheckBox("Reconnect on launch", group)
        self.sl_auto.setChecked(self._settings.streamlabs.auto_connect)

        self.sl_host.editingFinished.connect(self._store_streamlabs_settings)
        self.sl_port.valueChanged.connect(self._store_streamlabs_settings)
        self.sl_token.editingFinished.connect(self._store_streamlabs_settings)
        self.sl_auto.toggled.connect(self._store_streamlabs_settings)

        form.addRow("Host", self.sl_host)
        form.addRow("Port", self.sl_port)
        form.addRow("Remote token", self.sl_token)
        form.addRow("", self.sl_auto)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.streamlabs_connect_button = QPushButton("Connect", group)
        self.streamlabs_connect_button.setObjectName("primaryButton")
        self.streamlabs_connect_button.clicked.connect(self.request_streamlabs_connect.emit)
        refresh_button = QPushButton("Refresh Scenes", group)
        refresh_button.clicked.connect(self.request_streamlabs_refresh.emit)
        disconnect_button = QPushButton("Disconnect", group)
        disconnect_button.clicked.connect(self._streamlabs_service.disconnect)
        buttons.addWidget(self.streamlabs_connect_button)
        buttons.addWidget(refresh_button)
        buttons.addWidget(disconnect_button)
        layout.addLayout(buttons)

        self.streamlabs_status = QLabel("Streamlabs Desktop is offline.", group)
        set_status_label(self.streamlabs_status, False, "Streamlabs Desktop is offline.")
        layout.addWidget(self.streamlabs_status)

        self.streamlabs_mode = QLabel("Mode: Disconnected", group)
        self.streamlabs_mode.setObjectName("mutedText")
        layout.addWidget(self.streamlabs_mode)

        self.streamlabs_scenes = QListWidget(group)
        layout.addWidget(self.streamlabs_scenes)

        activate_button = QPushButton("Make Selected Scene Live", group)
        activate_button.clicked.connect(self._emit_streamlabs_scene_activation)
        layout.addWidget(activate_button)
        return group

    def set_obs_status(self, connected: bool, message: str) -> None:
        set_status_label(self.obs_status, connected, message)
        mode = "Simulator" if self._obs_service.is_simulated else "Real OBS"
        if not connected:
            mode = "Disconnected"
        self.obs_mode.setText(f"Mode: {mode}")

    def set_streamlabs_status(self, connected: bool, message: str) -> None:
        set_status_label(self.streamlabs_status, connected, message)
        mode = "Simulator" if self._streamlabs_service.is_simulated else "Real Streamlabs Desktop"
        if not connected:
            mode = "Disconnected"
        self.streamlabs_mode.setText(f"Mode: {mode}")

    def update_obs_scenes(self, payload: dict[str, object]) -> None:
        self.obs_scenes.clear()
        current = payload.get("current")
        for scene in payload.get("scenes", []):
            item = QListWidgetItem(scene["name"])
            item.setData(Qt.ItemDataRole.UserRole, scene["name"])
            if scene["name"] == current:
                item.setText(f"{scene['name']} [Live]")
            self.obs_scenes.addItem(item)

    def update_streamlabs_scenes(self, payload: dict[str, object]) -> None:
        self.streamlabs_scenes.clear()
        current = payload.get("current")
        for scene in payload.get("scenes", []):
            item = QListWidgetItem(scene["name"])
            item.setData(Qt.ItemDataRole.UserRole, scene["id"])
            if scene["id"] == current:
                item.setText(f"{scene['name']} [Live]")
            self.streamlabs_scenes.addItem(item)

    def _store_obs_settings(self, *_args: object) -> None:
        self._settings.obs.host = self.obs_host.text().strip()
        self._settings.obs.port = self.obs_port.value()
        self._settings.obs.password = self.obs_password.text()
        self._settings.obs.auto_connect = self.obs_auto.isChecked()
        self.settings_changed.emit()

    def _store_streamlabs_settings(self, *_args: object) -> None:
        self._settings.streamlabs.host = self.sl_host.text().strip()
        self._settings.streamlabs.port = self.sl_port.value()
        self._settings.streamlabs.token = self.sl_token.text().strip()
        self._settings.streamlabs.auto_connect = self.sl_auto.isChecked()
        self.settings_changed.emit()

    def _store_simulator_settings(self, *_args: object) -> None:
        self._settings.simulator.auto_start = self.simulator_auto_start.isChecked()
        self.settings_changed.emit()
        self._sync_simulator_controls()

    def _emit_obs_scene_activation(self) -> None:
        item = self.obs_scenes.currentItem()
        if item is None:
            return
        self.request_obs_activate.emit(item.data(Qt.ItemDataRole.UserRole))

    def _emit_streamlabs_scene_activation(self) -> None:
        item = self.streamlabs_scenes.currentItem()
        if item is None:
            return
        self.request_streamlabs_activate.emit(item.data(Qt.ItemDataRole.UserRole))

    def _sync_simulator_controls(self) -> None:
        obs_sim = self._obs_service.is_simulated
        sl_sim = self._streamlabs_service.is_simulated
        if obs_sim or sl_sim:
            self.simulator_hint.setText(
                "A simulator session is active. Real Connect buttons still target the real apps; use Stop Test Session to leave offline mode."
            )
        else:
            self.simulator_hint.setText(
                "Start Test Session uses the built-in simulator. The regular Connect buttons always target real OBS Studio and Streamlabs Desktop."
            )


class IntegrationsPlugin(AppPlugin):
    plugin_id = "integrations"
    display_name = "Integrations"
    nav_order = 10
    load_order = 10

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._settings = IntegrationsPluginConfig()
        self._page: IntegrationsPage | None = None
        self.obs_service: ObsService | None = None
        self.streamlabs_service: StreamlabsService | None = None
        self._persist_obs_password_in_config = False
        self._persist_streamlabs_token_in_config = False

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self._settings = IntegrationsPluginConfig.from_dict(context.plugin_settings(self.plugin_id))
        self._hydrate_credentials()

        self.obs_service = ObsService(context.qt_parent)
        self.streamlabs_service = StreamlabsService(context.qt_parent)
        self._page = IntegrationsPage(self._settings, self.obs_service, self.streamlabs_service, context.qt_parent)
        self._page.settings_changed.connect(self._save_settings)
        self._page.request_obs_connect.connect(lambda: context.schedule(self._connect_obs()))
        self._page.request_obs_refresh.connect(lambda: context.schedule(self.obs_service.refresh_scenes()))
        self._page.request_obs_activate.connect(lambda scene_name: context.schedule(self.obs_service.set_current_scene(scene_name)))
        self._page.request_streamlabs_connect.connect(lambda: context.schedule(self._connect_streamlabs()))
        self._page.request_streamlabs_refresh.connect(
            lambda: context.schedule(self.streamlabs_service.refresh_scenes())
        )
        self._page.request_streamlabs_activate.connect(
            lambda scene_id: context.schedule(self.streamlabs_service.set_active_scene(scene_id))
        )
        self._page.request_start_test_session.connect(lambda: context.schedule(self._start_test_session()))
        self._page.request_stop_test_session.connect(self._stop_test_session)

        context.register_service("integrations.obs_service", self.obs_service)
        context.register_service("integrations.streamlabs_service", self.streamlabs_service)
        context.register_service("integrations.plugin", self)
        self._persist_runtime_settings()

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def on_plugins_loaded(self, _host) -> None:
        if self._context is None:
            return
        if self._settings.simulator.auto_start:
            self._context.schedule(self._start_test_session())
            return
        if self._settings.obs.auto_connect and self.obs_service is not None:
            self._context.schedule(self._connect_obs())
        if self._settings.streamlabs.auto_connect and self._settings.streamlabs.token and self.streamlabs_service is not None:
            self._context.schedule(self._connect_streamlabs())

    def shutdown(self) -> None:
        if self.obs_service is not None:
            self.obs_service.disconnect()
        if self.streamlabs_service is not None:
            self.streamlabs_service.disconnect()

    async def _connect_obs(self) -> None:
        if self.obs_service is None:
            return
        await self.obs_service.connect(self._settings.obs)
        if self._page is not None:
            self._page._sync_simulator_controls()

    async def _connect_streamlabs(self) -> None:
        if self.streamlabs_service is None:
            return
        await self.streamlabs_service.connect(self._settings.streamlabs)
        if self._page is not None:
            self._page._sync_simulator_controls()

    async def _start_test_session(self) -> None:
        if self.obs_service is not None:
            await self.obs_service.connect_simulated()
        if self.streamlabs_service is not None:
            await self.streamlabs_service.connect_simulated()
        if self._page is not None:
            self._page._sync_simulator_controls()

    def _stop_test_session(self) -> None:
        if self.obs_service is not None:
            self.obs_service.disconnect()
        if self.streamlabs_service is not None:
            self.streamlabs_service.disconnect()
        if self._page is not None:
            self._page._sync_simulator_controls()

    def _save_settings(self) -> None:
        if self._context is None:
            return
        self._persist_obs_password_in_config = (
            bool(self._settings.obs.password)
            and not self._context.credential_store.store_secret(OBS_PASSWORD, self._settings.obs.password)
        )
        self._persist_streamlabs_token_in_config = (
            bool(self._settings.streamlabs.token)
            and not self._context.credential_store.store_secret(STREAMLABS_TOKEN, self._settings.streamlabs.token)
        )
        self._context.save_plugin_settings(
            self.plugin_id,
            self._settings.to_dict(
                include_obs_password=self._persist_obs_password_in_config,
                include_streamlabs_token=self._persist_streamlabs_token_in_config,
            ),
        )

    def _hydrate_credentials(self) -> None:
        if self._context is None:
            return
        obs_secret = self._context.credential_store.load_or_migrate(OBS_PASSWORD, self._settings.obs.password)
        streamlabs_secret = self._context.credential_store.load_or_migrate(
            STREAMLABS_TOKEN,
            self._settings.streamlabs.token,
        )
        self._settings.obs.password = obs_secret.value
        self._settings.streamlabs.token = streamlabs_secret.value
        self._persist_obs_password_in_config = obs_secret.should_persist_in_config
        self._persist_streamlabs_token_in_config = streamlabs_secret.should_persist_in_config

    def _persist_runtime_settings(self) -> None:
        if self._context is None:
            return
        self._context.app_config.set_plugin_settings(
            self.plugin_id,
            self._settings.to_dict(
                include_obs_password=self._persist_obs_password_in_config,
                include_streamlabs_token=self._persist_streamlabs_token_in_config,
            ),
        )
