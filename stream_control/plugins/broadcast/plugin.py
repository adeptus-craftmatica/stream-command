from __future__ import annotations

from dataclasses import asdict, dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stream_control.plugins.base import AppPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.services.obs_service import ObsService
from stream_control.services.streamlabs_service import StreamlabsService
from stream_control.services.twitch_service import (
    TwitchApiError,
    TwitchCategory,
    TwitchChannelInfo,
    TwitchCredentials,
    TwitchService,
)
from stream_control.ui.widgets.common import PanelCard, set_status_label


@dataclass(slots=True)
class BroadcastPreset:
    name: str
    title: str = ""
    category_id: str = ""
    category_name: str = ""
    apply_info_before_live: bool = True


@dataclass(slots=True)
class BroadcastChecklistItem:
    label: str
    checked: bool = False


def default_broadcast_checklist() -> list[BroadcastChecklistItem]:
    return [
        BroadcastChecklistItem("Microphone levels checked"),
        BroadcastChecklistItem("Game or camera scene is correct"),
        BroadcastChecklistItem("Title and category are ready"),
        BroadcastChecklistItem("Chat and alerts are visible"),
        BroadcastChecklistItem("Music and soundboard volume are safe"),
    ]


@dataclass(slots=True)
class BroadcastPluginConfig:
    output_target: str = "auto"
    twitch: TwitchCredentials = field(default_factory=TwitchCredentials)
    stream_title: str = ""
    category_id: str = ""
    category_name: str = ""
    apply_info_before_going_live: bool = True
    presets: list[BroadcastPreset] = field(default_factory=list)
    selected_preset_name: str = ""
    checklist: list[BroadcastChecklistItem] = field(default_factory=default_broadcast_checklist)
    require_checklist_before_live: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "output_target": self.output_target,
            "twitch": asdict(self.twitch),
            "stream_title": self.stream_title,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "apply_info_before_going_live": self.apply_info_before_going_live,
            "presets": [asdict(preset) for preset in self.presets],
            "selected_preset_name": self.selected_preset_name,
            "checklist": [asdict(item) for item in self.checklist],
            "require_checklist_before_live": self.require_checklist_before_live,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "BroadcastPluginConfig":
        checklist_raw = raw.get("checklist", [])
        checklist = [BroadcastChecklistItem(**item) for item in checklist_raw] if checklist_raw else default_broadcast_checklist()
        return cls(
            output_target=str(raw.get("output_target", "auto") or "auto"),
            twitch=TwitchCredentials(**raw.get("twitch", {})),
            stream_title=str(raw.get("stream_title", "")),
            category_id=str(raw.get("category_id", "")),
            category_name=str(raw.get("category_name", "")),
            apply_info_before_going_live=bool(raw.get("apply_info_before_going_live", True)),
            presets=[BroadcastPreset(**item) for item in raw.get("presets", [])],
            selected_preset_name=str(raw.get("selected_preset_name", "")),
            checklist=checklist,
            require_checklist_before_live=bool(raw.get("require_checklist_before_live", True)),
        )

    def preset_by_name(self, name: str) -> BroadcastPreset | None:
        normalized = name.strip().lower()
        if not normalized:
            return None
        for preset in self.presets:
            if preset.name.strip().lower() == normalized:
                return preset
        return None

    def upsert_preset(self, preset: BroadcastPreset) -> None:
        existing = self.preset_by_name(preset.name)
        if existing is None:
            self.presets.append(preset)
        else:
            existing.name = preset.name
            existing.title = preset.title
            existing.category_id = preset.category_id
            existing.category_name = preset.category_name
            existing.apply_info_before_live = preset.apply_info_before_live
        self.presets.sort(key=lambda item: item.name.lower())
        self.selected_preset_name = preset.name

    def remove_preset(self, name: str) -> bool:
        normalized = name.strip().lower()
        kept = [preset for preset in self.presets if preset.name.strip().lower() != normalized]
        changed = len(kept) != len(self.presets)
        self.presets = kept
        if self.selected_preset_name.strip().lower() == normalized:
            self.selected_preset_name = ""
        return changed

    def incomplete_checklist_labels(self) -> list[str]:
        return [item.label for item in self.checklist if item.label.strip() and not item.checked]

    def reset_checklist(self) -> None:
        for item in self.checklist:
            item.checked = False


class BroadcastPage(QWidget):
    settings_changed = Signal()
    output_target_changed = Signal()
    request_refresh_output_status = Signal()
    request_go_live = Signal()
    request_stop_streaming = Signal()
    request_search_categories = Signal()
    request_refresh_channel_info = Signal()
    request_apply_stream_info = Signal(str)
    request_apply_selected_preset = Signal()
    request_save_preset = Signal()
    request_update_selected_preset = Signal()
    request_delete_selected_preset = Signal()

    def __init__(self, settings: BroadcastPluginConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._rendering_presets = False
        self._rendering_checklist = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title = QLabel("Broadcast Control", self)
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Run a repeatable show launch with saved presets, a go-live checklist, and stream metadata controls in one plugin-owned surface.",
            self,
        )
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("broadcastTabs")
        self.tabs.addTab(self._build_launch_tab(), "Launch")
        self.tabs.addTab(self._build_presets_tab(), "Presets")
        self.tabs.addTab(self._build_metadata_tab(), "Metadata")
        layout.addWidget(self.tabs)
        layout.addStretch(1)

        self._apply_settings_to_fields()
        self.set_output_target_summary(
            "Auto Detect",
            "Connect OBS Studio or Streamlabs Desktop on the Integrations page, then come back here to control the live output.",
        )
        self.set_stream_status(
            {
                "connected": False,
                "is_live": False,
                "status": "Disconnected",
                "detail": "No live controller connected yet.",
            }
        )
        self.set_twitch_status(False, "Add your Twitch app credentials when you are ready to publish title and category changes.")
        self.set_stream_info_status(
            True,
            "Apply Stream Info works before you go live, and Update While Live stays available once the stream is live.",
        )
        self.set_preset_status(True, "Save stream setups like Podcast, Just Chatting, or Ranked Sessions so you can restore them in one click.")
        self._refresh_checklist_status()

    def _build_launch_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_copy = QLabel(
            "Refresh the live output, confirm the checklist, then go live from here without bouncing between sections.",
            tab,
        )
        summary_copy.setObjectName("mutedText")
        summary_copy.setWordWrap(True)
        layout.addWidget(summary_copy)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        grid.addWidget(self._build_output_card(), 0, 0)
        grid.addWidget(self._build_checklist_card(), 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        return tab

    def _build_presets_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_copy = QLabel(
            "Keep a saved setup for each stream format so you can pull the right title, category, and launch behavior into place instantly.",
            tab,
        )
        summary_copy.setObjectName("mutedText")
        summary_copy.setWordWrap(True)
        layout.addWidget(summary_copy)

        layout.addWidget(self._build_preset_card())
        layout.addStretch(1)
        return tab

    def _build_metadata_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_copy = QLabel(
            "Handle credentials, title, and category work here with enough space to search categories and review what will be published.",
            tab,
        )
        summary_copy.setObjectName("mutedText")
        summary_copy.setWordWrap(True)
        layout.addWidget(summary_copy)

        metadata_tabs = QTabWidget(tab)
        metadata_tabs.setObjectName("broadcastMetadataTabs")
        metadata_tabs.addTab(self._build_twitch_card(), "Channel")
        metadata_tabs.addTab(self._build_stream_info_card(), "Stream Info")
        layout.addWidget(metadata_tabs, 1)
        return tab

    def _build_output_card(self) -> PanelCard:
        card = PanelCard("Live Output", self)
        card.layout.addWidget(
            self._muted_label(
                "Choose which connected app should handle Go Live and Stop Streaming. Auto Detect prefers OBS Studio when more than one output app is connected.",
                card,
            )
        )

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.output_target = QComboBox(card)
        self.output_target.addItem("Auto Detect", "auto")
        self.output_target.addItem("OBS Studio", "obs")
        self.output_target.addItem("Streamlabs Desktop", "streamlabs")
        self.output_target.currentIndexChanged.connect(self._store_output_target)
        form.addRow("Output app", self.output_target)
        card.layout.addLayout(form)

        self.output_target_summary = QLabel(card)
        self.output_target_summary.setWordWrap(True)
        card.layout.addWidget(self.output_target_summary)

        self.stream_state = QLabel(card)
        self.stream_state.setObjectName("sectionTitle")
        self.stream_state.setWordWrap(True)
        card.layout.addWidget(self.stream_state)

        self.output_status = QLabel(card)
        self.output_status.setWordWrap(True)
        card.layout.addWidget(self.output_status)

        buttons = QHBoxLayout()
        refresh = QPushButton("Refresh Status", card)
        refresh.clicked.connect(self.request_refresh_output_status.emit)
        self.go_live_button = QPushButton("Go Live", card)
        self.go_live_button.setObjectName("primaryButton")
        self.go_live_button.clicked.connect(self._emit_go_live)
        self.stop_stream_button = QPushButton("Stop Streaming", card)
        self.stop_stream_button.setObjectName("dangerButton")
        self.stop_stream_button.clicked.connect(self.request_stop_streaming.emit)
        buttons.addWidget(refresh)
        buttons.addWidget(self.go_live_button)
        buttons.addWidget(self.stop_stream_button)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)
        return card

    def _build_twitch_card(self) -> PanelCard:
        card = PanelCard("Twitch Channel", self)
        card.layout.addWidget(
            self._muted_label(
                "Twitch metadata updates use the Helix API. Use a user access token with the channel:manage:broadcast scope. Broadcaster ID is optional and can be resolved from the token.",
                card,
            )
        )

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.client_id = QLineEdit(card)
        self.client_id.setPlaceholderText("Twitch Client ID")
        self.client_id.editingFinished.connect(self._store_twitch_credentials)
        form.addRow("Client ID", self.client_id)

        self.access_token = QLineEdit(card)
        self.access_token.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.access_token.setPlaceholderText("User Access Token")
        self.access_token.editingFinished.connect(self._store_twitch_credentials)
        form.addRow("Access token", self.access_token)

        self.broadcaster_id = QLineEdit(card)
        self.broadcaster_id.setPlaceholderText("Optional, auto-resolved when blank")
        self.broadcaster_id.editingFinished.connect(self._store_twitch_credentials)
        form.addRow("Broadcaster ID", self.broadcaster_id)

        card.layout.addLayout(form)

        self.twitch_status = QLabel(card)
        self.twitch_status.setWordWrap(True)
        card.layout.addWidget(self.twitch_status)
        return card

    def _build_preset_card(self) -> PanelCard:
        card = PanelCard("Show Presets", self)
        card.layout.addWidget(
            self._muted_label(
                "Capture a whole stream setup as a reusable preset. Each preset stores the title, category, and whether metadata should be pushed before Go Live.",
                card,
            )
        )

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.preset_name = QLineEdit(card)
        self.preset_name.setPlaceholderText("Preset name")
        form.addRow("Preset name", self.preset_name)
        card.layout.addLayout(form)

        self.preset_list = QListWidget(card)
        self.preset_list.setMaximumHeight(140)
        self.preset_list.itemSelectionChanged.connect(self._sync_selected_preset)
        card.layout.addWidget(self.preset_list)

        buttons = QHBoxLayout()
        apply_button = QPushButton("Apply Selected", card)
        apply_button.clicked.connect(self.request_apply_selected_preset.emit)
        save_button = QPushButton("Save Current", card)
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self.request_save_preset.emit)
        update_button = QPushButton("Update Selected", card)
        update_button.clicked.connect(self.request_update_selected_preset.emit)
        delete_button = QPushButton("Delete Selected", card)
        delete_button.setObjectName("dangerButton")
        delete_button.clicked.connect(self.request_delete_selected_preset.emit)
        buttons.addWidget(apply_button)
        buttons.addWidget(save_button)
        buttons.addWidget(update_button)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)

        self.preset_summary = QLabel(card)
        self.preset_summary.setObjectName("mutedText")
        self.preset_summary.setWordWrap(True)
        card.layout.addWidget(self.preset_summary)

        self.preset_status = QLabel(card)
        self.preset_status.setWordWrap(True)
        card.layout.addWidget(self.preset_status)
        return card

    def _build_checklist_card(self) -> PanelCard:
        card = PanelCard("Go-Live Checklist", self)
        card.layout.addWidget(
            self._muted_label(
                "Turn your launch routine into a repeatable checklist. Broadcast Control can block Go Live until every required item is checked off.",
                card,
            )
        )

        self.require_checklist = QCheckBox("Require checklist completion before Go Live", card)
        self.require_checklist.toggled.connect(self._store_checklist_settings)
        card.layout.addWidget(self.require_checklist)

        self.checklist_progress = QLabel(card)
        self.checklist_progress.setObjectName("sectionTitle")
        self.checklist_progress.setWordWrap(True)
        card.layout.addWidget(self.checklist_progress)

        self.checklist_list = QListWidget(card)
        self.checklist_list.setMaximumHeight(140)
        self.checklist_list.itemChanged.connect(self._sync_checklist_items)
        card.layout.addWidget(self.checklist_list)

        add_row = QHBoxLayout()
        self.new_checklist_item = QLineEdit(card)
        self.new_checklist_item.setPlaceholderText("Add a checklist item")
        self.new_checklist_item.returnPressed.connect(self._add_checklist_item)
        add_button = QPushButton("Add Item", card)
        add_button.clicked.connect(self._add_checklist_item)
        add_row.addWidget(self.new_checklist_item)
        add_row.addWidget(add_button)
        card.layout.addLayout(add_row)

        buttons = QHBoxLayout()
        reset_button = QPushButton("Reset Checks", card)
        reset_button.clicked.connect(self._reset_checklist)
        remove_button = QPushButton("Remove Selected", card)
        remove_button.clicked.connect(self._remove_selected_checklist_item)
        defaults_button = QPushButton("Restore Defaults", card)
        defaults_button.clicked.connect(self._restore_default_checklist)
        buttons.addWidget(reset_button)
        buttons.addWidget(remove_button)
        buttons.addWidget(defaults_button)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)

        self.checklist_status = QLabel(card)
        self.checklist_status.setWordWrap(True)
        card.layout.addWidget(self.checklist_status)
        return card

    def _build_stream_info_card(self) -> PanelCard:
        card = PanelCard("Stream Information", self)
        card.layout.addWidget(
            self._muted_label(
                "Keep the next title and category ready here. Apply Stream Info publishes it now, while Update While Live lets you change the channel without stopping the stream.",
                card,
            )
        )

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.stream_title = QLineEdit(card)
        self.stream_title.setPlaceholderText("Stream title")
        self.stream_title.editingFinished.connect(self._store_stream_info)
        form.addRow("Title", self.stream_title)
        card.layout.addLayout(form)

        search_row = QHBoxLayout()
        self.category_search = QLineEdit(card)
        self.category_search.setPlaceholderText("Search Twitch categories or games")
        self.category_search.editingFinished.connect(self._store_stream_info)
        search_button = QPushButton("Search Categories", card)
        search_button.clicked.connect(self._emit_search_categories)
        search_row.addWidget(self.category_search)
        search_row.addWidget(search_button)
        card.layout.addLayout(search_row)

        self.category_results = QListWidget(card)
        self.category_results.setMaximumHeight(140)
        self.category_results.itemSelectionChanged.connect(self._select_category)
        card.layout.addWidget(self.category_results)

        self.selected_category = QLabel(card)
        self.selected_category.setWordWrap(True)
        card.layout.addWidget(self.selected_category)

        self.auto_apply_before_live = QCheckBox("Apply the saved title and category before Go Live", card)
        self.auto_apply_before_live.toggled.connect(self._store_stream_info)
        card.layout.addWidget(self.auto_apply_before_live)

        buttons = QHBoxLayout()
        refresh_button = QPushButton("Load Current Twitch Info", card)
        refresh_button.clicked.connect(self._emit_refresh_channel_info)
        apply_button = QPushButton("Apply Stream Info", card)
        apply_button.setObjectName("primaryButton")
        apply_button.clicked.connect(lambda: self._emit_apply_stream_info("standard"))
        self.update_live_button = QPushButton("Update While Live", card)
        self.update_live_button.clicked.connect(lambda: self._emit_apply_stream_info("live_update"))
        buttons.addWidget(refresh_button)
        buttons.addWidget(apply_button)
        buttons.addWidget(self.update_live_button)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)

        self.stream_info_status = QLabel(card)
        self.stream_info_status.setWordWrap(True)
        card.layout.addWidget(self.stream_info_status)
        return card

    def _apply_settings_to_fields(self) -> None:
        self.output_target.setCurrentIndex(max(0, self.output_target.findData(self._settings.output_target)))
        self.client_id.setText(self._settings.twitch.client_id)
        self.access_token.setText(self._settings.twitch.access_token)
        self.broadcaster_id.setText(self._settings.twitch.broadcaster_id)
        self.stream_title.setText(self._settings.stream_title)
        self.category_search.setText(self._settings.category_name)
        self.auto_apply_before_live.setChecked(self._settings.apply_info_before_going_live)
        self.require_checklist.setChecked(self._settings.require_checklist_before_live)
        self._update_selected_category_label()
        self.render_presets()
        self.render_checklist()

    def set_output_target_summary(self, target_name: str, detail: str) -> None:
        self.output_target_summary.setText(f"Selected output: {target_name}\n{detail}")

    def set_stream_status(self, payload: dict[str, object]) -> None:
        status = str(payload.get("status", "Disconnected"))
        detail = str(payload.get("detail", "")).strip()
        self.stream_state.setText(f"Streaming status: {status}")
        ok = bool(payload.get("connected", False))
        if detail:
            set_status_label(self.output_status, ok, detail)
        else:
            set_status_label(self.output_status, ok, f"Streaming status is {status}.")
        is_live = bool(payload.get("is_live", False))
        self.go_live_button.setEnabled(ok and not is_live)
        self.stop_stream_button.setEnabled(ok and is_live)
        self.update_live_button.setEnabled(is_live)

    def set_twitch_status(self, ok: bool, message: str) -> None:
        set_status_label(self.twitch_status, ok, message)

    def set_stream_info_status(self, ok: bool, message: str) -> None:
        set_status_label(self.stream_info_status, ok, message)

    def set_preset_status(self, ok: bool, message: str) -> None:
        set_status_label(self.preset_status, ok, message)

    def set_checklist_status(self, ok: bool, message: str) -> None:
        set_status_label(self.checklist_status, ok, message)

    def set_category_results(self, categories: list[TwitchCategory]) -> None:
        self.category_results.clear()
        for category in categories:
            item = QListWidgetItem(category.name)
            item.setData(Qt.ItemDataRole.UserRole, category.id)
            self.category_results.addItem(item)
            if category.id == self._settings.category_id:
                item.setSelected(True)
        if not categories:
            self.category_results.addItem(QListWidgetItem("No categories matched that search."))

    def load_channel_info(self, info: TwitchChannelInfo) -> None:
        self.stream_title.setText(info.title)
        self.category_search.setText(info.category_name)
        self.broadcaster_id.setText(info.broadcaster_id)
        self._settings.twitch.broadcaster_id = info.broadcaster_id
        self._settings.stream_title = info.title
        self._settings.category_id = info.category_id
        self._settings.category_name = info.category_name
        self._update_selected_category_label()

    def render_presets(self) -> None:
        self._rendering_presets = True
        self.preset_list.clear()
        selected_name = self._settings.selected_preset_name.strip().lower()
        for preset in self._settings.presets:
            item = QListWidgetItem(preset.name)
            item.setData(Qt.ItemDataRole.UserRole, preset.name)
            self.preset_list.addItem(item)
            if preset.name.strip().lower() == selected_name:
                item.setSelected(True)
        self._rendering_presets = False
        self._sync_selected_preset()

    def render_checklist(self) -> None:
        self._rendering_checklist = True
        self.checklist_list.clear()
        for checklist_item in self._settings.checklist:
            item = QListWidgetItem(checklist_item.label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(Qt.CheckState.Checked if checklist_item.checked else Qt.CheckState.Unchecked)
            self.checklist_list.addItem(item)
        self._rendering_checklist = False
        self._refresh_checklist_status()

    def selected_preset_name(self) -> str:
        item = self.preset_list.currentItem()
        if item is not None:
            return str(item.data(Qt.ItemDataRole.UserRole) or item.text()).strip()
        return self.preset_name.text().strip()

    def current_preset(self) -> BroadcastPreset | None:
        return self._settings.preset_by_name(self.selected_preset_name())

    def incomplete_checklist_labels(self) -> list[str]:
        return self._settings.incomplete_checklist_labels()

    def load_preset(self, preset: BroadcastPreset) -> None:
        self.preset_name.setText(preset.name)
        self.stream_title.setText(preset.title)
        self.category_search.setText(preset.category_name)
        self.auto_apply_before_live.setChecked(preset.apply_info_before_live)
        self._settings.stream_title = preset.title
        self._settings.category_id = preset.category_id
        self._settings.category_name = preset.category_name
        self._settings.apply_info_before_going_live = preset.apply_info_before_live
        self._settings.selected_preset_name = preset.name
        self._update_selected_category_label()
        self.settings_changed.emit()
        self.render_presets()

    def reset_checklist_checks(self) -> None:
        self._settings.reset_checklist()
        self.render_checklist()
        self.settings_changed.emit()

    def restore_default_checklist(self) -> None:
        self._settings.checklist = default_broadcast_checklist()
        self.render_checklist()
        self.settings_changed.emit()

    def _store_output_target(self) -> None:
        self._settings.output_target = str(self.output_target.currentData() or "auto")
        self.settings_changed.emit()
        self.output_target_changed.emit()

    def _store_twitch_credentials(self) -> None:
        self._settings.twitch.client_id = self.client_id.text().strip()
        self._settings.twitch.access_token = self.access_token.text().strip()
        self._settings.twitch.broadcaster_id = self.broadcaster_id.text().strip()
        self.settings_changed.emit()

    def _store_stream_info(self) -> None:
        self._settings.stream_title = self.stream_title.text().strip()
        self._settings.apply_info_before_going_live = self.auto_apply_before_live.isChecked()
        typed_category = self.category_search.text().strip()
        if typed_category != self._settings.category_name:
            self._settings.category_name = typed_category
            self._settings.category_id = ""
        self.settings_changed.emit()
        self._update_selected_category_label()

    def _store_all(self) -> None:
        self._store_twitch_credentials()
        self._settings.stream_title = self.stream_title.text().strip()
        typed_category = self.category_search.text().strip()
        if typed_category and typed_category != self._settings.category_name:
            self._settings.category_name = typed_category
            self._settings.category_id = ""
        self._settings.apply_info_before_going_live = self.auto_apply_before_live.isChecked()
        self.settings_changed.emit()
        self._update_selected_category_label()

    def _select_category(self) -> None:
        item = self.category_results.currentItem()
        if item is None:
            return
        category_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        category_name = item.text().strip()
        if not category_id:
            return
        self._settings.category_id = category_id
        self._settings.category_name = category_name
        self.category_search.setText(category_name)
        self._update_selected_category_label()
        self.settings_changed.emit()

    def _update_selected_category_label(self) -> None:
        if self._settings.category_name:
            self.selected_category.setText(
                f"Selected category: {self._settings.category_name}"
                + (f" ({self._settings.category_id})" if self._settings.category_id else "")
            )
        else:
            self.selected_category.setText("Selected category: Keep the current Twitch category or search for a new one.")

    def _sync_selected_preset(self) -> None:
        if self._rendering_presets:
            return
        preset = self.current_preset()
        if preset is None:
            self._settings.selected_preset_name = ""
            self.preset_summary.setText("Select a preset to inspect it, or save the current stream setup as a new one.")
            return
        self._settings.selected_preset_name = preset.name
        self.preset_name.setText(preset.name)
        self.preset_summary.setText(
            f"Title: {preset.title or 'Keep current title'}\n"
            f"Category: {preset.category_name or 'Keep current category'}\n"
            f"Apply metadata before live: {'Yes' if preset.apply_info_before_live else 'No'}"
        )
        self.settings_changed.emit()

    def _sync_checklist_items(self, _item: QListWidgetItem) -> None:
        if self._rendering_checklist:
            return
        updated: list[BroadcastChecklistItem] = []
        for row in range(self.checklist_list.count()):
            raw_item = self.checklist_list.item(row)
            label = raw_item.text().strip()
            if not label:
                continue
            updated.append(
                BroadcastChecklistItem(
                    label=label,
                    checked=raw_item.checkState() == Qt.CheckState.Checked,
                )
            )
        self._settings.checklist = updated
        self._refresh_checklist_status()
        self.settings_changed.emit()

    def _refresh_checklist_status(self) -> None:
        total = len([item for item in self._settings.checklist if item.label.strip()])
        done = len([item for item in self._settings.checklist if item.label.strip() and item.checked])
        if total == 0:
            self.checklist_progress.setText("Checklist progress: no items yet")
            self.set_checklist_status(False, "Add at least one item if you want Broadcast Control to guide your launch routine.")
            return

        incomplete = self.incomplete_checklist_labels()
        self.checklist_progress.setText(f"Checklist progress: {done}/{total} complete")
        if not incomplete:
            self.set_checklist_status(True, "Checklist complete. Go Live can proceed cleanly.")
            return
        preview = ", ".join(incomplete[:3])
        suffix = "" if len(incomplete) <= 3 else ", ..."
        if self._settings.require_checklist_before_live:
            self.set_checklist_status(False, f"{len(incomplete)} items still need attention: {preview}{suffix}")
        else:
            self.set_checklist_status(True, f"{len(incomplete)} items remain, but Go Live is allowed: {preview}{suffix}")

    def _store_checklist_settings(self) -> None:
        self._settings.require_checklist_before_live = self.require_checklist.isChecked()
        self._refresh_checklist_status()
        self.settings_changed.emit()

    def _add_checklist_item(self) -> None:
        label = self.new_checklist_item.text().strip()
        if not label:
            self.set_checklist_status(False, "Enter a checklist item before adding it.")
            return
        self._settings.checklist.append(BroadcastChecklistItem(label=label))
        self.new_checklist_item.clear()
        self.render_checklist()
        self.settings_changed.emit()

    def _remove_selected_checklist_item(self) -> None:
        row = self.checklist_list.currentRow()
        if row < 0 or row >= len(self._settings.checklist):
            self.set_checklist_status(False, "Select a checklist item before removing it.")
            return
        del self._settings.checklist[row]
        self.render_checklist()
        self.settings_changed.emit()

    def _reset_checklist(self) -> None:
        self.reset_checklist_checks()

    def _restore_default_checklist(self) -> None:
        self.restore_default_checklist()

    def _emit_search_categories(self) -> None:
        self._store_all()
        self.request_search_categories.emit()

    def _emit_refresh_channel_info(self) -> None:
        self._store_all()
        self.request_refresh_channel_info.emit()

    def _emit_apply_stream_info(self, mode: str) -> None:
        self._store_all()
        self.request_apply_stream_info.emit(mode)

    def _emit_go_live(self) -> None:
        self._store_all()
        self.request_go_live.emit()

    @staticmethod
    def _muted_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("mutedText")
        label.setWordWrap(True)
        return label


class BroadcastPlugin(AppPlugin):
    plugin_id = "broadcast"
    display_name = "Broadcast"
    nav_order = 15
    load_order = 15

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._settings = BroadcastPluginConfig()
        self._page: BroadcastPage | None = None
        self.obs_service: ObsService | None = None
        self.streamlabs_service: StreamlabsService | None = None
        self.twitch_service: TwitchService | None = None
        self._stream_status_cache: dict[str, dict[str, object]] = {}

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self._settings = BroadcastPluginConfig.from_dict(context.plugin_settings(self.plugin_id))
        self.obs_service = context.get_service("integrations.obs_service")
        self.streamlabs_service = context.get_service("integrations.streamlabs_service")
        self.twitch_service = TwitchService(context.qt_parent)

        self._page = BroadcastPage(self._settings, context.qt_parent)
        self._page.settings_changed.connect(self._save_settings)
        self._page.output_target_changed.connect(self._sync_output_summary)
        self._page.request_refresh_output_status.connect(lambda: context.schedule(self._refresh_output_status()))
        self._page.request_go_live.connect(lambda: context.schedule(self._go_live()))
        self._page.request_stop_streaming.connect(lambda: context.schedule(self._stop_streaming()))
        self._page.request_search_categories.connect(lambda: context.schedule(self._search_categories()))
        self._page.request_refresh_channel_info.connect(lambda: context.schedule(self._load_current_twitch_info()))
        self._page.request_apply_stream_info.connect(lambda mode: context.schedule(self._apply_stream_info(mode)))
        self._page.request_apply_selected_preset.connect(self._apply_selected_preset)
        self._page.request_save_preset.connect(self._save_current_as_preset)
        self._page.request_update_selected_preset.connect(self._update_selected_preset)
        self._page.request_delete_selected_preset.connect(self._delete_selected_preset)

        self._bind_output_service("obs", self.obs_service)
        self._bind_output_service("streamlabs", self.streamlabs_service)
        context.register_service("broadcast.plugin", self)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def hotkey_actions(self) -> list[HotkeyAction]:
        if self._context is None:
            return []
        return [
            HotkeyAction(
                action_id="broadcast.go_live",
                label="Go live with the selected output app",
                handler=lambda: self._context.schedule(self._go_live()),
            ),
            HotkeyAction(
                action_id="broadcast.stop_streaming",
                label="Stop the active stream",
                handler=lambda: self._context.schedule(self._stop_streaming()),
            ),
            HotkeyAction(
                action_id="broadcast.apply_stream_info",
                label="Apply saved title and category",
                handler=lambda: self._context.schedule(self._apply_stream_info("standard")),
            ),
        ]

    def on_plugins_loaded(self, _host) -> None:
        if self._context is not None:
            self._sync_output_summary()
            self._context.schedule(self._refresh_output_status())

    def _bind_output_service(self, key: str, service: ObsService | StreamlabsService | None) -> None:
        if service is None:
            return
        service.connection_changed.connect(
            lambda connected, message, service_key=key: self._handle_output_connection_change(
                service_key, connected, message
            )
        )
        service.stream_status_changed.connect(
            lambda payload, service_key=key: self._handle_stream_status(service_key, payload)
        )

    def _handle_output_connection_change(self, key: str, connected: bool, message: str) -> None:
        self._sync_output_summary()
        if self._page is not None and self._selected_output_key() == key:
            self._page.set_stream_status(self._stream_status_cache.get(key, self._fallback_status(message, connected)))
        if connected and self._context is not None:
            service = self._service_for_key(key)
            if service is not None:
                self._context.schedule(service.refresh_stream_status())

    def _handle_stream_status(self, key: str, payload: dict[str, object]) -> None:
        self._stream_status_cache[key] = payload
        self._sync_output_summary()

    def _sync_output_summary(self) -> None:
        if self._page is None:
            return

        selected = self._selected_output()
        if selected is None:
            self._page.set_output_target_summary(
                "Unavailable",
                "Connect OBS Studio or Streamlabs Desktop on the Integrations page first.",
            )
            self._page.set_stream_status(self._fallback_status("No output controller is available yet.", False))
            return

        key, label, service = selected
        if service is None:
            self._page.set_output_target_summary(label, f"{label} is not available in this session.")
            self._page.set_stream_status(self._fallback_status(f"{label} is unavailable.", False))
            return

        if self._settings.output_target == "auto":
            if self.obs_service and self.obs_service.is_connected and self.streamlabs_service and self.streamlabs_service.is_connected:
                detail = f"Auto Detect is currently using {label}. OBS Studio is preferred when both apps are connected."
            elif service.is_connected and service.is_simulated:
                detail = f"Auto Detect is using the {label} simulator for offline testing."
            elif service.is_connected:
                detail = f"Auto Detect is using {label}."
            else:
                detail = f"Auto Detect will use {label} once it is connected."
        elif service.is_simulated:
            detail = f"{label} simulator is active for offline testing."
        elif service.is_connected:
            detail = f"{label} is connected and ready."
        else:
            detail = f"{label} is selected but not connected yet."

        preset_name = self._settings.selected_preset_name.strip()
        if preset_name:
            detail += f" Active preset: {preset_name}."
        self._page.set_output_target_summary(label, detail)
        payload = self._stream_status_cache.get(key)
        if payload is not None:
            self._page.set_stream_status(payload)
        else:
            self._page.set_stream_status(self._fallback_status(detail, service.is_connected))

    async def _refresh_output_status(self) -> None:
        selected = self._selected_output()
        if self._page is None:
            return
        if selected is None or selected[2] is None:
            self._page.set_stream_status(self._fallback_status("Connect OBS Studio or Streamlabs Desktop first.", False))
            return

        key, label, service = selected
        payload = await service.refresh_stream_status()
        self._stream_status_cache[key] = payload
        self._sync_output_summary()
        self._page.set_stream_status(payload)
        self._page.set_stream_info_status(
            True,
            f"{label} reports {payload['status'].lower()}. Use Update While Live any time the stream is active.",
        )

    async def _go_live(self) -> None:
        selected = self._selected_output()
        if self._page is None or selected is None or selected[2] is None:
            return

        incomplete = self._settings.incomplete_checklist_labels()
        if self._settings.require_checklist_before_live and incomplete:
            preview = ", ".join(incomplete[:3])
            suffix = "" if len(incomplete) <= 3 else ", ..."
            message = f"Go Live blocked until the checklist is complete. Remaining items: {preview}{suffix}"
            self._page.set_checklist_status(False, message)
            set_status_label(self._page.output_status, False, message)
            return

        key, label, service = selected
        notes: list[str] = []
        if incomplete:
            notes.append(f"Checklist warning: {len(incomplete)} items remain.")
        if self._settings.apply_info_before_going_live:
            if self._has_twitch_credentials():
                success, message = await self._publish_stream_info("before_go_live", announce=False)
                notes.append(message)
                if not success:
                    notes.append("Streaming will still continue so you can recover quickly.")
            else:
                notes.append("Twitch update skipped because no Twitch credentials are configured yet.")

        payload = await service.start_streaming()
        self._stream_status_cache[key] = payload
        self._sync_output_summary()
        notes.append(f"{label}: {payload['detail']}")
        set_status_label(self._page.output_status, bool(payload.get("is_live", False)), " ".join(notes))

    async def _stop_streaming(self) -> None:
        selected = self._selected_output()
        if self._page is None or selected is None or selected[2] is None:
            return

        key, label, service = selected
        payload = await service.stop_streaming()
        self._stream_status_cache[key] = payload
        self._sync_output_summary()
        set_status_label(
            self._page.output_status,
            bool(payload.get("connected", False)) and not bool(payload.get("is_live", False)),
            f"{label}: {payload['detail']}",
        )

    async def _search_categories(self) -> None:
        if self._page is None or self.twitch_service is None:
            return
        query = self._settings.category_name.strip() or self._page.category_search.text().strip()
        if not query:
            self._page.set_stream_info_status(False, "Enter a category search first.")
            return
        try:
            categories = await self.twitch_service.search_categories(self._settings.twitch, query)
        except TwitchApiError as exc:
            self._page.set_twitch_status(False, str(exc))
            self._page.set_stream_info_status(False, f"Could not search Twitch categories: {exc}")
            return
        self._page.set_category_results(categories)
        if categories:
            self._page.set_stream_info_status(True, f"Found {len(categories)} Twitch category matches.")
        else:
            self._page.set_stream_info_status(False, "No Twitch categories matched that search.")

    async def _load_current_twitch_info(self) -> None:
        if self._page is None or self.twitch_service is None:
            return
        try:
            info = await self.twitch_service.get_channel_info(self._settings.twitch)
        except TwitchApiError as exc:
            self._page.set_twitch_status(False, str(exc))
            self._page.set_stream_info_status(False, f"Could not load Twitch channel info: {exc}")
            return
        self._apply_twitch_channel_info(info)
        self._page.set_twitch_status(True, f"Loaded channel info for {info.broadcaster_name or info.broadcaster_id}.")
        self._page.set_stream_info_status(
            True,
            "Current Twitch title and category loaded into Broadcast Control.",
        )

    async def _apply_stream_info(self, mode: str) -> None:
        await self._publish_stream_info(mode, announce=True)

    async def _publish_stream_info(self, mode: str, announce: bool) -> tuple[bool, str]:
        if self._page is None or self.twitch_service is None:
            return False, "Broadcast page is unavailable."
        if not self._has_twitch_credentials():
            message = "Add a Twitch client ID and user access token before publishing title or category changes."
            if announce:
                self._page.set_twitch_status(False, message)
                self._page.set_stream_info_status(False, message)
            return False, message

        try:
            info = await self.twitch_service.update_channel_info(
                self._settings.twitch,
                title=self._settings.stream_title,
                category_id=self._settings.category_id,
            )
        except TwitchApiError as exc:
            message = f"Twitch update failed: {exc}"
            if announce:
                self._page.set_twitch_status(False, message)
                self._page.set_stream_info_status(False, message)
            return False, message

        self._apply_twitch_channel_info(info)
        if mode == "live_update":
            message = "Twitch title and category updated while live."
        elif mode == "before_go_live":
            message = "Twitch title and category updated before going live."
        else:
            message = "Twitch title and category applied."
        if announce:
            self._page.set_twitch_status(True, f"Synced Twitch channel info for {info.broadcaster_name or info.broadcaster_id}.")
            self._page.set_stream_info_status(True, message)
        return True, message

    def _apply_twitch_channel_info(self, info: TwitchChannelInfo) -> None:
        if self._page is None:
            return
        self._settings.twitch.broadcaster_id = info.broadcaster_id
        self._settings.stream_title = info.title
        self._settings.category_id = info.category_id
        self._settings.category_name = info.category_name
        self._page.load_channel_info(info)
        self._save_settings()

    def _save_current_as_preset(self) -> None:
        if self._page is None:
            return
        self._page._store_all()
        name = self._page.preset_name.text().strip()
        if not name:
            self._page.set_preset_status(False, "Enter a preset name before saving the current setup.")
            return

        preset = BroadcastPreset(
            name=name,
            title=self._settings.stream_title,
            category_id=self._settings.category_id,
            category_name=self._settings.category_name,
            apply_info_before_live=self._settings.apply_info_before_going_live,
        )
        self._settings.upsert_preset(preset)
        self._page.render_presets()
        self._page.set_preset_status(True, f"Saved preset '{name}'.")
        self._save_settings()
        self._sync_output_summary()

    def _update_selected_preset(self) -> None:
        if self._page is None:
            return
        selected_name = self._page.selected_preset_name()
        if not selected_name:
            self._page.set_preset_status(False, "Select a preset before updating it.")
            return
        self._page.preset_name.setText(selected_name)
        self._save_current_as_preset()
        self._page.set_preset_status(True, f"Updated preset '{selected_name}'.")

    def _delete_selected_preset(self) -> None:
        if self._page is None:
            return
        selected_name = self._page.selected_preset_name()
        if not selected_name:
            self._page.set_preset_status(False, "Select a preset before deleting it.")
            return
        removed = self._settings.remove_preset(selected_name)
        if not removed:
            self._page.set_preset_status(False, f"Could not find preset '{selected_name}'.")
            return
        self._page.preset_name.clear()
        self._page.render_presets()
        self._page.set_preset_status(True, f"Deleted preset '{selected_name}'.")
        self._save_settings()
        self._sync_output_summary()

    def _apply_selected_preset(self) -> None:
        if self._page is None:
            return
        preset = self._settings.preset_by_name(self._page.selected_preset_name())
        if preset is None:
            self._page.set_preset_status(False, "Select a preset before applying it.")
            return
        self._page.load_preset(preset)
        self._page.set_preset_status(True, f"Applied preset '{preset.name}'.")
        self._page.set_stream_info_status(True, f"Loaded '{preset.name}' into the current broadcast fields.")
        self._save_settings()
        self._sync_output_summary()

    def _has_twitch_credentials(self) -> bool:
        return self.twitch_service is not None and self.twitch_service.has_credentials(self._settings.twitch)

    def _selected_output(self) -> tuple[str, str, ObsService | StreamlabsService | None] | None:
        services = [
            ("obs", "OBS Studio", self.obs_service),
            ("streamlabs", "Streamlabs Desktop", self.streamlabs_service),
        ]
        if self._settings.output_target != "auto":
            for key, label, service in services:
                if key == self._settings.output_target:
                    return key, label, service
            return None

        for key, label, service in services:
            if service is not None and service.is_connected:
                return key, label, service
        for key, label, service in services:
            if service is not None:
                return key, label, service
        return None

    def _selected_output_key(self) -> str:
        selected = self._selected_output()
        return selected[0] if selected is not None else ""

    def _service_for_key(self, key: str) -> ObsService | StreamlabsService | None:
        if key == "obs":
            return self.obs_service
        if key == "streamlabs":
            return self.streamlabs_service
        return None

    @staticmethod
    def _fallback_status(detail: str, connected: bool) -> dict[str, object]:
        return {
            "connected": connected,
            "is_live": False,
            "status": "Offline" if connected else "Disconnected",
            "detail": detail,
        }

    def _save_settings(self) -> None:
        if self._context is None:
            return
        self._context.save_plugin_settings(self.plugin_id, self._settings.to_dict())
