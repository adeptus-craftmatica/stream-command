from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
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
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stream_control.plugins.base import AppPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.services.obs_service import ObsService
from stream_control.ui.widgets.common import PanelCard, configure_readonly_line, set_status_label


@dataclass(slots=True)
class ObsProductionPluginConfig:
    source_scene_name: str = ""
    override_scene_name: str = ""
    selected_audio_input: str = ""
    duck_target_db: int = -18
    fade_target_db: int = -60
    fade_duration_ms: int = 600
    snapshot_width: int = 1280
    snapshot_height: int = 720
    marker_name: str = ""
    last_snapshot_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "ObsProductionPluginConfig":
        return cls(
            source_scene_name=str(raw.get("source_scene_name", "")),
            override_scene_name=str(raw.get("override_scene_name", "")),
            selected_audio_input=str(raw.get("selected_audio_input", "")),
            duck_target_db=int(raw.get("duck_target_db", -18)),
            fade_target_db=int(raw.get("fade_target_db", -60)),
            fade_duration_ms=int(raw.get("fade_duration_ms", 600)),
            snapshot_width=int(raw.get("snapshot_width", 1280)),
            snapshot_height=int(raw.get("snapshot_height", 720)),
            marker_name=str(raw.get("marker_name", "")),
            last_snapshot_path=str(raw.get("last_snapshot_path", "")),
        )


class ObsProductionPage(QWidget):
    settings_changed = Signal()
    request_refresh_all = Signal()
    request_set_studio_mode = Signal(bool)
    request_make_program_live = Signal()
    request_load_preview = Signal()
    request_transition_preview = Signal()
    request_set_transition = Signal()
    request_set_transition_duration = Signal()
    request_refresh_override = Signal()
    request_save_override = Signal()
    request_clear_override = Signal()
    request_refresh_sources = Signal()
    request_toggle_source_item = Signal(int, bool)
    request_refresh_audio = Signal()
    request_set_audio_mute = Signal(bool)
    request_set_audio_volume = Signal(int)
    request_duck_audio = Signal()
    request_restore_audio = Signal()
    request_fade_audio = Signal()
    request_refresh_tools = Signal()
    request_start_replay = Signal()
    request_stop_replay = Signal()
    request_save_replay = Signal()
    request_create_marker = Signal()
    request_capture_program_snapshot = Signal()
    request_capture_selected_source_snapshot = Signal()
    request_switch_collection = Signal()
    request_switch_profile = Signal()

    def __init__(self, settings: ObsProductionPluginConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._rendering_sources = False
        self._rendering_audio = False
        self._live_program_scene = ""
        self._live_preview_scene = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title = QLabel("OBS Production", self)
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Run deeper OBS show control here with studio mode workflows, source visibility, mixer moves, and production tools in one dedicated panel.",
            self,
        )
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.connection_status = QLabel(self)
        self.connection_status.setWordWrap(True)
        layout.addWidget(self.connection_status)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("obsProductionTabs")
        self.tabs.addTab(self._build_studio_tab(), "Studio")
        self.tabs.addTab(self._build_sources_tab(), "Sources")
        self.tabs.addTab(self._build_audio_tab(), "Audio")
        self.tabs.addTab(self._build_tools_tab(), "Tools")
        layout.addWidget(self.tabs)
        layout.addStretch(1)

        self._apply_settings_to_fields()
        self.set_connection_status(
            False,
            "Connect OBS Studio on the Integrations page or start the built-in simulator to rehearse these production tools safely.",
        )
        self.set_production_state(
            {
                "connected": False,
                "studio_mode_enabled": False,
                "program_scene": "",
                "preview_scene": "",
                "transitions": [],
                "current_transition": "",
                "transition_duration": 300,
                "replay_buffer_active": False,
                "last_replay_path": "",
                "scene_collections": [],
                "current_scene_collection": "",
                "profiles": [],
                "current_profile": "",
                "record_active": False,
            }
        )
        self.set_source_items({"connected": False, "scene_name": "", "items": []})
        self.set_audio_inputs({"connected": False, "inputs": []})
        self.set_transition_override(
            {"scene_name": "", "has_override": False, "transition_name": "", "duration": 0}
        )

    def _build_studio_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_copy = QLabel(
            "Keep your live scene, preview scene, studio mode state, and transition behavior together so scene moves feel deliberate instead of frantic.",
            tab,
        )
        summary_copy.setObjectName("mutedText")
        summary_copy.setWordWrap(True)
        layout.addWidget(summary_copy)

        sections = QTabWidget(tab)
        sections.setObjectName("obsStudioSections")
        sections.addTab(self._build_studio_workflow_card(), "Workflow")
        sections.addTab(self._build_transition_card(), "Transitions")
        layout.addWidget(sections, 1)
        return tab

    def _build_sources_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_copy = QLabel(
            "Review one scene at a time, then flip source visibility with simple checkboxes so lower thirds, overlays, and utility sources stay under control.",
            tab,
        )
        summary_copy.setObjectName("mutedText")
        summary_copy.setWordWrap(True)
        layout.addWidget(summary_copy)

        layout.addWidget(self._build_sources_card())
        layout.addStretch(1)
        return tab

    def _build_audio_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_copy = QLabel(
            "Pick one OBS audio input at a time, then mute it, nudge levels, duck it under voice, or fade it smoothly when the moment needs it.",
            tab,
        )
        summary_copy.setObjectName("mutedText")
        summary_copy.setWordWrap(True)
        layout.addWidget(summary_copy)

        sections = QTabWidget(tab)
        sections.setObjectName("obsAudioSections")
        sections.addTab(self._build_audio_list_card(), "Inputs")
        sections.addTab(self._build_audio_controls_card(), "Controls")
        layout.addWidget(sections, 1)
        return tab

    def _build_tools_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_copy = QLabel(
            "Handle replay saves, clip markers, snapshots, scene collections, and profiles from one tools surface instead of digging through OBS menus mid-show.",
            tab,
        )
        summary_copy.setObjectName("mutedText")
        summary_copy.setWordWrap(True)
        layout.addWidget(summary_copy)

        sections = QTabWidget(tab)
        sections.setObjectName("obsToolsSections")
        sections.addTab(self._build_replay_card(), "Replay")
        sections.addTab(self._build_snapshot_card(), "Snapshots")
        sections.addTab(self._build_marker_card(), "Markers")
        sections.addTab(self._build_collections_card(), "Collections")
        layout.addWidget(sections, 1)
        return tab

    def _build_studio_workflow_card(self) -> PanelCard:
        card = PanelCard("Studio Workflow", self)
        card.layout.addWidget(
            self._muted_label(
                "Use Make Live Now for direct program switches, or load a preview first and transition it when you want the safer studio-mode workflow.",
                card,
            )
        )

        self.studio_mode = QCheckBox("Enable Studio Mode", card)
        self.studio_mode.toggled.connect(self.request_set_studio_mode.emit)
        card.layout.addWidget(self.studio_mode)

        self.program_scene_live = QLabel("Program: Not connected", card)
        self.program_scene_live.setObjectName("sectionTitle")
        card.layout.addWidget(self.program_scene_live)

        self.preview_scene_live = QLabel("Preview: Not loaded", card)
        self.preview_scene_live.setObjectName("mutedText")
        self.preview_scene_live.setWordWrap(True)
        card.layout.addWidget(self.preview_scene_live)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.program_scene_combo = QComboBox(card)
        self.preview_scene_combo = QComboBox(card)
        form.addRow("Program scene", self.program_scene_combo)
        form.addRow("Preview scene", self.preview_scene_combo)
        card.layout.addLayout(form)

        buttons = QHBoxLayout()
        make_live = QPushButton("Make Live", card)
        make_live.clicked.connect(self.request_make_program_live.emit)
        load_preview = QPushButton("Load Preview", card)
        load_preview.clicked.connect(self.request_load_preview.emit)
        transition = QPushButton("Take Live", card)
        transition.setObjectName("primaryButton")
        transition.clicked.connect(self.request_transition_preview.emit)
        refresh = QPushButton("Refresh", card)
        refresh.clicked.connect(self.request_refresh_all.emit)
        buttons.addWidget(make_live)
        buttons.addWidget(load_preview)
        buttons.addWidget(transition)
        buttons.addWidget(refresh)
        card.layout.addLayout(buttons)

        self.studio_status = QLabel(card)
        self.studio_status.setWordWrap(True)
        card.layout.addWidget(self.studio_status)
        return card

    def _build_transition_card(self) -> PanelCard:
        card = PanelCard("Transitions And Scene Presets", self)
        card.layout.addWidget(
            self._muted_label(
                "Set the active transition and duration globally, then optionally save a scene-specific transition preset for moments like BRB or intro handoffs.",
                card,
            )
        )

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.transition_combo = QComboBox(card)
        self.transition_duration = QSpinBox(card)
        self.transition_duration.setRange(50, 20_000)
        self.transition_duration.setSingleStep(50)
        form.addRow("Current transition", self.transition_combo)
        form.addRow("Duration (ms)", self.transition_duration)
        card.layout.addLayout(form)

        controls = QHBoxLayout()
        set_transition = QPushButton("Set Transition", card)
        set_transition.clicked.connect(self.request_set_transition.emit)
        set_duration = QPushButton("Set Duration", card)
        set_duration.clicked.connect(self.request_set_transition_duration.emit)
        controls.addWidget(set_transition)
        controls.addWidget(set_duration)
        controls.addStretch(1)
        card.layout.addLayout(controls)

        self.override_scene_combo = QComboBox(card)
        self.override_scene_combo.currentTextChanged.connect(self._handle_override_scene_changed)
        self.override_transition_combo = QComboBox(card)
        self.override_duration = QSpinBox(card)
        self.override_duration.setRange(50, 20_000)
        self.override_duration.setSingleStep(50)

        override_form = QFormLayout()
        override_form.setContentsMargins(0, 0, 0, 0)
        override_form.addRow("Preset scene", self.override_scene_combo)
        override_form.addRow("Scene transition", self.override_transition_combo)
        override_form.addRow("Scene duration (ms)", self.override_duration)
        card.layout.addLayout(override_form)

        override_buttons = QHBoxLayout()
        refresh_override = QPushButton("Refresh Preset", card)
        refresh_override.clicked.connect(self.request_refresh_override.emit)
        save_override = QPushButton("Save Preset", card)
        save_override.setObjectName("primaryButton")
        save_override.clicked.connect(self.request_save_override.emit)
        clear_override = QPushButton("Clear Preset", card)
        clear_override.clicked.connect(self.request_clear_override.emit)
        override_buttons.addWidget(refresh_override)
        override_buttons.addWidget(save_override)
        override_buttons.addWidget(clear_override)
        card.layout.addLayout(override_buttons)

        self.override_summary = QLabel(card)
        self.override_summary.setObjectName("mutedText")
        self.override_summary.setWordWrap(True)
        card.layout.addWidget(self.override_summary)

        self.override_status = QLabel(card)
        self.override_status.setWordWrap(True)
        card.layout.addWidget(self.override_status)
        return card

    def _build_sources_card(self) -> PanelCard:
        card = PanelCard("Source Visibility", self)
        card.layout.addWidget(
            self._muted_label(
                "Each checkbox mirrors one OBS scene item. Turn items on or off without opening the OBS scene tree while you are producing.",
                card,
            )
        )

        top = QHBoxLayout()
        self.source_scene_combo = QComboBox(card)
        self.source_scene_combo.currentTextChanged.connect(self._handle_source_scene_changed)
        refresh = QPushButton("Refresh Sources", card)
        refresh.clicked.connect(self.request_refresh_sources.emit)
        top.addWidget(QLabel("Scene", card))
        top.addWidget(self.source_scene_combo, 1)
        top.addWidget(refresh)
        card.layout.addLayout(top)

        self.source_list = QListWidget(card)
        self.source_list.itemChanged.connect(self._emit_source_toggle)
        self.source_list.itemSelectionChanged.connect(self._sync_selected_source_label)
        card.layout.addWidget(self.source_list)

        self.source_status = QLabel(card)
        self.source_status.setWordWrap(True)
        card.layout.addWidget(self.source_status)
        return card

    def _build_audio_list_card(self) -> PanelCard:
        card = PanelCard("Audio Inputs", self)
        card.layout.addWidget(
            self._muted_label(
                "This list focuses on inputs OBS will let us control like mixer channels, music buses, and browser-source audio.",
                card,
            )
        )

        refresh = QPushButton("Refresh Audio", card)
        refresh.clicked.connect(self.request_refresh_audio.emit)
        card.layout.addWidget(refresh)

        self.audio_list = QListWidget(card)
        self.audio_list.itemSelectionChanged.connect(self._load_selected_audio_input)
        card.layout.addWidget(self.audio_list)

        self.audio_list_status = QLabel(card)
        self.audio_list_status.setWordWrap(True)
        card.layout.addWidget(self.audio_list_status)
        return card

    def _build_audio_controls_card(self) -> PanelCard:
        card = PanelCard("Selected Input Controls", self)
        card.layout.addWidget(
            self._muted_label(
                "Use the selected input details here for quick level work instead of dragging tiny mixer faders in the middle of a scene change.",
                card,
            )
        )

        self.audio_selected_name = QLabel("No audio input selected", card)
        self.audio_selected_name.setObjectName("sectionTitle")
        card.layout.addWidget(self.audio_selected_name)

        self.audio_mute = QCheckBox("Mute selected input", card)
        self.audio_mute.toggled.connect(self.request_set_audio_mute.emit)
        card.layout.addWidget(self.audio_mute)

        volume_row = QHBoxLayout()
        self.audio_volume_slider = QSlider(Qt.Orientation.Horizontal, card)
        self.audio_volume_slider.setRange(-60, 6)
        self.audio_volume_slider.valueChanged.connect(self._update_audio_level_label)
        self.audio_volume_value = QLabel("0 dB", card)
        volume_row.addWidget(self.audio_volume_slider, 1)
        volume_row.addWidget(self.audio_volume_value)
        card.layout.addLayout(volume_row)

        apply_level = QPushButton("Apply Level", card)
        apply_level.clicked.connect(self._emit_audio_volume)
        card.layout.addWidget(apply_level)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.duck_target = QSpinBox(card)
        self.duck_target.setRange(-60, 6)
        self.duck_target.valueChanged.connect(self._store_processing_settings)
        self.fade_target = QSpinBox(card)
        self.fade_target.setRange(-60, 6)
        self.fade_target.valueChanged.connect(self._store_processing_settings)
        self.fade_duration = QSpinBox(card)
        self.fade_duration.setRange(100, 10_000)
        self.fade_duration.setSingleStep(100)
        self.fade_duration.valueChanged.connect(self._store_processing_settings)
        form.addRow("Duck target (dB)", self.duck_target)
        form.addRow("Fade target (dB)", self.fade_target)
        form.addRow("Fade duration (ms)", self.fade_duration)
        card.layout.addLayout(form)

        buttons = QHBoxLayout()
        duck = QPushButton("Duck", card)
        duck.clicked.connect(self.request_duck_audio.emit)
        restore = QPushButton("Restore Level", card)
        restore.clicked.connect(self.request_restore_audio.emit)
        fade = QPushButton("Fade", card)
        fade.clicked.connect(self.request_fade_audio.emit)
        buttons.addWidget(duck)
        buttons.addWidget(restore)
        buttons.addWidget(fade)
        card.layout.addLayout(buttons)

        self.audio_controls_status = QLabel(card)
        self.audio_controls_status.setWordWrap(True)
        card.layout.addWidget(self.audio_controls_status)
        return card

    def _build_replay_card(self) -> PanelCard:
        card = PanelCard("Replay Buffer", self)
        card.layout.addWidget(
            self._muted_label(
                "Keep replay buffer control close by so you can arm it, save a highlight, and confirm the last replay path without leaving the app.",
                card,
            )
        )

        self.replay_state = QLabel("Replay buffer: Not connected", card)
        self.replay_state.setObjectName("sectionTitle")
        card.layout.addWidget(self.replay_state)

        self.replay_last_path = QLabel("", card)
        self.replay_last_path.setObjectName("mutedText")
        self.replay_last_path.setWordWrap(True)
        configure_readonly_line(self.replay_last_path)
        card.layout.addWidget(self.replay_last_path)

        buttons = QHBoxLayout()
        start = QPushButton("Start", card)
        start.clicked.connect(self.request_start_replay.emit)
        stop = QPushButton("Stop", card)
        stop.clicked.connect(self.request_stop_replay.emit)
        save = QPushButton("Save", card)
        save.setObjectName("primaryButton")
        save.clicked.connect(self.request_save_replay.emit)
        buttons.addWidget(start)
        buttons.addWidget(stop)
        buttons.addWidget(save)
        card.layout.addLayout(buttons)

        self.replay_status = QLabel(card)
        self.replay_status.setWordWrap(True)
        card.layout.addWidget(self.replay_status)
        return card

    def _build_snapshot_card(self) -> PanelCard:
        card = PanelCard("Snapshots", self)
        card.layout.addWidget(
            self._muted_label(
                "Grab a snapshot of the current program scene or whichever scene item you have selected on the Sources tab, then keep the saved path visible here.",
                card,
            )
        )

        self.snapshot_program_summary = QLabel("Program scene snapshot target: Not connected", card)
        self.snapshot_program_summary.setObjectName("mutedText")
        self.snapshot_program_summary.setWordWrap(True)
        card.layout.addWidget(self.snapshot_program_summary)

        self.snapshot_selected_source = QLabel("Selected source snapshot target: No source selected", card)
        self.snapshot_selected_source.setObjectName("mutedText")
        self.snapshot_selected_source.setWordWrap(True)
        card.layout.addWidget(self.snapshot_selected_source)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.snapshot_width = QSpinBox(card)
        self.snapshot_width.setRange(64, 4096)
        self.snapshot_width.valueChanged.connect(self._store_snapshot_settings)
        self.snapshot_height = QSpinBox(card)
        self.snapshot_height.setRange(64, 4096)
        self.snapshot_height.valueChanged.connect(self._store_snapshot_settings)
        form.addRow("Width", self.snapshot_width)
        form.addRow("Height", self.snapshot_height)
        card.layout.addLayout(form)

        buttons = QHBoxLayout()
        program_shot = QPushButton("Snapshot Program", card)
        program_shot.clicked.connect(self.request_capture_program_snapshot.emit)
        source_shot = QPushButton("Snapshot Source", card)
        source_shot.clicked.connect(self.request_capture_selected_source_snapshot.emit)
        buttons.addWidget(program_shot)
        buttons.addWidget(source_shot)
        card.layout.addLayout(buttons)

        self.snapshot_path = QLabel("", card)
        self.snapshot_path.setObjectName("mutedText")
        self.snapshot_path.setWordWrap(True)
        configure_readonly_line(self.snapshot_path)
        card.layout.addWidget(self.snapshot_path)

        self.snapshot_status = QLabel(card)
        self.snapshot_status.setWordWrap(True)
        card.layout.addWidget(self.snapshot_status)
        return card

    def _build_marker_card(self) -> PanelCard:
        card = PanelCard("Clip Markers", self)
        card.layout.addWidget(
            self._muted_label(
                "OBS chapter markers are a clean way to flag moments during a recording workflow, and the simulator keeps this rehearsal-friendly even when you are offline.",
                card,
            )
        )

        row = QHBoxLayout()
        self.marker_name = QLineEdit(card)
        self.marker_name.setPlaceholderText("Marker name")
        self.marker_name.setText(self._settings.marker_name)
        self.marker_name.editingFinished.connect(self._store_marker_settings)
        marker_button = QPushButton("Create Marker", card)
        marker_button.setObjectName("primaryButton")
        marker_button.clicked.connect(self.request_create_marker.emit)
        row.addWidget(self.marker_name, 1)
        row.addWidget(marker_button)
        card.layout.addLayout(row)

        self.marker_status = QLabel(card)
        self.marker_status.setWordWrap(True)
        card.layout.addWidget(self.marker_status)
        return card

    def _build_collections_card(self) -> PanelCard:
        card = PanelCard("Collections And Profiles", self)
        card.layout.addWidget(
            self._muted_label(
                "Swap scene collections when a show format changes, or switch profiles when you need a different encoder, canvas, or audio setup.",
                card,
            )
        )

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.scene_collection_combo = QComboBox(card)
        self.profile_combo = QComboBox(card)
        form.addRow("Scene collection", self.scene_collection_combo)
        form.addRow("Profile", self.profile_combo)
        card.layout.addLayout(form)

        buttons = QHBoxLayout()
        collection_button = QPushButton("Switch Collection", card)
        collection_button.clicked.connect(self.request_switch_collection.emit)
        profile_button = QPushButton("Switch Profile", card)
        profile_button.clicked.connect(self.request_switch_profile.emit)
        refresh = QPushButton("Refresh", card)
        refresh.clicked.connect(self.request_refresh_tools.emit)
        buttons.addWidget(collection_button)
        buttons.addWidget(profile_button)
        buttons.addWidget(refresh)
        card.layout.addLayout(buttons)

        self.collection_status = QLabel(card)
        self.collection_status.setWordWrap(True)
        card.layout.addWidget(self.collection_status)
        return card

    def _apply_settings_to_fields(self) -> None:
        self.duck_target.setValue(self._settings.duck_target_db)
        self.fade_target.setValue(self._settings.fade_target_db)
        self.fade_duration.setValue(self._settings.fade_duration_ms)
        self.snapshot_width.setValue(self._settings.snapshot_width)
        self.snapshot_height.setValue(self._settings.snapshot_height)
        self.snapshot_path.setText(self._settings.last_snapshot_path)

    def set_connection_status(self, connected: bool, message: str) -> None:
        set_status_label(self.connection_status, connected, message)

    def set_production_state(self, payload: dict[str, object]) -> None:
        connected = bool(payload.get("connected", False))
        studio_mode = bool(payload.get("studio_mode_enabled", False))
        program_scene = str(payload.get("program_scene", "") or "")
        preview_scene = str(payload.get("preview_scene", "") or "")
        transitions = [str(item) for item in payload.get("transitions", [])]
        transition_name = str(payload.get("current_transition", "") or "")
        transition_duration = int(payload.get("transition_duration", 300) or 300)
        scene_collections = [str(item) for item in payload.get("scene_collections", [])]
        current_collection = str(payload.get("current_scene_collection", "") or "")
        profiles = [str(item) for item in payload.get("profiles", [])]
        current_profile = str(payload.get("current_profile", "") or "")

        blocker = QSignalBlocker(self.studio_mode)
        self.studio_mode.setChecked(studio_mode)
        del blocker

        self._live_program_scene = program_scene
        self._live_preview_scene = preview_scene

        self.program_scene_live.setText(
            f"Program: {program_scene}" if program_scene else "Program: Not connected"
        )
        if studio_mode and preview_scene:
            self.preview_scene_live.setText(f"Preview: {preview_scene}")
        elif studio_mode:
            self.preview_scene_live.setText("Preview: Ready but not loaded")
        else:
            self.preview_scene_live.setText("Preview: Studio mode is off")

        if program_scene and program_scene in self._combo_items(self.program_scene_combo):
            blocker = QSignalBlocker(self.program_scene_combo)
            self.program_scene_combo.setCurrentText(program_scene)
            del blocker
        if preview_scene and preview_scene in self._combo_items(self.preview_scene_combo):
            blocker = QSignalBlocker(self.preview_scene_combo)
            self.preview_scene_combo.setCurrentText(preview_scene)
            del blocker

        self._set_combo_items(self.transition_combo, transitions, transition_name)
        self._set_combo_items(self.override_transition_combo, transitions, transition_name)
        blocker = QSignalBlocker(self.transition_duration)
        self.transition_duration.setValue(transition_duration)
        del blocker
        blocker = QSignalBlocker(self.override_duration)
        if not self.override_status.text():
            self.override_duration.setValue(transition_duration)
        del blocker

        self._set_combo_items(self.scene_collection_combo, scene_collections, current_collection)
        self._set_combo_items(self.profile_combo, profiles, current_profile)

        replay_active = bool(payload.get("replay_buffer_active", False))
        replay_available = bool(payload.get("replay_buffer_available", True))
        replay_detail = str(payload.get("replay_buffer_detail", "") or "")
        if not replay_available:
            self.replay_state.setText("Replay buffer: Unavailable in OBS")
        else:
            self.replay_state.setText(
                "Replay buffer: Active" if replay_active else "Replay buffer: Standing by"
            )
        last_replay_path = str(payload.get("last_replay_path", "") or "")
        if last_replay_path:
            self.replay_last_path.setText(f"Last replay save: {last_replay_path}")
        elif not replay_available and replay_detail:
            self.replay_last_path.setText(replay_detail)
        else:
            self.replay_last_path.setText("Last replay save: Not saved yet")
        self.snapshot_program_summary.setText(
            f"Program scene snapshot target: {program_scene}" if program_scene else "Program scene snapshot target: Not connected"
        )
        self.set_studio_status(
            connected,
            "Studio controls are ready." if connected else str(payload.get("detail", "OBS Studio is disconnected.")),
        )
        self.set_collection_status(
            connected,
            (
                f"Current collection: {current_collection or 'Unavailable'}. Current profile: {current_profile or 'Unavailable'}."
                if connected
                else str(payload.get("detail", "OBS Studio is disconnected."))
            ),
        )

    def set_scenes(self, payload: dict[str, object]) -> None:
        scene_names = [str(scene["name"]) for scene in payload.get("scenes", [])]
        current_scene = str(payload.get("current", "") or "")
        preferred_source = self._settings.source_scene_name or current_scene
        preferred_override = self._settings.override_scene_name or current_scene
        preferred_program = current_scene or (scene_names[0] if scene_names else "")

        self._set_combo_items(self.program_scene_combo, scene_names, preferred_program)
        self._set_combo_items(self.preview_scene_combo, scene_names, current_scene or preferred_program)
        selected_source = self._set_combo_items(self.source_scene_combo, scene_names, preferred_source)
        selected_override = self._set_combo_items(self.override_scene_combo, scene_names, preferred_override)
        self._settings.source_scene_name = selected_source
        self._settings.override_scene_name = selected_override
        self.settings_changed.emit()

    def set_source_items(self, payload: dict[str, object]) -> None:
        self._rendering_sources = True
        self.source_list.clear()
        items = payload.get("items", [])
        for raw_item in items:
            item = QListWidgetItem(str(raw_item.get("name", "")))
            item.setData(Qt.ItemDataRole.UserRole, int(raw_item.get("id", 0)))
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.CheckState.Checked if bool(raw_item.get("enabled", False)) else Qt.CheckState.Unchecked
            )
            self.source_list.addItem(item)
        self._rendering_sources = False

        if self.source_list.count() > 0:
            self.source_list.setCurrentRow(0)
            self.set_source_status(True, f"Loaded {self.source_list.count()} scene items.")
        else:
            self.set_source_status(bool(payload.get("connected", False)), "No scene items are available for this scene.")
        self._sync_selected_source_label()

    def set_audio_inputs(self, payload: dict[str, object]) -> None:
        inputs = payload.get("inputs", [])
        selected_name = self._settings.selected_audio_input
        self.audio_list.clear()
        for raw_input in inputs:
            label = str(raw_input.get("name", ""))
            volume = float(raw_input.get("volume_db", 0.0) or 0.0)
            muted = bool(raw_input.get("muted", False))
            detail = "[Muted]" if muted else f"{volume:.1f} dB"
            if bool(raw_input.get("is_special", False)):
                detail += " [Special]"
            item = QListWidgetItem(f"{label}  {detail}")
            item.setData(Qt.ItemDataRole.UserRole, dict(raw_input))
            self.audio_list.addItem(item)

        if self.audio_list.count() == 0:
            self._settings.selected_audio_input = ""
            self._load_audio_controls(None)
            self.set_audio_list_status(bool(payload.get("connected", False)), "No controllable OBS audio inputs were found.")
            return

        selected_row = 0
        for index in range(self.audio_list.count()):
            item = self.audio_list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            if data.get("name") == selected_name:
                selected_row = index
                break
        self.audio_list.setCurrentRow(selected_row)
        self.set_audio_list_status(True, f"Loaded {self.audio_list.count()} OBS audio inputs.")
        self._load_selected_audio_input()

    def set_transition_override(self, payload: dict[str, object]) -> None:
        has_override = bool(payload.get("has_override", False))
        scene_name = str(payload.get("scene_name", "") or "")
        transition_name = str(payload.get("transition_name", "") or "")
        duration = int(payload.get("duration", 0) or 0)
        if has_override:
            self._set_combo_items(self.override_transition_combo, self._combo_items(self.override_transition_combo), transition_name)
            blocker = QSignalBlocker(self.override_duration)
            self.override_duration.setValue(max(50, duration))
            del blocker
            self.override_summary.setText(
                f"{scene_name} will use {transition_name} at {duration} ms when OBS applies its scene-specific transition override."
            )
            self.set_override_status(True, f"Loaded the saved transition preset for '{scene_name}'.")
        else:
            self.override_summary.setText(
                f"{scene_name or 'This scene'} is currently using the standard OBS transition settings."
            )
            self.set_override_status(True, f"No scene-specific transition preset is saved for '{scene_name or 'this scene'}'.")

    def set_studio_status(self, ok: bool, message: str) -> None:
        set_status_label(self.studio_status, ok, message)

    def set_source_status(self, ok: bool, message: str) -> None:
        set_status_label(self.source_status, ok, message)

    def set_audio_list_status(self, ok: bool, message: str) -> None:
        set_status_label(self.audio_list_status, ok, message)

    def set_audio_controls_status(self, ok: bool, message: str) -> None:
        set_status_label(self.audio_controls_status, ok, message)

    def set_override_status(self, ok: bool, message: str) -> None:
        set_status_label(self.override_status, ok, message)

    def set_replay_status(self, ok: bool, message: str) -> None:
        set_status_label(self.replay_status, ok, message)

    def set_snapshot_status(self, ok: bool, message: str, path: str = "") -> None:
        if path:
            self.snapshot_path.setText(path)
        set_status_label(self.snapshot_status, ok, message)

    def set_marker_status(self, ok: bool, message: str) -> None:
        set_status_label(self.marker_status, ok, message)

    def set_collection_status(self, ok: bool, message: str) -> None:
        set_status_label(self.collection_status, ok, message)

    def selected_program_scene(self) -> str:
        return self.program_scene_combo.currentText().strip()

    def selected_preview_scene(self) -> str:
        return self.preview_scene_combo.currentText().strip()

    def selected_source_scene(self) -> str:
        return self.source_scene_combo.currentText().strip()

    def selected_override_scene(self) -> str:
        return self.override_scene_combo.currentText().strip()

    def selected_override_transition(self) -> str:
        return self.override_transition_combo.currentText().strip()

    def selected_audio_input(self) -> str:
        item = self.audio_list.currentItem()
        if item is None:
            return ""
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        return str(data.get("name", "") or "")

    def selected_source_item(self) -> tuple[int, str]:
        item = self.source_list.currentItem()
        if item is None:
            return 0, ""
        return int(item.data(Qt.ItemDataRole.UserRole) or 0), item.text().strip()

    def selected_collection(self) -> str:
        return self.scene_collection_combo.currentText().strip()

    def selected_profile(self) -> str:
        return self.profile_combo.currentText().strip()

    def marker_label(self) -> str:
        return self.marker_name.text().strip()

    def snapshot_dimensions(self) -> tuple[int, int]:
        return self.snapshot_width.value(), self.snapshot_height.value()

    def live_program_scene(self) -> str:
        return self._live_program_scene

    def _handle_source_scene_changed(self, scene_name: str) -> None:
        self._settings.source_scene_name = scene_name.strip()
        self.settings_changed.emit()
        self.request_refresh_sources.emit()

    def _handle_override_scene_changed(self, scene_name: str) -> None:
        self._settings.override_scene_name = scene_name.strip()
        self.settings_changed.emit()
        self.request_refresh_override.emit()

    def _emit_source_toggle(self, item: QListWidgetItem) -> None:
        if self._rendering_sources:
            return
        self.request_toggle_source_item.emit(
            int(item.data(Qt.ItemDataRole.UserRole) or 0),
            item.checkState() == Qt.CheckState.Checked,
        )
        self._sync_selected_source_label()

    def _load_selected_audio_input(self) -> None:
        item = self.audio_list.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self._load_audio_controls(data)

    def _load_audio_controls(self, data: dict[str, object] | None) -> None:
        self._rendering_audio = True
        if not data:
            self.audio_selected_name.setText("No audio input selected")
            blocker = QSignalBlocker(self.audio_mute)
            self.audio_mute.setChecked(False)
            del blocker
            blocker = QSignalBlocker(self.audio_volume_slider)
            self.audio_volume_slider.setValue(0)
            del blocker
            self.audio_volume_value.setText("0 dB")
            self._rendering_audio = False
            return

        self._settings.selected_audio_input = str(data.get("name", "") or "")
        self.settings_changed.emit()
        self.audio_selected_name.setText(self._settings.selected_audio_input)
        blocker = QSignalBlocker(self.audio_mute)
        self.audio_mute.setChecked(bool(data.get("muted", False)))
        del blocker
        blocker = QSignalBlocker(self.audio_volume_slider)
        self.audio_volume_slider.setValue(int(round(float(data.get("volume_db", 0.0) or 0.0))))
        del blocker
        self.audio_volume_value.setText(f"{float(data.get('volume_db', 0.0) or 0.0):.1f} dB")
        self._rendering_audio = False

    def _update_audio_level_label(self, value: int) -> None:
        self.audio_volume_value.setText(f"{value} dB")

    def _emit_audio_volume(self) -> None:
        self.request_set_audio_volume.emit(self.audio_volume_slider.value())

    def _store_processing_settings(self, *_args: object) -> None:
        self._settings.duck_target_db = self.duck_target.value()
        self._settings.fade_target_db = self.fade_target.value()
        self._settings.fade_duration_ms = self.fade_duration.value()
        self.settings_changed.emit()

    def _store_snapshot_settings(self, *_args: object) -> None:
        self._settings.snapshot_width = self.snapshot_width.value()
        self._settings.snapshot_height = self.snapshot_height.value()
        self.settings_changed.emit()

    def _store_marker_settings(self) -> None:
        self._settings.marker_name = self.marker_name.text().strip()
        self.settings_changed.emit()

    def _sync_selected_source_label(self) -> None:
        _item_id, name = self.selected_source_item()
        if name:
            self.snapshot_selected_source.setText(f"Selected source snapshot target: {name}")
        else:
            self.snapshot_selected_source.setText("Selected source snapshot target: No source selected")

    @staticmethod
    def _muted_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("mutedText")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _combo_items(combo: QComboBox) -> list[str]:
        return [combo.itemText(index) for index in range(combo.count())]

    @staticmethod
    def _set_combo_items(combo: QComboBox, values: list[str], preferred: str) -> str:
        values = [value for value in values if value]
        blocker = QSignalBlocker(combo)
        combo.clear()
        combo.addItems(values)
        if not values:
            del blocker
            return ""
        target = preferred if preferred in values else values[0]
        combo.setCurrentText(target)
        del blocker
        return target


class ObsProductionPlugin(AppPlugin):
    plugin_id = "obs_production"
    display_name = "OBS Production"
    nav_order = 12
    load_order = 12

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._settings = ObsProductionPluginConfig()
        self._page: ObsProductionPage | None = None
        self.obs_service: ObsService | None = None

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self._settings = ObsProductionPluginConfig.from_dict(context.plugin_settings(self.plugin_id))
        self.obs_service = context.get_service("integrations.obs_service")

        self._page = ObsProductionPage(self._settings, context.qt_parent)
        self._page.settings_changed.connect(self._save_settings)
        self._page.request_refresh_all.connect(lambda: context.schedule(self._refresh_everything()))
        self._page.request_set_studio_mode.connect(lambda enabled: context.schedule(self._set_studio_mode(enabled)))
        self._page.request_make_program_live.connect(lambda: context.schedule(self._make_program_live()))
        self._page.request_load_preview.connect(lambda: context.schedule(self._load_preview()))
        self._page.request_transition_preview.connect(lambda: context.schedule(self._transition_preview()))
        self._page.request_set_transition.connect(lambda: context.schedule(self._set_transition()))
        self._page.request_set_transition_duration.connect(lambda: context.schedule(self._set_transition_duration()))
        self._page.request_refresh_override.connect(lambda: context.schedule(self._refresh_override()))
        self._page.request_save_override.connect(lambda: context.schedule(self._save_override()))
        self._page.request_clear_override.connect(lambda: context.schedule(self._clear_override()))
        self._page.request_refresh_sources.connect(lambda: context.schedule(self._refresh_sources()))
        self._page.request_toggle_source_item.connect(
            lambda item_id, enabled: context.schedule(self._toggle_source_item(item_id, enabled))
        )
        self._page.request_refresh_audio.connect(lambda: context.schedule(self._refresh_audio()))
        self._page.request_set_audio_mute.connect(lambda muted: context.schedule(self._set_audio_mute(muted)))
        self._page.request_set_audio_volume.connect(lambda level: context.schedule(self._set_audio_volume(level)))
        self._page.request_duck_audio.connect(lambda: context.schedule(self._duck_audio()))
        self._page.request_restore_audio.connect(lambda: context.schedule(self._restore_audio()))
        self._page.request_fade_audio.connect(lambda: context.schedule(self._fade_audio()))
        self._page.request_refresh_tools.connect(lambda: context.schedule(self._refresh_tools()))
        self._page.request_start_replay.connect(lambda: context.schedule(self._start_replay()))
        self._page.request_stop_replay.connect(lambda: context.schedule(self._stop_replay()))
        self._page.request_save_replay.connect(lambda: context.schedule(self._save_replay()))
        self._page.request_create_marker.connect(lambda: context.schedule(self._create_marker()))
        self._page.request_capture_program_snapshot.connect(
            lambda: context.schedule(self._capture_program_snapshot())
        )
        self._page.request_capture_selected_source_snapshot.connect(
            lambda: context.schedule(self._capture_selected_source_snapshot())
        )
        self._page.request_switch_collection.connect(lambda: context.schedule(self._switch_collection()))
        self._page.request_switch_profile.connect(lambda: context.schedule(self._switch_profile()))

        if self.obs_service is not None:
            self.obs_service.connection_changed.connect(self._handle_connection_changed)
            self.obs_service.scenes_changed.connect(self._handle_scenes_changed)
            self.obs_service.production_state_changed.connect(self._handle_production_state_changed)
            self.obs_service.source_items_changed.connect(self._handle_source_items_changed)
            self.obs_service.audio_inputs_changed.connect(self._handle_audio_inputs_changed)
            self.obs_service.scene_transition_override_changed.connect(self._handle_override_changed)

        context.register_service("obs_production.plugin", self)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def hotkey_actions(self) -> list[HotkeyAction]:
        if self._context is None:
            return []
        return [
            HotkeyAction(
                action_id="obs_production.transition_preview",
                label="Take the OBS preview scene live",
                handler=lambda: self._context.schedule(self._transition_preview()),
            ),
            HotkeyAction(
                action_id="obs_production.save_replay",
                label="Save the OBS replay buffer",
                handler=lambda: self._context.schedule(self._save_replay()),
            ),
        ]

    def on_plugins_loaded(self, _host) -> None:
        if self._context is not None and self.obs_service is not None and self.obs_service.is_connected:
            self._context.schedule(self._refresh_everything())

    async def _refresh_everything(self) -> None:
        if self.obs_service is None:
            return
        scenes = await self.obs_service.refresh_scenes()
        await self.obs_service.refresh_production_state()
        target_scene = self._page.selected_source_scene() if self._page is not None else ""
        target_scene = target_scene or str(scenes.get("current", "") or "")
        await self.obs_service.refresh_source_items(target_scene)
        await self.obs_service.refresh_audio_inputs()
        override_scene = self._page.selected_override_scene() if self._page is not None else ""
        override_scene = override_scene or target_scene
        if override_scene:
            await self.obs_service.refresh_scene_transition_override(override_scene)

    async def _set_studio_mode(self, enabled: bool) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.set_studio_mode_enabled(enabled)
        self._page.set_studio_status(bool(result["ok"]), str(result["detail"]))

    async def _make_program_live(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        scene_name = self._page.selected_program_scene()
        result = await self.obs_service.set_program_scene(scene_name)
        self._page.set_studio_status(bool(result["ok"]), str(result["detail"]))

    async def _load_preview(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        scene_name = self._page.selected_preview_scene()
        result = await self.obs_service.set_preview_scene(scene_name)
        self._page.set_studio_status(bool(result["ok"]), str(result["detail"]))

    async def _transition_preview(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.trigger_studio_transition()
        self._page.set_studio_status(bool(result["ok"]), str(result["detail"]))

    async def _set_transition(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.set_current_transition(self._page.transition_combo.currentText())
        self._page.set_studio_status(bool(result["ok"]), str(result["detail"]))

    async def _set_transition_duration(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.set_transition_duration(self._page.transition_duration.value())
        self._page.set_studio_status(bool(result["ok"]), str(result["detail"]))

    async def _refresh_override(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        scene_name = self._page.selected_override_scene()
        if not scene_name:
            self._page.set_override_status(False, "Choose a scene before loading a scene transition preset.")
            return
        payload = await self.obs_service.refresh_scene_transition_override(scene_name)
        if not payload.get("scene_name"):
            self._page.set_override_status(False, "Could not load the scene transition preset.")

    async def _save_override(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        scene_name = self._page.selected_override_scene()
        result = await self.obs_service.set_scene_transition_override(
            scene_name,
            self._page.selected_override_transition(),
            self._page.override_duration.value(),
        )
        self._page.set_override_status(bool(result["ok"]), str(result["detail"]))

    async def _clear_override(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        scene_name = self._page.selected_override_scene()
        result = await self.obs_service.set_scene_transition_override(scene_name, None, None)
        self._page.set_override_status(bool(result["ok"]), str(result["detail"]))

    async def _refresh_sources(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        scene_name = self._page.selected_source_scene()
        await self.obs_service.refresh_source_items(scene_name)

    async def _toggle_source_item(self, item_id: int, enabled: bool) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.set_source_enabled(
            self._page.selected_source_scene(),
            item_id,
            enabled,
        )
        self._page.set_source_status(bool(result["ok"]), str(result["detail"]))

    async def _refresh_audio(self) -> None:
        if self.obs_service is None:
            return
        await self.obs_service.refresh_audio_inputs()

    async def _set_audio_mute(self, muted: bool) -> None:
        if self.obs_service is None or self._page is None:
            return
        input_name = self._page.selected_audio_input()
        if not input_name:
            self._page.set_audio_controls_status(False, "Select an OBS audio input before muting it.")
            return
        result = await self.obs_service.set_input_mute(input_name, muted)
        self._page.set_audio_controls_status(bool(result["ok"]), str(result["detail"]))

    async def _set_audio_volume(self, level: int) -> None:
        if self.obs_service is None or self._page is None:
            return
        input_name = self._page.selected_audio_input()
        if not input_name:
            self._page.set_audio_controls_status(False, "Select an OBS audio input before setting its level.")
            return
        result = await self.obs_service.set_input_volume_db(input_name, float(level))
        self._page.set_audio_controls_status(bool(result["ok"]), str(result["detail"]))

    async def _duck_audio(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        input_name = self._page.selected_audio_input()
        if not input_name:
            self._page.set_audio_controls_status(False, "Select an OBS audio input before ducking it.")
            return
        result = await self.obs_service.duck_audio_input(input_name, float(self._settings.duck_target_db))
        self._page.set_audio_controls_status(bool(result["ok"]), str(result["detail"]))

    async def _restore_audio(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        input_name = self._page.selected_audio_input()
        if not input_name:
            self._page.set_audio_controls_status(False, "Select an OBS audio input before restoring it.")
            return
        result = await self.obs_service.restore_audio_input(input_name)
        self._page.set_audio_controls_status(bool(result["ok"]), str(result["detail"]))

    async def _fade_audio(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        input_name = self._page.selected_audio_input()
        if not input_name:
            self._page.set_audio_controls_status(False, "Select an OBS audio input before fading it.")
            return
        result = await self.obs_service.fade_audio_input(
            input_name,
            float(self._settings.fade_target_db),
            int(self._settings.fade_duration_ms),
        )
        self._page.set_audio_controls_status(bool(result["ok"]), str(result["detail"]))

    async def _refresh_tools(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        await self.obs_service.refresh_production_state()
        self._page.set_collection_status(True, "OBS tools refreshed.")

    async def _start_replay(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.start_replay_buffer()
        self._page.set_replay_status(bool(result["ok"]), str(result["detail"]))

    async def _stop_replay(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.stop_replay_buffer()
        self._page.set_replay_status(bool(result["ok"]), str(result["detail"]))

    async def _save_replay(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.save_replay_buffer()
        self._page.set_replay_status(bool(result["ok"]), str(result["detail"]))

    async def _create_marker(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.create_clip_marker(self._page.marker_label())
        self._page.set_marker_status(bool(result["ok"]), str(result["detail"]))

    async def _capture_program_snapshot(self) -> None:
        if self.obs_service is None or self._page is None or self._context is None:
            return
        target_scene = self._page.live_program_scene().strip()
        if not target_scene:
            self._page.set_snapshot_status(False, "OBS does not have a live program scene available yet.")
            return
        result = await self.obs_service.save_source_snapshot(
            target_scene,
            self._snapshot_dir(),
            *self._page.snapshot_dimensions(),
        )
        self._remember_snapshot_result(result)

    async def _capture_selected_source_snapshot(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        _item_id, source_name = self._page.selected_source_item()
        if not source_name:
            self._page.set_snapshot_status(False, "Select a source on the Sources tab before taking its snapshot.")
            return
        result = await self.obs_service.save_source_snapshot(
            source_name,
            self._snapshot_dir(),
            *self._page.snapshot_dimensions(),
        )
        self._remember_snapshot_result(result)

    async def _switch_collection(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.set_current_scene_collection(self._page.selected_collection())
        self._page.set_collection_status(bool(result["ok"]), str(result["detail"]))

    async def _switch_profile(self) -> None:
        if self.obs_service is None or self._page is None:
            return
        result = await self.obs_service.set_current_profile(self._page.selected_profile())
        self._page.set_collection_status(bool(result["ok"]), str(result["detail"]))

    def _remember_snapshot_result(self, result: dict[str, object]) -> None:
        if self._page is None:
            return
        path = str(result.get("path", "") or "")
        if path:
            self._settings.last_snapshot_path = path
            self._save_settings()
        self._page.set_snapshot_status(bool(result["ok"]), str(result["detail"]), path)

    def _handle_connection_changed(self, connected: bool, message: str) -> None:
        if self._page is None:
            return
        self._page.set_connection_status(connected, message)

    def _handle_scenes_changed(self, payload: dict[str, object]) -> None:
        if self._page is None:
            return
        self._page.set_scenes(payload)

    def _handle_production_state_changed(self, payload: dict[str, object]) -> None:
        if self._page is None:
            return
        self._page.set_production_state(payload)

    def _handle_source_items_changed(self, payload: dict[str, object]) -> None:
        if self._page is None:
            return
        self._page.set_source_items(payload)

    def _handle_audio_inputs_changed(self, payload: dict[str, object]) -> None:
        if self._page is None:
            return
        self._page.set_audio_inputs(payload)

    def _handle_override_changed(self, payload: dict[str, object]) -> None:
        if self._page is None:
            return
        self._page.set_transition_override(payload)

    def _snapshot_dir(self) -> Path:
        assert self._context is not None
        path = self._context.app_paths.root / "obs-snapshots"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_settings(self) -> None:
        if self._context is None:
            return
        self._context.save_plugin_settings(self.plugin_id, self._settings.to_dict())
