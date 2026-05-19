from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stream_control.core.models import HotkeyBinding
from stream_control.plugins.base import AppPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.plugins.host import PluginHost
from stream_control.services.hotkey_service import HotkeyService
from stream_control.ui.widgets.common import (
    PanelCard,
    capture_table_column_widths,
    restore_table_column_widths,
)


def _new_custom_hotkey_id() -> str:
    return uuid4().hex


@dataclass(slots=True)
class CustomHotkey:
    id: str = field(default_factory=_new_custom_hotkey_id)
    label: str = "Custom Hotkey"
    combo: str = ""
    enabled: bool = True
    primary_action_id: str = ""
    secondary_action_id: str = ""
    delay_ms: int = 250

    def as_binding(self) -> HotkeyBinding:
        return HotkeyBinding(
            action_id=f"hotkeys.custom.{self.id}",
            label=self.label.strip() or "Custom Hotkey",
            combo=self.combo,
            enabled=self.enabled,
        )


@dataclass(slots=True)
class HotkeysPluginConfig:
    bindings: list[HotkeyBinding] = field(default_factory=list)
    custom_hotkeys: list[CustomHotkey] = field(default_factory=list)
    bindings_column_widths: list[int] = field(default_factory=list)
    custom_column_widths: list[int] = field(default_factory=list)
    filter_text: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "bindings": [asdict(binding) for binding in self.bindings],
            "custom_hotkeys": [asdict(binding) for binding in self.custom_hotkeys],
            "bindings_column_widths": list(self.bindings_column_widths),
            "custom_column_widths": list(self.custom_column_widths),
            "filter_text": self.filter_text,
        }

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, object],
        available_actions: dict[str, HotkeyAction],
    ) -> "HotkeysPluginConfig":
        stored_bindings = {
            binding["action_id"] if isinstance(binding, dict) else binding.action_id:
            binding if isinstance(binding, HotkeyBinding) else HotkeyBinding(**binding)
            for binding in raw.get("bindings", [])
        }

        merged: list[HotkeyBinding] = []
        for action_id, action in available_actions.items():
            existing = stored_bindings.get(action_id)
            if existing is None:
                merged.append(
                    HotkeyBinding(
                        action_id=action.action_id,
                        label=action.label,
                        combo=action.default_combo,
                        enabled=action.default_enabled,
                    )
                )
                continue
            merged.append(
                HotkeyBinding(
                    action_id=action.action_id,
                    label=action.label,
                    combo=existing.combo,
                    enabled=existing.enabled,
                )
            )

        custom_hotkeys = [
            CustomHotkey(**item)
            for item in raw.get("custom_hotkeys", [])
            if isinstance(item, dict)
        ]

        return cls(
            bindings=merged,
            custom_hotkeys=custom_hotkeys,
            bindings_column_widths=[max(40, int(width)) for width in raw.get("bindings_column_widths", [])],
            custom_column_widths=[max(40, int(width)) for width in raw.get("custom_column_widths", [])],
            filter_text=str(raw.get("filter_text", "")).strip(),
        )


class HotkeysPage(QWidget):
    apply_requested = Signal()
    reset_requested = Signal()
    settings_changed = Signal()

    def __init__(self, hotkey_service: HotkeyService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hotkey_service = hotkey_service
        self._action_choices: list[tuple[str, str]] = []
        self._rendering_bindings = False
        self._rendering_custom = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title = QLabel("Hotkeys", self)
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Map global shortcuts to plugin actions, then layer your own custom shortcut sequences on top. On macOS, the app may need Accessibility permission.",
            self,
        )
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        shortcut_note = self._muted_label(
            "Shortcut format still follows pynput syntax like <ctrl>+<alt>+p. Duplicate shortcuts are detected automatically and skipped instead of silently overriding each other.",
            self,
        )
        layout.addWidget(shortcut_note)

        action_row = QHBoxLayout()
        apply_button = QPushButton("Apply Hotkeys", self)
        apply_button.setObjectName("primaryButton")
        apply_button.clicked.connect(self.apply_requested.emit)
        reset_button = QPushButton("Reset Default Bindings", self)
        reset_button.clicked.connect(self.reset_requested.emit)
        action_row.addWidget(apply_button)
        action_row.addWidget(reset_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        self.status_label.setText(self._hotkey_service.last_status)
        layout.addWidget(self.status_label)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("hotkeyTabs")
        self.tabs.addTab(self._build_bindings_tab(), "Action Bindings")
        self.tabs.addTab(self._build_custom_tab(), "Custom Hotkeys")
        layout.addWidget(self.tabs, 1)

        self._hotkey_service.status_changed.connect(self.status_label.setText)

    def _build_bindings_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QGridLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(16)

        card = PanelCard("Plugin Actions", tab)
        controls = QHBoxLayout()
        self.filter_input = QLineEdit(card)
        self.filter_input.setPlaceholderText("Filter actions by plugin or label")
        self.filter_input.textChanged.connect(self._filter_bindings_rows)
        controls.addWidget(self.filter_input)
        card.layout.addLayout(controls)

        self.table = QTableWidget(0, 3, card)
        self.table.setHorizontalHeaderLabels(["Action", "Shortcut", "Enabled"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().sectionResized.connect(self._emit_settings_changed)
        self.table.setMinimumHeight(280)
        self.table.itemChanged.connect(self._handle_bindings_changed)
        card.layout.addWidget(self.table, 1)

        self.binding_summary = QLabel(card)
        self.binding_summary.setObjectName("mutedText")
        self.binding_summary.setWordWrap(True)
        card.layout.addWidget(self.binding_summary)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(card, 0, 0)
        layout.addWidget(self._build_bindings_overview_card(tab), 0, 1)
        layout.setColumnStretch(0, 5)
        layout.setColumnStretch(1, 2)
        layout.setRowStretch(0, 1)
        return tab

    def _build_custom_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QGridLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(16)

        card = PanelCard("Custom Hotkey Sequences", tab)
        card.layout.addWidget(
            self._muted_label(
                "Create your own shortcut rows here. Each custom hotkey can trigger one primary action and an optional follow-up action after a small delay.",
                card,
            )
        )

        self.custom_table = QTableWidget(0, 6, card)
        self.custom_table.setHorizontalHeaderLabels(
            ["Enabled", "Label", "Shortcut", "Primary Action", "Follow-up Action", "Delay (ms)"]
        )
        self.custom_table.verticalHeader().setVisible(False)
        self.custom_table.setAlternatingRowColors(True)
        self.custom_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.custom_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.custom_table.horizontalHeader().sectionResized.connect(self._emit_settings_changed)
        self.custom_table.setMinimumHeight(280)
        self.custom_table.itemChanged.connect(self._handle_custom_changed)
        card.layout.addWidget(self.custom_table, 1)

        buttons = QHBoxLayout()
        add_button = QPushButton("Add Custom Hotkey", card)
        add_button.clicked.connect(self._add_custom_row)
        remove_button = QPushButton("Remove Selected", card)
        remove_button.clicked.connect(self._remove_selected_custom_row)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)

        self.custom_summary = QLabel(card)
        self.custom_summary.setObjectName("mutedText")
        self.custom_summary.setWordWrap(True)
        card.layout.addWidget(self.custom_summary)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(card, 0, 0)
        layout.addWidget(self._build_custom_overview_card(tab), 0, 1)
        layout.setColumnStretch(0, 5)
        layout.setColumnStretch(1, 2)
        layout.setRowStretch(0, 1)
        return tab

    def _build_bindings_overview_card(self, parent: QWidget) -> PanelCard:
        card = PanelCard("Overview", parent)
        card.layout.addWidget(self._muted_label("Keep an eye on how many plugin actions are available, visible, and ready to register.", card))

        self.bindings_total_value = self._overview_value("0")
        self.bindings_visible_value = self._overview_value("0")
        self.bindings_enabled_value = self._overview_value("0")
        self.bindings_filter_value = self._overview_value("No filter")

        card.layout.addWidget(self._overview_pair("Plugin actions", self.bindings_total_value, card))
        card.layout.addWidget(self._overview_pair("Visible rows", self.bindings_visible_value, card))
        card.layout.addWidget(self._overview_pair("Enabled with shortcuts", self.bindings_enabled_value, card))
        card.layout.addWidget(self._overview_pair("Filter", self.bindings_filter_value, card))
        card.layout.addStretch(1)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return card

    def _build_custom_overview_card(self, parent: QWidget) -> PanelCard:
        card = PanelCard("Sequence Overview", parent)
        card.layout.addWidget(
            self._muted_label(
                "Custom hotkeys can stay lightweight here while still showing how many multi-step sequences are ready.",
                card,
            )
        )

        self.custom_total_value = self._overview_value("0")
        self.custom_ready_value = self._overview_value("0")
        self.custom_multi_step_value = self._overview_value("0")
        self.custom_incomplete_value = self._overview_value("0")

        card.layout.addWidget(self._overview_pair("Custom rows", self.custom_total_value, card))
        card.layout.addWidget(self._overview_pair("Ready to register", self.custom_ready_value, card))
        card.layout.addWidget(self._overview_pair("Two-step sequences", self.custom_multi_step_value, card))
        card.layout.addWidget(self._overview_pair("Still incomplete", self.custom_incomplete_value, card))
        card.layout.addStretch(1)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return card

    def set_available_actions(self, actions: dict[str, HotkeyAction]) -> None:
        self._action_choices = [("", "No action")]
        self._action_choices.extend(
            sorted(
                ((action_id, action.label) for action_id, action in actions.items()),
                key=lambda item: item[1].lower(),
            )
        )

    def set_bindings(self, bindings: list[HotkeyBinding], filter_text: str = "") -> None:
        self._rendering_bindings = True
        self.table.setRowCount(len(bindings))
        for row, binding in enumerate(bindings):
            action_item = QTableWidgetItem(binding.label)
            action_item.setData(Qt.ItemDataRole.UserRole, binding.action_id)
            action_item.setFlags(action_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            shortcut_item = QTableWidgetItem(binding.combo)
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            enabled_item.setCheckState(Qt.CheckState.Checked if binding.enabled else Qt.CheckState.Unchecked)

            self.table.setItem(row, 0, action_item)
            self.table.setItem(row, 1, shortcut_item)
            self.table.setItem(row, 2, enabled_item)
        filter_blocker = QSignalBlocker(self.filter_input)
        self.filter_input.setText(filter_text)
        del filter_blocker
        self._filter_bindings_rows()
        self._update_binding_overview()
        self._rendering_bindings = False

    def bindings(self) -> list[HotkeyBinding]:
        bindings: list[HotkeyBinding] = []
        for row in range(self.table.rowCount()):
            action_item = self.table.item(row, 0)
            shortcut_item = self.table.item(row, 1)
            enabled_item = self.table.item(row, 2)
            if action_item is None or shortcut_item is None or enabled_item is None:
                continue
            bindings.append(
                HotkeyBinding(
                    action_id=str(action_item.data(Qt.ItemDataRole.UserRole) or ""),
                    label=action_item.text(),
                    combo=shortcut_item.text().strip(),
                    enabled=enabled_item.checkState() == Qt.CheckState.Checked,
                )
            )
        return bindings

    def set_custom_hotkeys(self, custom_hotkeys: list[CustomHotkey]) -> None:
        self._rendering_custom = True
        self.custom_table.setRowCount(len(custom_hotkeys))
        for row, hotkey in enumerate(custom_hotkeys):
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            enabled_item.setCheckState(Qt.CheckState.Checked if hotkey.enabled else Qt.CheckState.Unchecked)
            label_item = QTableWidgetItem(hotkey.label)
            label_item.setData(Qt.ItemDataRole.UserRole, hotkey.id)
            combo_item = QTableWidgetItem(hotkey.combo)

            self.custom_table.setItem(row, 0, enabled_item)
            self.custom_table.setItem(row, 1, label_item)
            self.custom_table.setItem(row, 2, combo_item)

            primary_combo = self._make_action_combo(hotkey.primary_action_id)
            followup_combo = self._make_action_combo(hotkey.secondary_action_id)
            delay_spin = QSpinBox(self.custom_table)
            delay_spin.setRange(0, 10_000)
            delay_spin.setSingleStep(50)
            delay_spin.setValue(int(hotkey.delay_ms))
            delay_spin.valueChanged.connect(self._emit_settings_changed)

            self.custom_table.setCellWidget(row, 3, primary_combo)
            self.custom_table.setCellWidget(row, 4, followup_combo)
            self.custom_table.setCellWidget(row, 5, delay_spin)

        self._rendering_custom = False
        self._update_custom_summary()

    def custom_hotkeys(self) -> list[CustomHotkey]:
        custom: list[CustomHotkey] = []
        for row in range(self.custom_table.rowCount()):
            enabled_item = self.custom_table.item(row, 0)
            label_item = self.custom_table.item(row, 1)
            combo_item = self.custom_table.item(row, 2)
            primary_combo = self.custom_table.cellWidget(row, 3)
            followup_combo = self.custom_table.cellWidget(row, 4)
            delay_spin = self.custom_table.cellWidget(row, 5)
            custom_id = ""
            delay_ms = 250

            if label_item is not None:
                custom_id = str(label_item.data(Qt.ItemDataRole.UserRole) or "")
            if isinstance(delay_spin, QSpinBox):
                delay_ms = int(delay_spin.value())

            custom.append(
                CustomHotkey(
                    id=custom_id or _new_custom_hotkey_id(),
                    label=label_item.text().strip() if label_item is not None else "Custom Hotkey",
                    combo=combo_item.text().strip() if combo_item is not None else "",
                    enabled=enabled_item is not None and enabled_item.checkState() == Qt.CheckState.Checked,
                    primary_action_id=self._selected_action_id(primary_combo),
                    secondary_action_id=self._selected_action_id(followup_combo),
                    delay_ms=delay_ms,
                )
            )
        return custom

    def set_bindings_column_widths(self, widths: list[int]) -> None:
        if widths:
            restore_table_column_widths(self.table, widths)
            return
        self._apply_default_binding_widths()

    def set_custom_column_widths(self, widths: list[int]) -> None:
        if widths:
            restore_table_column_widths(self.custom_table, widths)
            return
        self._apply_default_custom_widths()

    def bindings_column_widths(self) -> list[int]:
        return capture_table_column_widths(self.table)

    def custom_column_widths(self) -> list[int]:
        return capture_table_column_widths(self.custom_table)

    def _make_action_combo(self, selected_action_id: str) -> QComboBox:
        combo = QComboBox(self.custom_table)
        for action_id, label in self._action_choices:
            combo.addItem(label, action_id)
        target_index = max(0, combo.findData(selected_action_id))
        combo.setCurrentIndex(target_index)
        combo.currentIndexChanged.connect(self._emit_settings_changed)
        return combo

    @staticmethod
    def _selected_action_id(widget: QWidget | None) -> str:
        if isinstance(widget, QComboBox):
            return str(widget.currentData() or "")
        return ""

    def _filter_bindings_rows(self) -> None:
        filter_text = self.filter_input.text().strip().lower()
        visible_rows = 0
        for row in range(self.table.rowCount()):
            action_item = self.table.item(row, 0)
            shortcut_item = self.table.item(row, 1)
            haystack = f"{action_item.text() if action_item is not None else ''} {shortcut_item.text() if shortcut_item is not None else ''}".lower()
            hidden = bool(filter_text) and filter_text not in haystack
            self.table.setRowHidden(row, hidden)
            if not hidden:
                visible_rows += 1
        self.binding_summary.setText(f"{visible_rows} action binding(s) visible.")
        self._update_binding_overview(visible_rows=visible_rows)
        if not self._rendering_bindings:
            self.settings_changed.emit()

    def _emit_settings_changed(self, *_args: object) -> None:
        self.settings_changed.emit()

    def _handle_bindings_changed(self, _item: QTableWidgetItem) -> None:
        if self._rendering_bindings:
            return
        self._update_binding_overview()
        self.settings_changed.emit()

    def _handle_custom_changed(self, _item: QTableWidgetItem) -> None:
        if self._rendering_custom:
            return
        self._update_custom_summary()
        self.settings_changed.emit()

    def _add_custom_row(self) -> None:
        hotkeys = self.custom_hotkeys()
        hotkeys.append(CustomHotkey())
        self.set_custom_hotkeys(hotkeys)
        self.settings_changed.emit()

    def _remove_selected_custom_row(self) -> None:
        row = self.custom_table.currentRow()
        if row < 0:
            self.status_label.setText("Select a custom hotkey row before removing it.")
            return
        hotkeys = self.custom_hotkeys()
        if row >= len(hotkeys):
            return
        del hotkeys[row]
        self.set_custom_hotkeys(hotkeys)
        self.settings_changed.emit()

    def _update_custom_summary(self) -> None:
        rows = self.custom_hotkeys()
        active = sum(1 for row in rows if row.enabled and row.combo.strip())
        self.custom_summary.setText(f"{active} custom hotkey(s) are ready to register.")
        self._update_custom_overview(rows)

    def _update_binding_overview(self, visible_rows: int | None = None) -> None:
        rows = self.bindings()
        visible = visible_rows if visible_rows is not None else sum(
            1 for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)
        )
        enabled = sum(1 for row in rows if row.enabled and row.combo.strip())
        filter_text = self.filter_input.text().strip()

        self.bindings_total_value.setText(str(len(rows)))
        self.bindings_visible_value.setText(str(visible))
        self.bindings_enabled_value.setText(str(enabled))
        self.bindings_filter_value.setText(filter_text or "No filter")

    def _update_custom_overview(self, rows: list[CustomHotkey] | None = None) -> None:
        rows = rows if rows is not None else self.custom_hotkeys()
        ready = sum(1 for row in rows if row.enabled and row.combo.strip())
        multi_step = sum(1 for row in rows if row.secondary_action_id.strip())
        incomplete = sum(
            1
            for row in rows
            if not row.combo.strip() or not row.primary_action_id.strip()
        )

        self.custom_total_value.setText(str(len(rows)))
        self.custom_ready_value.setText(str(ready))
        self.custom_multi_step_value.setText(str(multi_step))
        self.custom_incomplete_value.setText(str(incomplete))

    def _apply_default_binding_widths(self) -> None:
        viewport_width = max(self.table.viewport().width(), 840)
        self.table.setColumnWidth(0, int(viewport_width * 0.58))
        self.table.setColumnWidth(1, int(viewport_width * 0.28))
        self.table.setColumnWidth(2, max(100, int(viewport_width * 0.12)))

    def _apply_default_custom_widths(self) -> None:
        viewport_width = max(self.custom_table.viewport().width(), 1120)
        widths = [
            max(90, int(viewport_width * 0.08)),
            int(viewport_width * 0.18),
            int(viewport_width * 0.16),
            int(viewport_width * 0.23),
            int(viewport_width * 0.23),
            max(120, int(viewport_width * 0.12)),
        ]
        for index, width in enumerate(widths):
            self.custom_table.setColumnWidth(index, width)

    @staticmethod
    def _muted_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("mutedText")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _overview_pair(label_text: str, value_label: QLabel, parent: QWidget) -> QWidget:
        container = QWidget(parent)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        label = QLabel(label_text, container)
        label.setObjectName("mutedText")
        layout.addWidget(label)
        layout.addWidget(value_label)
        return container

    @staticmethod
    def _overview_value(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        label.setWordWrap(True)
        return label


class HotkeysPlugin(AppPlugin):
    plugin_id = "hotkeys"
    display_name = "Hotkeys"
    nav_order = 40
    load_order = 40

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._host: PluginHost | None = None
        self._settings = HotkeysPluginConfig()
        self._page: HotkeysPage | None = None
        self.hotkey_service: HotkeyService | None = None
        self._base_actions: dict[str, HotkeyAction] = {}

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self.hotkey_service = HotkeyService(context.qt_parent)
        self._page = HotkeysPage(self.hotkey_service, context.qt_parent)
        self._page.apply_requested.connect(self.apply_changes)
        self._page.reset_requested.connect(self.reset_defaults)
        self._page.settings_changed.connect(self._save_page_state)

        context.register_service("hotkeys.service", self.hotkey_service)
        context.register_service("hotkeys.plugin", self)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def on_plugins_loaded(self, host: PluginHost) -> None:
        self._host = host
        self.reload_actions()

    def shutdown(self) -> None:
        if self.hotkey_service is not None:
            self.hotkey_service.stop()

    def reload_actions(self) -> None:
        if self._host is None or self._context is None or self.hotkey_service is None or self._page is None:
            return

        self._base_actions = {
            action.action_id: action
            for action in self._host.collect_hotkey_actions()
            if action.action_id not in {"", None}
        }
        raw = self._context.plugin_settings(self.plugin_id)
        self._settings = HotkeysPluginConfig.from_dict(raw, self._base_actions)

        self._page.set_available_actions(self._base_actions)
        self._page.set_bindings(self._settings.bindings, self._settings.filter_text)
        self._page.set_custom_hotkeys(self._settings.custom_hotkeys)
        self._page.set_bindings_column_widths(self._settings.bindings_column_widths)
        self._page.set_custom_column_widths(self._settings.custom_column_widths)

        self._apply_runtime_bindings()
        self._save_settings()

    def apply_changes(self) -> None:
        if self._page is None:
            return
        self._settings.bindings = self._page.bindings()
        self._settings.custom_hotkeys = self._page.custom_hotkeys()
        self._settings.bindings_column_widths = self._page.bindings_column_widths()
        self._settings.custom_column_widths = self._page.custom_column_widths()
        self._settings.filter_text = self._page.filter_input.text().strip()
        self._apply_runtime_bindings()
        self._save_settings()

    def reset_defaults(self) -> None:
        preserved_custom = list(self._settings.custom_hotkeys)
        preserved_binding_widths = list(self._settings.bindings_column_widths)
        preserved_custom_widths = list(self._settings.custom_column_widths)
        preserved_filter = self._settings.filter_text
        self._settings = HotkeysPluginConfig.from_dict({}, self._base_actions)
        self._settings.custom_hotkeys = preserved_custom
        self._settings.bindings_column_widths = preserved_binding_widths
        self._settings.custom_column_widths = preserved_custom_widths
        self._settings.filter_text = preserved_filter
        if self._page is not None:
            self._page.set_bindings(self._settings.bindings, self._settings.filter_text)
            self._page.set_custom_hotkeys(self._settings.custom_hotkeys)
            self._page.set_bindings_column_widths(self._settings.bindings_column_widths)
            self._page.set_custom_column_widths(self._settings.custom_column_widths)
        self._apply_runtime_bindings()
        self._save_settings()

    async def _run_custom_hotkey(self, custom_id: str) -> None:
        definition = next((item for item in self._settings.custom_hotkeys if item.id == custom_id), None)
        if definition is None or not definition.enabled:
            return
        sequence = [definition.primary_action_id, definition.secondary_action_id]
        ran_any = False
        for index, action_id in enumerate(sequence):
            if not action_id:
                continue
            action = self._base_actions.get(action_id)
            if action is None:
                continue
            action.handler()
            ran_any = True
            if index == 0 and definition.secondary_action_id and definition.delay_ms > 0:
                await asyncio.sleep(definition.delay_ms / 1000)
        if not ran_any and self.hotkey_service is not None:
            self.hotkey_service.status_changed.emit(
                f"Custom hotkey '{definition.label or 'Custom Hotkey'}' does not have any available target actions."
            )

    def _apply_runtime_bindings(self) -> None:
        if self.hotkey_service is None or self._context is None:
            return

        self.hotkey_service.clear_action_handlers()
        for action in self._base_actions.values():
            self.hotkey_service.set_action_handler(action.action_id, action.handler)
        for definition in self._settings.custom_hotkeys:
            self.hotkey_service.set_action_handler(
                f"hotkeys.custom.{definition.id}",
                lambda custom_id=definition.id: self._context.schedule(self._run_custom_hotkey(custom_id)),
            )

        all_bindings = list(self._settings.bindings)
        all_bindings.extend(
            definition.as_binding()
            for definition in self._settings.custom_hotkeys
            if definition.label.strip() or definition.combo.strip()
        )
        self.hotkey_service.apply_bindings(all_bindings)

    def _save_page_state(self) -> None:
        if self._page is None:
            return
        self._settings.bindings = self._page.bindings()
        self._settings.custom_hotkeys = self._page.custom_hotkeys()
        self._settings.bindings_column_widths = self._page.bindings_column_widths()
        self._settings.custom_column_widths = self._page.custom_column_widths()
        self._settings.filter_text = self._page.filter_input.text().strip()
        self._save_settings()

    def _save_settings(self) -> None:
        if self._context is None:
            return
        self._context.save_plugin_settings(self.plugin_id, self._settings.to_dict())
