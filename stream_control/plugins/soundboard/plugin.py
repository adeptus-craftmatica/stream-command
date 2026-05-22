from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stream_control.core.audio import SYSTEM_DEFAULT_AUDIO_OUTPUT_ID, list_audio_output_options
from stream_control.core.models import (
    SoundboardBank,
    SoundboardPad,
    build_soundboard_bank,
    default_soundboard_bank,
    default_soundboard_pads,
)
from stream_control.plugins.base import AppPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.services.soundboard_service import SoundboardService
from stream_control.ui.widgets.common import PanelCard


@dataclass(slots=True)
class SoundboardPluginConfig:
    banks: list[SoundboardBank] = field(default_factory=lambda: [default_soundboard_bank()])
    selected_bank_id: str = "main"
    volume: int = 85
    output_device_id: str = SYSTEM_DEFAULT_AUDIO_OUTPUT_ID

    def to_dict(self) -> dict[str, object]:
        return {
            "banks": [asdict(bank) for bank in self.banks],
            "selected_bank_id": self.selected_bank_id,
            "volume": self.volume,
            "output_device_id": self.output_device_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "SoundboardPluginConfig":
        banks_raw = raw.get("banks", [])
        banks = [cls._bank_from_raw(item) for item in banks_raw if isinstance(item, dict)]
        banks = [bank for bank in banks if bank is not None]

        if not banks:
            legacy_pads_raw = raw.get("pads", default_soundboard_pads())
            legacy_pads = [
                pad if isinstance(pad, SoundboardPad) else SoundboardPad(**pad)
                for pad in legacy_pads_raw
                if isinstance(pad, (SoundboardPad, dict))
            ]
            if not legacy_pads:
                legacy_pads = default_soundboard_pads()
            banks = [SoundboardBank(id="main", name="Main Bank", pads=legacy_pads)]

        selected_bank_id = str(raw.get("selected_bank_id", banks[0].id) or banks[0].id)
        if selected_bank_id not in {bank.id for bank in banks}:
            selected_bank_id = banks[0].id

        return cls(
            banks=banks,
            selected_bank_id=selected_bank_id,
            volume=int(raw.get("volume", 85)),
            output_device_id=str(raw.get("output_device_id", SYSTEM_DEFAULT_AUDIO_OUTPUT_ID) or SYSTEM_DEFAULT_AUDIO_OUTPUT_ID),
        )

    @staticmethod
    def _bank_from_raw(raw: dict[str, object]) -> SoundboardBank | None:
        bank_id = str(raw.get("id", "")).strip()
        if not bank_id:
            return None
        name = str(raw.get("name", "")).strip() or "Soundboard Bank"
        pads_raw = raw.get("pads", [])
        pads = [
            pad if isinstance(pad, SoundboardPad) else SoundboardPad(**pad)
            for pad in pads_raw
            if isinstance(pad, (SoundboardPad, dict))
        ]
        if not pads:
            if bank_id == "main":
                pads = default_soundboard_pads()
            else:
                pads = build_soundboard_bank(name=name, bank_id=bank_id).pads
        return SoundboardBank(id=bank_id, name=name, pads=pads)

    def current_bank(self) -> SoundboardBank:
        for bank in self.banks:
            if bank.id == self.selected_bank_id:
                return bank
        self.selected_bank_id = self.banks[0].id
        return self.banks[0]


class SoundboardPage(QWidget):
    settings_changed = Signal()

    def __init__(self, settings: SoundboardPluginConfig, soundboard_service: SoundboardService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._soundboard_service = soundboard_service
        self._rendering_tabs = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title = QLabel("Soundboard")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Keep your main 3x3 pad bank ready for live use, then add named extra banks for different stream types when you need them.",
            self,
        )
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        controls = PanelCard("Soundboard Mixer", self)
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Pad volume", controls))
        slider = QSlider(Qt.Orientation.Horizontal, controls)
        slider.setRange(0, 100)
        slider.setValue(self._settings.volume)
        slider.valueChanged.connect(self._set_volume)
        volume_row.addWidget(slider)
        controls.layout.addLayout(volume_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Audio output", controls))
        self.output_device = QComboBox(controls)
        self.output_device.currentIndexChanged.connect(self._set_output_device)
        output_row.addWidget(self.output_device, 1)
        refresh_outputs = QPushButton("Refresh Outputs", controls)
        refresh_outputs.clicked.connect(self._populate_output_devices)
        output_row.addWidget(refresh_outputs)
        controls.layout.addLayout(output_row)

        bank_row = QHBoxLayout()
        bank_row.addWidget(QLabel("Current bank", controls))
        self.bank_name = QLineEdit(controls)
        self.bank_name.setPlaceholderText("Bank name")
        self.bank_name.editingFinished.connect(self._rename_current_bank)
        self.add_bank_button = QPushButton("Add Bank", controls)
        self.add_bank_button.clicked.connect(self._add_bank)
        self.remove_bank_button = QPushButton("Remove Current", controls)
        self.remove_bank_button.clicked.connect(self._remove_current_bank)
        bank_row.addWidget(self.bank_name, 1)
        bank_row.addWidget(self.add_bank_button)
        bank_row.addWidget(self.remove_bank_button)
        controls.layout.addLayout(bank_row)

        self.message_label = QLabel("", controls)
        self.message_label.setObjectName("mutedText")
        self.message_label.setWordWrap(True)
        controls.layout.addWidget(self.message_label)
        layout.addWidget(controls)

        pads_card = PanelCard("Pad Banks", self)
        self.pad_tabs = QTabWidget(pads_card)
        self.pad_tabs.setObjectName("soundboardPadTabs")
        self.pad_tabs.currentChanged.connect(self._handle_bank_tab_changed)
        pads_card.layout.addWidget(self.pad_tabs)
        layout.addWidget(pads_card, 1)

        self._soundboard_service.status_message.connect(self.message_label.setText)
        self._populate_output_devices()
        self._render_banks()

    def _render_banks(self) -> None:
        self._rendering_tabs = True
        while self.pad_tabs.count():
            page = self.pad_tabs.widget(0)
            self.pad_tabs.removeTab(0)
            if page is not None:
                page.deleteLater()

        for bank in self._settings.banks:
            page = QWidget(self.pad_tabs)
            grid = QGridLayout(page)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(12)

            for index, pad in enumerate(bank.pads):
                card = PanelCard(parent=page)
                card.layout.setSpacing(6)

                title = QLabel(pad.label, card)
                title.setObjectName("sectionTitle")
                clip_label = QLabel(pad.file_path or "No clip assigned", card)
                clip_label.setWordWrap(True)
                clip_label.setObjectName("mutedText")
                hotkey_label = QLabel(f"Hotkey target: {bank.name} / Slot {index + 1}", card)
                hotkey_label.setObjectName("mutedText")
                hotkey_label.setWordWrap(True)

                row = QHBoxLayout()
                trigger = QPushButton("Play", card)
                trigger.setObjectName("primaryButton")
                trigger.clicked.connect(
                    lambda _checked=False, pad_id=pad.id: self._soundboard_service.trigger_pad(pad_id)
                )
                assign = QPushButton("Assign", card)
                assign.clicked.connect(lambda _checked=False, pad_id=pad.id: self._assign_clip(pad_id))
                clear = QPushButton("Clear", card)
                clear.clicked.connect(lambda _checked=False, pad_id=pad.id: self._clear_clip(pad_id))
                row.addWidget(trigger)
                row.addWidget(assign)
                row.addWidget(clear)

                card.layout.addWidget(title)
                card.layout.addWidget(clip_label)
                card.layout.addWidget(hotkey_label)
                card.layout.addLayout(row)
                grid.addWidget(card, index // 3, index % 3)

            for column in range(3):
                grid.setColumnStretch(column, 1)
            for row_index in range(3):
                grid.setRowStretch(row_index, 1)
            self.pad_tabs.addTab(page, bank.name)

        current_index = 0
        for index, bank in enumerate(self._settings.banks):
            if bank.id == self._settings.selected_bank_id:
                current_index = index
                break
        self.pad_tabs.setCurrentIndex(current_index)
        self._rendering_tabs = False
        self._sync_bank_controls()

    def _handle_bank_tab_changed(self, index: int) -> None:
        if self._rendering_tabs or index < 0 or index >= len(self._settings.banks):
            return
        self._settings.selected_bank_id = self._settings.banks[index].id
        self._sync_bank_controls()
        self.settings_changed.emit()

    def _sync_bank_controls(self) -> None:
        bank = self._settings.current_bank()
        self.bank_name.setText(bank.name)
        self.remove_bank_button.setEnabled(len(self._settings.banks) > 1)

    def _add_bank(self) -> None:
        next_number = len(self._settings.banks) + 1
        bank_id = f"bank_{next_number}"
        while any(existing.id == bank_id for existing in self._settings.banks):
            next_number += 1
            bank_id = f"bank_{next_number}"
        bank = build_soundboard_bank(name=f"Bank {next_number}", bank_id=bank_id)
        self._settings.banks.append(bank)
        self._settings.selected_bank_id = bank.id
        self._render_banks()
        self.message_label.setText(f"Added {bank.name}.")
        self.settings_changed.emit()

    def _remove_current_bank(self) -> None:
        if len(self._settings.banks) <= 1:
            self.message_label.setText("Keep at least one soundboard bank available.")
            return
        current_bank = self._settings.current_bank()
        self._settings.banks = [bank for bank in self._settings.banks if bank.id != current_bank.id]
        self._settings.selected_bank_id = self._settings.banks[0].id
        self._render_banks()
        self.message_label.setText(f"Removed {current_bank.name}.")
        self.settings_changed.emit()

    def _rename_current_bank(self) -> None:
        bank = self._settings.current_bank()
        new_name = self.bank_name.text().strip() or bank.name
        if new_name == bank.name:
            self.bank_name.setText(bank.name)
            return
        bank.name = new_name
        current_index = self.pad_tabs.currentIndex()
        if current_index >= 0:
            self.pad_tabs.setTabText(current_index, new_name)
        self.settings_changed.emit()

    def _assign_clip(self, pad_id: str) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a soundboard clip",
            "",
            "Audio Files (*.aac *.flac *.m4a *.mp3 *.ogg *.wav *.wma)",
        )
        if not file_path:
            return
        for bank in self._settings.banks:
            for pad in bank.pads:
                if pad.id != pad_id:
                    continue
                pad.file_path = str(Path(file_path).resolve())
                if not pad.label or pad.label.startswith("Pad "):
                    pad.label = Path(file_path).stem.replace("_", " ").replace("-", " ")
                self._render_banks()
                self.message_label.setText(f"Assigned clip to {pad.label}.")
                self.settings_changed.emit()
                return

    def _clear_clip(self, pad_id: str) -> None:
        for bank in self._settings.banks:
            for pad in bank.pads:
                if pad.id != pad_id:
                    continue
                pad.file_path = ""
                self._render_banks()
                self.message_label.setText(f"Cleared {pad.label}.")
                self.settings_changed.emit()
                return

    def _set_volume(self, value: int) -> None:
        self._settings.volume = value
        self._soundboard_service.set_volume(value)
        self.settings_changed.emit()

    def _populate_output_devices(self) -> None:
        current_device_id = self._settings.output_device_id
        self.output_device.blockSignals(True)
        self.output_device.clear()
        for option in list_audio_output_options():
            self.output_device.addItem(option.label, option.device_id)
        selected_index = self.output_device.findData(current_device_id)
        if selected_index < 0:
            selected_index = 0
        self.output_device.setCurrentIndex(selected_index)
        self.output_device.blockSignals(False)
        self._set_output_device()

    def _set_output_device(self, *_args: object) -> None:
        self._settings.output_device_id = str(self.output_device.currentData() or SYSTEM_DEFAULT_AUDIO_OUTPUT_ID)
        self._soundboard_service.set_output_device(self._settings.output_device_id)
        self.settings_changed.emit()


class SoundboardPlugin(AppPlugin):
    plugin_id = "soundboard"
    display_name = "Soundboard"
    nav_order = 30
    load_order = 30

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._settings = SoundboardPluginConfig()
        self._page: SoundboardPage | None = None
        self.soundboard_service: SoundboardService | None = None

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self._settings = SoundboardPluginConfig.from_dict(context.plugin_settings(self.plugin_id))

        self.soundboard_service = SoundboardService(context.qt_parent)
        self.soundboard_service.set_pads(self._all_pads())
        self.soundboard_service.set_volume(self._settings.volume)
        self.soundboard_service.set_output_device(self._settings.output_device_id)

        self._page = SoundboardPage(self._settings, self.soundboard_service, context.qt_parent)
        self._page.settings_changed.connect(self._handle_settings_changed)

        context.register_service("soundboard.service", self.soundboard_service)
        context.register_service("soundboard.plugin", self)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def hotkey_actions(self) -> list[HotkeyAction]:
        if self.soundboard_service is None:
            return []
        multiple_banks = len(self._settings.banks) > 1
        actions: list[HotkeyAction] = []
        for bank in self._settings.banks:
            for index, pad in enumerate(bank.pads, start=1):
                if multiple_banks:
                    label = f"Trigger {pad.label} ({bank.name})"
                else:
                    label = f"Trigger {pad.label}"
                default_combo = f"<ctrl>+<alt>+{index}" if bank.id == "main" else ""
                actions.append(
                    HotkeyAction(
                        action_id=pad.hotkey_action_id,
                        label=label,
                        handler=lambda pad_id=pad.id: self.soundboard_service.trigger_pad(pad_id),
                        default_combo=default_combo,
                        default_enabled=False,
                    )
                )
        return actions

    def _all_pads(self) -> list[SoundboardPad]:
        return [pad for bank in self._settings.banks for pad in bank.pads]

    def _handle_settings_changed(self) -> None:
        if self.soundboard_service is not None:
            self.soundboard_service.set_pads(self._all_pads())
            self.soundboard_service.set_volume(self._settings.volume)
            self.soundboard_service.set_output_device(self._settings.output_device_id)
        self._save_settings()
        hotkeys_plugin = self._context.get_plugin("hotkeys") if self._context is not None else None
        if hotkeys_plugin is not None:
            hotkeys_plugin.reload_actions()

    def _save_settings(self) -> None:
        if self._context is None:
            return
        self._context.save_plugin_settings(self.plugin_id, self._settings.to_dict())
