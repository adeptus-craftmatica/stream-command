from __future__ import annotations

from dataclasses import asdict, dataclass, field

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stream_control.plugins.base import AppPlugin, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.services.twitch_chat_service import (
    AutoModQueueItem,
    ChatActivity,
    ChatMessage,
    TwitchChatService,
    TwitchChatSettings,
    ViewerCard,
)
from stream_control.services.twitch_service import TwitchApiError
from stream_control.ui.widgets.common import (
    PanelCard,
    capture_table_column_widths,
    restore_table_column_widths,
    set_status_label,
)


@dataclass(slots=True)
class QuickReply:
    label: str
    text: str


@dataclass(slots=True)
class ChatCommand:
    trigger: str
    response: str
    enabled: bool = True


def default_quick_replies() -> list[QuickReply]:
    return [
        QuickReply("Welcome", "Welcome in! Glad you're here."),
        QuickReply("BRB", "Be right back. Staying live, just stepping away for a moment."),
        QuickReply("Thanks", "Thanks for hanging out tonight."),
        QuickReply("Question", "Drop your question in chat and I will get to it."),
    ]


@dataclass(slots=True)
class ChatPluginConfig:
    twitch: TwitchChatSettings = field(default_factory=TwitchChatSettings)
    feed_filter: str = ""
    activity_filter: str = ""
    show_notices: bool = True
    show_events: bool = True
    max_messages: int = 250
    max_events: int = 250
    timeout_duration_seconds: int = 600
    moderation_reason: str = ""
    announcement_color: str = "primary"
    quick_replies: list[QuickReply] = field(default_factory=default_quick_replies)
    commands: list[ChatCommand] = field(default_factory=list)
    commands_column_widths: list[int] = field(default_factory=list)
    quick_replies_column_widths: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "twitch": asdict(self.twitch),
            "feed_filter": self.feed_filter,
            "activity_filter": self.activity_filter,
            "show_notices": self.show_notices,
            "show_events": self.show_events,
            "max_messages": self.max_messages,
            "max_events": self.max_events,
            "timeout_duration_seconds": self.timeout_duration_seconds,
            "moderation_reason": self.moderation_reason,
            "announcement_color": self.announcement_color,
            "quick_replies": [asdict(reply) for reply in self.quick_replies],
            "commands": [asdict(command) for command in self.commands],
            "commands_column_widths": list(self.commands_column_widths),
            "quick_replies_column_widths": list(self.quick_replies_column_widths),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "ChatPluginConfig":
        quick_replies = [
            QuickReply(**item)
            for item in raw.get("quick_replies", [])
            if isinstance(item, dict)
        ]
        commands = [
            ChatCommand(**item)
            for item in raw.get("commands", [])
            if isinstance(item, dict)
        ]
        return cls(
            twitch=TwitchChatSettings.from_dict(dict(raw.get("twitch", {}))),
            feed_filter=str(raw.get("feed_filter", "")).strip(),
            activity_filter=str(raw.get("activity_filter", "")).strip(),
            show_notices=bool(raw.get("show_notices", True)),
            show_events=bool(raw.get("show_events", True)),
            max_messages=max(50, min(int(raw.get("max_messages", 250)), 1000)),
            max_events=max(50, min(int(raw.get("max_events", 250)), 1000)),
            timeout_duration_seconds=max(1, min(int(raw.get("timeout_duration_seconds", 600)), 1_209_600)),
            moderation_reason=str(raw.get("moderation_reason", "")).strip(),
            announcement_color=str(raw.get("announcement_color", "primary") or "primary"),
            quick_replies=quick_replies or default_quick_replies(),
            commands=commands,
            commands_column_widths=[max(40, int(width)) for width in raw.get("commands_column_widths", [])],
            quick_replies_column_widths=[
                max(40, int(width)) for width in raw.get("quick_replies_column_widths", [])
            ],
        )


class ChatPage(QWidget):
    settings_changed = Signal()
    request_connect = Signal()
    request_disconnect = Signal()
    request_start_simulator = Signal()
    request_stop_simulator = Signal()
    request_send_message = Signal(str)
    request_send_announcement = Signal()
    request_send_shoutout = Signal()
    request_refresh_chatters = Signal()
    request_timeout_user = Signal()
    request_ban_user = Signal()
    request_unban_user = Signal()
    request_delete_message = Signal()
    request_approve_automod = Signal()
    request_deny_automod = Signal()
    request_create_poll = Signal()
    request_create_prediction = Signal()

    def __init__(self, settings: ChatPluginConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._messages: list[ChatMessage] = []
        self._activities: list[ChatActivity] = []
        self._viewer_cards: dict[str, ViewerCard] = {}
        self._automod_queue: dict[str, AutoModQueueItem] = {}
        self._subscription_summary: dict[str, object] = {}
        self._rendering_quick_replies = False
        self._rendering_commands = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title = QLabel("Chat Command Center", self)
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Manage chat with an EventSub-first workflow, including viewer cards, moderation tools, engagement controls, commands, and quick replies.",
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
        self.tabs.setObjectName("chatTabs")
        self.tabs.addTab(self._build_feed_tab(), "Feed")
        self.tabs.addTab(self._build_moderation_tab(), "Moderation")
        self.tabs.addTab(self._build_engagement_tab(), "Engagement")
        self.tabs.addTab(self._build_setup_tab(), "Setup")
        layout.addWidget(self.tabs)
        layout.addStretch(1)

        self._apply_settings_to_fields()
        self._apply_saved_table_layouts()
        self.set_connection_status(
            False,
            "Add your Twitch app credentials on Setup, or start the built-in simulator to rehearse moderation and engagement workflows offline.",
        )
        self.set_room_state(
            {
                "channel": "",
                "slow_mode": 0,
                "followers_only": -1,
                "subs_only": False,
                "emote_only": False,
                "unique_chat": False,
                "non_moderator_chat_delay": False,
                "non_moderator_chat_delay_duration": 0,
            }
        )
        self.set_subscription_summary(
            {
                "mode": "disconnected",
                "subscription_types": [],
                "subscription_warnings": [],
                "subscription_errors": [],
            }
        )
        self.set_feed_status(True, "Feed filters, quick replies, and command automation are ready.")
        self.set_moderation_status(True, "Viewer cards and AutoMod tools will populate as chat data arrives.")
        self.set_engagement_status(True, "Announcements, shoutouts, polls, and predictions can all live here.")

    def _build_feed_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        copy = QLabel(
            "Keep chat messages and structured Twitch events visible together, then answer quickly with your composer or saved quick replies.",
            tab,
        )
        copy.setObjectName("mutedText")
        copy.setWordWrap(True)
        layout.addWidget(copy)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.addWidget(self._build_chat_feed_card(), 0, 0)
        grid.addWidget(self._build_activity_feed_card(), 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid, 1)
        layout.addWidget(self._build_compose_card())
        layout.addStretch(1)
        return tab

    def _build_moderation_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        copy = QLabel(
            "Select a chatter, review their recent context, then handle message deletion, timeouts, bans, and held AutoMod messages from one surface.",
            tab,
        )
        copy.setObjectName("mutedText")
        copy.setWordWrap(True)
        layout.addWidget(copy)

        self.moderation_tabs = QTabWidget(tab)
        self.moderation_tabs.setObjectName("chatModerationTabs")
        self.moderation_tabs.addTab(self._build_viewer_actions_panel(), "Viewer Actions")
        self.moderation_tabs.addTab(self._build_automod_panel(), "AutoMod Queue")
        layout.addWidget(self.moderation_tabs, 1)
        return tab

    def _build_engagement_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        copy = QLabel(
            "Handle announcements, shoutouts, polls, predictions, commands, and quick replies here so community workflows stay organized instead of scattered.",
            tab,
        )
        copy.setObjectName("mutedText")
        copy.setWordWrap(True)
        layout.addWidget(copy)

        self.engagement_tabs = QTabWidget(tab)
        self.engagement_tabs.setObjectName("chatEngagementTabs")
        self.engagement_tabs.addTab(self._build_broadcast_tools_panel(), "Broadcasts")
        self.engagement_tabs.addTab(self._build_interactive_tools_panel(), "Interactive")
        self.engagement_tabs.addTab(self._build_commands_panel(), "Commands")
        self.engagement_tabs.addTab(self._build_quick_replies_panel(), "Quick Replies")
        layout.addWidget(self.engagement_tabs, 1)
        return tab

    def _build_viewer_actions_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(16)
        layout.addWidget(self._build_viewers_card(), 0, 0)
        layout.addWidget(self._build_moderation_controls_card(), 0, 1)
        layout.setColumnStretch(0, 5)
        layout.setColumnStretch(1, 4)
        return panel

    def _build_automod_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_automod_card())
        layout.addStretch(1)
        return panel

    def _build_broadcast_tools_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        layout.addWidget(self._build_announcement_card(), 0, 0)
        layout.addWidget(self._build_shoutout_card(), 0, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        return panel

    def _build_interactive_tools_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        layout.addWidget(self._build_poll_card(), 0, 0)
        layout.addWidget(self._build_prediction_card(), 0, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        return panel

    def _build_commands_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_commands_card())
        layout.addStretch(1)
        return panel

    def _build_quick_replies_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_quick_replies_card())
        layout.addStretch(1)
        return panel

    def _build_setup_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary = PanelCard(parent=tab)
        summary.setObjectName("headerCard")
        heading = QLabel("Twitch Connection Setup", summary)
        heading.setObjectName("sectionTitle")
        summary.layout.addWidget(heading)
        copy = QLabel(
            "Chat now prefers EventSub for inbound data and Helix for outbound actions. Save the channel, credentials, and scopes here, then connect or rehearse in simulator mode.",
            summary,
        )
        copy.setObjectName("mutedText")
        copy.setWordWrap(True)
        summary.layout.addWidget(copy)
        layout.addWidget(summary)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        grid.addWidget(self._build_connection_card(), 0, 0)
        grid.addWidget(self._build_room_state_card(), 0, 1)
        grid.addWidget(self._build_subscription_card(), 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        return tab

    def _build_chat_feed_card(self) -> PanelCard:
        card = PanelCard("Chat Feed", self)
        controls = QHBoxLayout()
        self.feed_filter = QLineEdit(card)
        self.feed_filter.setPlaceholderText("Filter by chatter or keyword")
        self.feed_filter.textChanged.connect(self._handle_feed_filter_change)
        clear_button = QPushButton("Clear Feed", card)
        clear_button.clicked.connect(self.clear_messages)
        controls.addWidget(self.feed_filter)
        controls.addWidget(clear_button)
        card.layout.addLayout(controls)

        toggles = QHBoxLayout()
        self.show_notices = QCheckBox("Show notices", card)
        self.show_notices.toggled.connect(self._handle_toggle_change)
        self.show_events = QCheckBox("Show chat events", card)
        self.show_events.toggled.connect(self._handle_toggle_change)
        toggles.addWidget(self.show_notices)
        toggles.addWidget(self.show_events)
        toggles.addStretch(1)
        card.layout.addLayout(toggles)

        self.feed = QListWidget(card)
        self.feed.setMinimumHeight(220)
        self.feed.itemSelectionChanged.connect(self._handle_message_selection)
        card.layout.addWidget(self.feed)

        self.feed_status = QLabel(card)
        self.feed_status.setWordWrap(True)
        card.layout.addWidget(self.feed_status)
        return card

    def _build_activity_feed_card(self) -> PanelCard:
        card = PanelCard("Event Feed", self)
        controls = QHBoxLayout()
        self.activity_filter = QLineEdit(card)
        self.activity_filter.setPlaceholderText("Filter by event or viewer")
        self.activity_filter.textChanged.connect(self._handle_activity_filter_change)
        clear_button = QPushButton("Clear Events", card)
        clear_button.clicked.connect(self.clear_activities)
        controls.addWidget(self.activity_filter)
        controls.addWidget(clear_button)
        card.layout.addLayout(controls)

        self.activity_feed = QListWidget(card)
        self.activity_feed.setMinimumHeight(220)
        card.layout.addWidget(self.activity_feed)

        self.activity_status = QLabel(card)
        self.activity_status.setObjectName("mutedText")
        self.activity_status.setWordWrap(True)
        card.layout.addWidget(self.activity_status)
        return card

    def _build_compose_card(self) -> PanelCard:
        card = PanelCard("Composer And Quick Replies", self)

        row = QHBoxLayout()
        self.composer = QLineEdit(card)
        self.composer.setPlaceholderText("Send a chat message")
        self.composer.returnPressed.connect(self._emit_send_message)
        send_button = QPushButton("Send", card)
        send_button.setObjectName("primaryButton")
        send_button.clicked.connect(self._emit_send_message)
        row.addWidget(self.composer)
        row.addWidget(send_button)
        card.layout.addLayout(row)

        self.quick_reply_buttons = QHBoxLayout()
        card.layout.addLayout(self.quick_reply_buttons)

        self.composer_status = QLabel(card)
        self.composer_status.setWordWrap(True)
        card.layout.addWidget(self.composer_status)
        return card

    def _build_viewers_card(self) -> PanelCard:
        card = PanelCard("Viewer Cards", self)
        top = QHBoxLayout()
        refresh = QPushButton("Refresh Chatters", card)
        refresh.clicked.connect(self.request_refresh_chatters.emit)
        top.addWidget(refresh)
        top.addStretch(1)
        card.layout.addLayout(top)

        self.viewer_list = QListWidget(card)
        self.viewer_list.setMinimumHeight(240)
        self.viewer_list.itemSelectionChanged.connect(self._handle_viewer_selection)
        card.layout.addWidget(self.viewer_list)

        self.viewer_status = QLabel(card)
        self.viewer_status.setObjectName("mutedText")
        self.viewer_status.setWordWrap(True)
        card.layout.addWidget(self.viewer_status)
        return card

    def _build_moderation_controls_card(self) -> PanelCard:
        card = PanelCard("Selected Viewer", self)
        self.viewer_name = QLabel("No viewer selected", card)
        self.viewer_name.setObjectName("sectionTitle")
        card.layout.addWidget(self.viewer_name)

        self.viewer_detail = QLabel("Select a chatter or simulator viewer to inspect their recent context.", card)
        self.viewer_detail.setObjectName("mutedText")
        self.viewer_detail.setWordWrap(True)
        card.layout.addWidget(self.viewer_detail)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.timeout_duration = QSpinBox(card)
        self.timeout_duration.setRange(1, 1_209_600)
        self.timeout_duration.setSingleStep(30)
        self.timeout_duration.setMaximumWidth(180)
        self.timeout_duration.valueChanged.connect(self._store_moderation_settings)
        form.addRow("Timeout (sec)", self.timeout_duration)

        self.moderation_reason = QLineEdit(card)
        self.moderation_reason.setPlaceholderText("Optional moderation reason")
        self.moderation_reason.editingFinished.connect(self._store_moderation_settings)
        form.addRow("Reason", self.moderation_reason)
        card.layout.addLayout(form)

        user_buttons = QHBoxLayout()
        timeout_button = QPushButton("Timeout", card)
        timeout_button.setObjectName("primaryButton")
        timeout_button.clicked.connect(self.request_timeout_user.emit)
        ban_button = QPushButton("Ban", card)
        ban_button.setObjectName("dangerButton")
        ban_button.clicked.connect(self.request_ban_user.emit)
        unban_button = QPushButton("Unban", card)
        unban_button.clicked.connect(self.request_unban_user.emit)
        user_buttons.addWidget(timeout_button)
        user_buttons.addWidget(ban_button)
        user_buttons.addWidget(unban_button)
        user_buttons.addStretch(1)
        card.layout.addLayout(user_buttons)

        self.selected_message_label = QLabel("Selected message: none", card)
        self.selected_message_label.setObjectName("mutedText")
        self.selected_message_label.setWordWrap(True)
        card.layout.addWidget(self.selected_message_label)

        delete_button = QPushButton("Delete Selected Message", card)
        delete_button.clicked.connect(self.request_delete_message.emit)
        card.layout.addWidget(delete_button)

        self.moderation_status = QLabel(card)
        self.moderation_status.setWordWrap(True)
        card.layout.addWidget(self.moderation_status)
        card.layout.addStretch(1)
        return card

    def _build_automod_card(self) -> PanelCard:
        card = PanelCard("AutoMod Review Queue", self)
        card.layout.addWidget(
            self._muted_label(
                "Held messages appear here so moderators can allow or deny them quickly. The simulator will generate sample holds automatically for testing.",
                card,
            )
        )
        self.automod_list = QListWidget(card)
        self.automod_list.setMinimumHeight(220)
        card.layout.addWidget(self.automod_list)

        buttons = QHBoxLayout()
        approve = QPushButton("Approve Selected", card)
        approve.setObjectName("primaryButton")
        approve.clicked.connect(self.request_approve_automod.emit)
        deny = QPushButton("Deny Selected", card)
        deny.setObjectName("dangerButton")
        deny.clicked.connect(self.request_deny_automod.emit)
        buttons.addWidget(approve)
        buttons.addWidget(deny)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)
        return card

    def _build_announcement_card(self) -> PanelCard:
        card = PanelCard("Announcements", self)
        card.layout.addWidget(
            self._muted_label(
                "Use announcements for schedule updates, hype moments, or stream instructions without burying the message in normal chat traffic.",
                card,
            )
        )
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.announcement_message = QLineEdit(card)
        self.announcement_message.setPlaceholderText("Event is starting in 20 minutes!")
        form.addRow("Message", self.announcement_message)
        self.announcement_color = QComboBox(card)
        for color in ["primary", "blue", "green", "orange", "purple"]:
            self.announcement_color.addItem(color.title(), color)
        self.announcement_color.currentIndexChanged.connect(self._store_engagement_settings)
        form.addRow("Color", self.announcement_color)
        card.layout.addLayout(form)

        send_button = QPushButton("Send Announcement", card)
        send_button.setObjectName("primaryButton")
        send_button.clicked.connect(self.request_send_announcement.emit)
        card.layout.addWidget(send_button)
        card.layout.addStretch(1)
        return card

    def _build_shoutout_card(self) -> PanelCard:
        card = PanelCard("Shoutouts", self)
        card.layout.addWidget(
            self._muted_label(
                "Send a shoutout to another broadcaster by login. Twitch still enforces its own cooldowns and moderator permissions here.",
                card,
            )
        )
        self.shoutout_target = QLineEdit(card)
        self.shoutout_target.setPlaceholderText("another_channel")
        card.layout.addWidget(self.shoutout_target)
        send_button = QPushButton("Send Shoutout", card)
        send_button.clicked.connect(self.request_send_shoutout.emit)
        card.layout.addWidget(send_button)
        card.layout.addStretch(1)
        return card

    def _build_poll_card(self) -> PanelCard:
        card = PanelCard("Polls", self)
        card.layout.addWidget(
            self._muted_label(
                "Use polls when you want quick audience input on the next segment, game choice, or stream direction.",
                card,
            )
        )
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.poll_question = QLineEdit(card)
        self.poll_question.setPlaceholderText("What should we do next?")
        form.addRow("Question", self.poll_question)
        self.poll_duration = QSpinBox(card)
        self.poll_duration.setRange(15, 1_800)
        self.poll_duration.setValue(120)
        self.poll_duration.setMaximumWidth(180)
        form.addRow("Duration (sec)", self.poll_duration)
        card.layout.addLayout(form)

        self.poll_choices = QPlainTextEdit(card)
        self.poll_choices.setPlaceholderText("One choice per line")
        self.poll_choices.setMinimumHeight(140)
        card.layout.addWidget(self.poll_choices)

        create_button = QPushButton("Create Poll", card)
        create_button.clicked.connect(self.request_create_poll.emit)
        card.layout.addWidget(create_button)
        return card

    def _build_prediction_card(self) -> PanelCard:
        card = PanelCard("Predictions", self)
        card.layout.addWidget(
            self._muted_label(
                "Use predictions for higher-stakes moments like match outcomes, boss clears, or community bets on what happens next.",
                card,
            )
        )
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.prediction_title = QLineEdit(card)
        self.prediction_title.setPlaceholderText("Will we beat the boss tonight?")
        form.addRow("Title", self.prediction_title)
        self.prediction_window = QSpinBox(card)
        self.prediction_window.setRange(15, 1_800)
        self.prediction_window.setValue(180)
        self.prediction_window.setMaximumWidth(180)
        form.addRow("Window (sec)", self.prediction_window)
        card.layout.addLayout(form)

        self.prediction_outcomes = QPlainTextEdit(card)
        self.prediction_outcomes.setPlaceholderText("One outcome per line")
        self.prediction_outcomes.setMinimumHeight(140)
        card.layout.addWidget(self.prediction_outcomes)

        create_button = QPushButton("Create Prediction", card)
        create_button.clicked.connect(self.request_create_prediction.emit)
        card.layout.addWidget(create_button)
        return card

    def _build_commands_card(self) -> PanelCard:
        card = PanelCard("Command System", self)
        card.layout.addWidget(
            self._muted_label(
                "Add exact-match triggers like !discord or !rules. When an incoming message starts with a matching trigger, the plugin auto-sends the configured response.",
                card,
            )
        )

        self.commands_table = QTableWidget(0, 3, card)
        self.commands_table.setHorizontalHeaderLabels(["Enabled", "Trigger", "Response"])
        self.commands_table.setAlternatingRowColors(True)
        self.commands_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.commands_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.commands_table.setMinimumHeight(220)
        self.commands_table.horizontalHeader().sectionResized.connect(self._store_table_layouts)
        self.commands_table.cellChanged.connect(self._handle_commands_changed)
        card.layout.addWidget(self.commands_table)

        buttons = QHBoxLayout()
        add_button = QPushButton("Add Command", card)
        add_button.clicked.connect(self._add_command_row)
        remove_button = QPushButton("Remove Selected", card)
        remove_button.clicked.connect(self._remove_selected_command_row)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)
        card.layout.addStretch(1)
        return card

    def _build_quick_replies_card(self) -> PanelCard:
        card = PanelCard("Quick Replies", self)
        card.layout.addWidget(
            self._muted_label(
                "These replies show up as one-click buttons on the Feed tab. Keep them short enough to be useful in the middle of a stream.",
                card,
            )
        )

        self.quick_replies_table = QTableWidget(0, 2, card)
        self.quick_replies_table.setHorizontalHeaderLabels(["Button Label", "Message"])
        self.quick_replies_table.setAlternatingRowColors(True)
        self.quick_replies_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.quick_replies_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.quick_replies_table.setMinimumHeight(220)
        self.quick_replies_table.horizontalHeader().sectionResized.connect(self._store_table_layouts)
        self.quick_replies_table.cellChanged.connect(self._handle_quick_replies_changed)
        card.layout.addWidget(self.quick_replies_table)

        buttons = QHBoxLayout()
        add_button = QPushButton("Add Reply", card)
        add_button.clicked.connect(self._add_quick_reply_row)
        remove_button = QPushButton("Remove Selected", card)
        remove_button.clicked.connect(self._remove_selected_quick_reply_row)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)

        self.engagement_status = QLabel(card)
        self.engagement_status.setWordWrap(True)
        card.layout.addWidget(self.engagement_status)
        card.layout.addStretch(1)
        return card

    def _build_connection_card(self) -> PanelCard:
        card = PanelCard("EventSub And API Credentials", self)
        card.layout.addWidget(
            self._muted_label(
                "Current Twitch guidance favors EventSub over IRC for receiving chat and events. A single user access token powers both the incoming subscriptions and the outbound moderation APIs.",
                card,
            )
        )

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.channel_name = QLineEdit(card)
        self.channel_name.setPlaceholderText("channel_name")
        self.channel_name.editingFinished.connect(self._store_connection_settings)
        form.addRow("Channel", self.channel_name)

        self.client_id = QLineEdit(card)
        self.client_id.setPlaceholderText("Twitch Client ID")
        self.client_id.editingFinished.connect(self._store_connection_settings)
        form.addRow("Client ID", self.client_id)

        self.access_token = QLineEdit(card)
        self.access_token.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.access_token.setPlaceholderText("User Access Token")
        self.access_token.editingFinished.connect(self._store_connection_settings)
        form.addRow("Access token", self.access_token)

        self.broadcaster_id = QLineEdit(card)
        self.broadcaster_id.setPlaceholderText("Optional, auto-resolved from channel")
        self.broadcaster_id.editingFinished.connect(self._store_connection_settings)
        form.addRow("Broadcaster ID", self.broadcaster_id)

        self.moderator_id = QLineEdit(card)
        self.moderator_id.setPlaceholderText("Optional, defaults to the token user")
        self.moderator_id.editingFinished.connect(self._store_connection_settings)
        form.addRow("Moderator ID", self.moderator_id)
        card.layout.addLayout(form)

        self.auto_connect = QCheckBox("Reconnect chat on launch", card)
        self.auto_connect.toggled.connect(self._store_connection_settings)
        card.layout.addWidget(self.auto_connect)

        self.auto_simulator = QCheckBox("Start the built-in chat simulator on launch", card)
        self.auto_simulator.toggled.connect(self._store_connection_settings)
        card.layout.addWidget(self.auto_simulator)

        buttons = QHBoxLayout()
        connect_button = QPushButton("Connect", card)
        connect_button.setObjectName("primaryButton")
        connect_button.clicked.connect(self.request_connect.emit)
        disconnect_button = QPushButton("Disconnect", card)
        disconnect_button.clicked.connect(self.request_disconnect.emit)
        sim_button = QPushButton("Start Test Chat", card)
        sim_button.clicked.connect(self.request_start_simulator.emit)
        stop_sim_button = QPushButton("Stop Test Chat", card)
        stop_sim_button.clicked.connect(self.request_stop_simulator.emit)
        buttons.addWidget(connect_button)
        buttons.addWidget(disconnect_button)
        buttons.addWidget(sim_button)
        buttons.addWidget(stop_sim_button)
        buttons.addStretch(1)
        card.layout.addLayout(buttons)

        self.scope_hint = QLabel(
            "Common scopes for the full feature set: user:read:chat, user:write:chat, moderator:manage:chat_messages, moderator:manage:banned_users, moderator:manage:automod, moderator:manage:announcements, moderator:manage:shoutouts, moderator:read:followers, moderator:read:chatters, channel:manage:polls, and channel:manage:predictions.",
            card,
        )
        self.scope_hint.setObjectName("mutedText")
        self.scope_hint.setWordWrap(True)
        card.layout.addWidget(self.scope_hint)
        return card

    def _build_room_state_card(self) -> PanelCard:
        card = PanelCard("Room State", self)
        self.room_summary = QLabel("Waiting for chat settings", card)
        self.room_summary.setObjectName("sectionTitle")
        self.room_summary.setWordWrap(True)
        card.layout.addWidget(self.room_summary)

        self.room_detail = QLabel(card)
        self.room_detail.setObjectName("mutedText")
        self.room_detail.setWordWrap(True)
        card.layout.addWidget(self.room_detail)
        return card

    def _build_subscription_card(self) -> PanelCard:
        card = PanelCard("Subscription Health", self)
        self.subscription_overview = QLabel(card)
        self.subscription_overview.setObjectName("sectionTitle")
        self.subscription_overview.setWordWrap(True)
        card.layout.addWidget(self.subscription_overview)

        self.subscription_detail = QLabel(card)
        self.subscription_detail.setObjectName("mutedText")
        self.subscription_detail.setWordWrap(True)
        card.layout.addWidget(self.subscription_detail)
        return card

    def _apply_settings_to_fields(self) -> None:
        self.channel_name.setText(self._settings.twitch.channel)
        self.client_id.setText(self._settings.twitch.client_id)
        self.access_token.setText(self._settings.twitch.access_token)
        self.broadcaster_id.setText(self._settings.twitch.broadcaster_id)
        self.moderator_id.setText(self._settings.twitch.moderator_id)
        self.auto_connect.setChecked(self._settings.twitch.auto_connect)
        self.auto_simulator.setChecked(self._settings.twitch.simulator_auto_start)
        self.feed_filter.setText(self._settings.feed_filter)
        self.activity_filter.setText(self._settings.activity_filter)
        self.show_notices.setChecked(self._settings.show_notices)
        self.show_events.setChecked(self._settings.show_events)
        self.timeout_duration.setValue(self._settings.timeout_duration_seconds)
        self.moderation_reason.setText(self._settings.moderation_reason)
        self.announcement_color.setCurrentIndex(
            max(0, self.announcement_color.findData(self._settings.announcement_color))
        )
        self.render_quick_replies()
        self.render_commands()
        self.render_quick_reply_buttons()

    def set_connection_status(self, ok: bool, message: str) -> None:
        set_status_label(self.connection_status, ok, message)

    def set_feed_status(self, ok: bool, message: str) -> None:
        set_status_label(self.feed_status, ok, message)
        self.composer_status.setText(message)

    def set_moderation_status(self, ok: bool, message: str) -> None:
        set_status_label(self.moderation_status, ok, message)

    def set_engagement_status(self, ok: bool, message: str) -> None:
        set_status_label(self.engagement_status, ok, message)

    def set_room_state(self, state: dict[str, object]) -> None:
        channel = str(state.get("channel", "")).strip()
        self.room_summary.setText(f"Watching #{channel}" if channel else "Waiting for chat settings")
        followers_only = int(state.get("followers_only", -1) or -1)
        followers_label = "Off" if followers_only < 0 else f"{followers_only} min"
        detail = (
            f"Slow mode: {int(state.get('slow_mode', 0) or 0)} sec\n"
            f"Followers-only: {followers_label}\n"
            f"Subscribers-only: {'On' if bool(state.get('subs_only', False)) else 'Off'}\n"
            f"Emote-only: {'On' if bool(state.get('emote_only', False)) else 'Off'}\n"
            f"Unique chat: {'On' if bool(state.get('unique_chat', False)) else 'Off'}\n"
            f"Non-moderator delay: {'On' if bool(state.get('non_moderator_chat_delay', False)) else 'Off'}"
        )
        self.room_detail.setText(detail)

    def set_subscription_summary(self, summary: dict[str, object]) -> None:
        self._subscription_summary = summary
        mode = str(summary.get("mode", "disconnected")).strip()
        mode_label = {
            "eventsub": "Real Twitch EventSub session active",
            "simulator": "Simulator session active",
            "disconnected": "Chat is disconnected",
        }.get(mode, mode.title())
        subscriptions = list(summary.get("subscription_types", []))
        warnings = [str(item) for item in summary.get("subscription_warnings", []) if str(item).strip()]
        errors = [str(item) for item in summary.get("subscription_errors", []) if str(item).strip()]

        self.subscription_overview.setText(mode_label)
        detail_lines = [
            f"Channel: #{summary.get('channel', '') or 'not set'}",
            f"Active subscription types: {', '.join(subscriptions) if subscriptions else 'none yet'}",
        ]
        if warnings:
            detail_lines.append("Warnings: " + " | ".join(warnings[:4]))
        if errors:
            detail_lines.append("Errors: " + " | ".join(errors[:4]))
        self.subscription_detail.setText("\n".join(detail_lines))

    def append_message(self, message: ChatMessage) -> None:
        self._messages.append(message)
        overflow = len(self._messages) - self._settings.max_messages
        if overflow > 0:
            self._messages = self._messages[overflow:]
        self._render_messages()

    def append_activity(self, activity: ChatActivity) -> None:
        self._activities.append(activity)
        overflow = len(self._activities) - self._settings.max_events
        if overflow > 0:
            self._activities = self._activities[overflow:]
        self._render_activities()

    def clear_messages(self) -> None:
        self._messages.clear()
        self.feed.clear()
        self.selected_message_label.setText("Selected message: none")

    def clear_activities(self) -> None:
        self._activities.clear()
        self.activity_feed.clear()

    def set_viewer_cards(self, cards: list[ViewerCard]) -> None:
        selected = self.selected_viewer_id()
        self._viewer_cards = {card.user_id or card.user_login: card for card in cards}
        self.viewer_list.clear()
        for card in cards:
            label = card.display_name or card.user_login or card.user_id
            subtitle = f"{label}  |  {', '.join(card.roles) if card.roles else 'viewer'}  |  {card.message_count} msg"
            item = QListWidgetItem(subtitle)
            item.setData(Qt.ItemDataRole.UserRole, card.user_id or card.user_login)
            self.viewer_list.addItem(item)
        if selected:
            self._restore_list_selection(self.viewer_list, selected)
        self.viewer_status.setText(f"{len(cards)} viewer cards in memory.")
        if not selected and cards:
            self.viewer_list.setCurrentRow(0)

    def set_automod_queue(self, items: list[AutoModQueueItem]) -> None:
        selected = self.selected_automod_id()
        self._automod_queue = {item.id: item for item in items}
        self.automod_list.clear()
        for item in items:
            label = f"[{item.timestamp}] {item.display_name or item.user_login}: {item.text}"
            row = QListWidgetItem(label)
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            self.automod_list.addItem(row)
        if selected:
            self._restore_list_selection(self.automod_list, selected)

    def selected_message(self) -> ChatMessage | None:
        item = self.feed.currentItem()
        if item is None:
            return None
        message_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        for message in self._messages:
            if message.id == message_id:
                return message
        return None

    def selected_message_id(self) -> str:
        message = self.selected_message()
        return message.id if message is not None else ""

    def selected_viewer_id(self) -> str:
        item = self.viewer_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def selected_automod_id(self) -> str:
        item = self.automod_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def announcement_payload(self) -> tuple[str, str]:
        return self.announcement_message.text().strip(), str(self.announcement_color.currentData() or "primary")

    def shoutout_target_value(self) -> str:
        return self.shoutout_target.text().strip()

    def poll_definition(self) -> tuple[str, list[str], int]:
        return (
            self.poll_question.text().strip(),
            [line.strip() for line in self.poll_choices.toPlainText().splitlines() if line.strip()],
            int(self.poll_duration.value()),
        )

    def prediction_definition(self) -> tuple[str, list[str], int]:
        return (
            self.prediction_title.text().strip(),
            [line.strip() for line in self.prediction_outcomes.toPlainText().splitlines() if line.strip()],
            int(self.prediction_window.value()),
        )

    def timeout_seconds(self) -> int:
        return int(self.timeout_duration.value())

    def moderation_reason_value(self) -> str:
        return self.moderation_reason.text().strip()

    def command_rules(self) -> list[ChatCommand]:
        rules: list[ChatCommand] = []
        for row in range(self.commands_table.rowCount()):
            enabled_item = self.commands_table.item(row, 0)
            trigger_item = self.commands_table.item(row, 1)
            response_item = self.commands_table.item(row, 2)
            trigger = trigger_item.text().strip() if trigger_item is not None else ""
            response = response_item.text().strip() if response_item is not None else ""
            if not trigger and not response:
                continue
            rules.append(
                ChatCommand(
                    trigger=trigger,
                    response=response,
                    enabled=enabled_item is not None and enabled_item.checkState() == Qt.CheckState.Checked,
                )
            )
        return rules

    def quick_replies(self) -> list[QuickReply]:
        replies: list[QuickReply] = []
        for row in range(self.quick_replies_table.rowCount()):
            label_item = self.quick_replies_table.item(row, 0)
            text_item = self.quick_replies_table.item(row, 1)
            label = label_item.text().strip() if label_item is not None else ""
            text = text_item.text().strip() if text_item is not None else ""
            if not label and not text:
                continue
            replies.append(QuickReply(label=label or "Reply", text=text))
        return replies

    def render_quick_replies(self) -> None:
        self._rendering_quick_replies = True
        self.quick_replies_table.setRowCount(len(self._settings.quick_replies))
        for row, reply in enumerate(self._settings.quick_replies):
            self.quick_replies_table.setItem(row, 0, QTableWidgetItem(reply.label))
            self.quick_replies_table.setItem(row, 1, QTableWidgetItem(reply.text))
        self._rendering_quick_replies = False
        if self._settings.quick_replies_column_widths:
            restore_table_column_widths(self.quick_replies_table, self._settings.quick_replies_column_widths)
        else:
            self._apply_default_quick_reply_widths()

    def render_commands(self) -> None:
        self._rendering_commands = True
        self.commands_table.setRowCount(len(self._settings.commands))
        for row, command in enumerate(self._settings.commands):
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            enabled_item.setCheckState(Qt.CheckState.Checked if command.enabled else Qt.CheckState.Unchecked)
            self.commands_table.setItem(row, 0, enabled_item)
            self.commands_table.setItem(row, 1, QTableWidgetItem(command.trigger))
            self.commands_table.setItem(row, 2, QTableWidgetItem(command.response))
        self._rendering_commands = False
        if self._settings.commands_column_widths:
            restore_table_column_widths(self.commands_table, self._settings.commands_column_widths)
        else:
            self._apply_default_command_widths()

    def render_quick_reply_buttons(self) -> None:
        while self.quick_reply_buttons.count():
            item = self.quick_reply_buttons.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        replies = [reply for reply in self._settings.quick_replies if reply.label.strip() and reply.text.strip()]
        for reply in replies[:6]:
            button = QPushButton(reply.label.strip(), self)
            button.clicked.connect(lambda _checked=False, text=reply.text: self.request_send_message.emit(text))
            self.quick_reply_buttons.addWidget(button)
        self.quick_reply_buttons.addStretch(1)

    def _render_messages(self) -> None:
        selected = self.selected_message_id()
        filter_text = self._settings.feed_filter.strip().lower()
        self.feed.clear()
        visible_count = 0
        for message in self._messages:
            if not self._should_show_message(message, filter_text):
                continue
            item = QListWidgetItem(self._format_message(message))
            item.setData(Qt.ItemDataRole.UserRole, message.id)
            if message.kind == "notice":
                item.setForeground(QColor("#d9b36d"))
            elif message.kind == "event":
                item.setForeground(QColor("#8ed8e5"))
            elif message.color:
                item.setForeground(QColor(message.color))
            self.feed.addItem(item)
            visible_count += 1
        self.feed.scrollToBottom()
        if selected:
            self._restore_list_selection(self.feed, selected)
        self.activity_status.setText(f"{visible_count} chat entries match the current filters.")

    def _render_activities(self) -> None:
        filter_text = self._settings.activity_filter.strip().lower()
        self.activity_feed.clear()
        visible_count = 0
        for activity in self._activities:
            if filter_text:
                haystack = f"{activity.kind} {activity.summary} {activity.detail} {activity.display_name} {activity.user_login}".lower()
                if filter_text not in haystack:
                    continue
            item = QListWidgetItem(self._format_activity(activity))
            if activity.kind in {"moderation", "automod"}:
                item.setForeground(QColor("#e8a5a5"))
            elif activity.kind in {"follow", "subscription", "redeem", "poll", "prediction", "shoutout", "cheer", "raid"}:
                item.setForeground(QColor("#8ed8e5"))
            self.activity_feed.addItem(item)
            visible_count += 1
        self.activity_feed.scrollToBottom()
        self.activity_status.setText(f"{visible_count} Twitch events match the current filters.")

    def _should_show_message(self, message: ChatMessage, filter_text: str) -> bool:
        if message.kind == "notice" and not self._settings.show_notices:
            return False
        if message.kind == "event" and not self._settings.show_events:
            return False
        if not filter_text:
            return True
        haystack = f"{message.display_name} {message.user_login} {message.text}".lower()
        return filter_text in haystack

    def _format_message(self, message: ChatMessage) -> str:
        prefix = f"[{message.timestamp}] "
        if message.kind == "notice":
            return f"{prefix}NOTICE: {message.text}"
        if message.kind == "event":
            return f"{prefix}EVENT: {message.text}"

        user = message.display_name or message.user_login or "Unknown"
        flags: list[str] = []
        if message.is_first_message:
            flags.append("first")
        if "broadcaster/" in message.badges:
            flags.append("broadcaster")
        elif "moderator/" in message.badges:
            flags.append("mod")
        elif "vip/" in message.badges:
            flags.append("vip")
        elif "subscriber/" in message.badges:
            flags.append("sub")
        flag_text = f" [{' | '.join(flags)}]" if flags else ""
        if message.is_action:
            return f"{prefix}* {user}{flag_text} {message.text}"
        return f"{prefix}{user}{flag_text}: {message.text}"

    @staticmethod
    def _format_activity(activity: ChatActivity) -> str:
        detail = f" - {activity.detail}" if activity.detail.strip() else ""
        return f"[{activity.timestamp}] {activity.kind.upper()}: {activity.summary}{detail}"

    def _handle_feed_filter_change(self) -> None:
        self._settings.feed_filter = self.feed_filter.text().strip()
        self.settings_changed.emit()
        self._render_messages()

    def _handle_activity_filter_change(self) -> None:
        self._settings.activity_filter = self.activity_filter.text().strip()
        self.settings_changed.emit()
        self._render_activities()

    def _handle_toggle_change(self) -> None:
        self._settings.show_notices = self.show_notices.isChecked()
        self._settings.show_events = self.show_events.isChecked()
        self.settings_changed.emit()
        self._render_messages()

    def _store_connection_settings(self) -> None:
        self._settings.twitch.channel = self.channel_name.text().strip()
        self._settings.twitch.client_id = self.client_id.text().strip()
        self._settings.twitch.access_token = self.access_token.text().strip()
        self._settings.twitch.broadcaster_id = self.broadcaster_id.text().strip()
        self._settings.twitch.moderator_id = self.moderator_id.text().strip()
        self._settings.twitch.auto_connect = self.auto_connect.isChecked()
        self._settings.twitch.simulator_auto_start = self.auto_simulator.isChecked()
        self.settings_changed.emit()

    def _store_moderation_settings(self, *_args: object) -> None:
        self._settings.timeout_duration_seconds = int(self.timeout_duration.value())
        self._settings.moderation_reason = self.moderation_reason.text().strip()
        self.settings_changed.emit()

    def _store_engagement_settings(self, *_args: object) -> None:
        self._settings.announcement_color = str(self.announcement_color.currentData() or "primary")
        self.settings_changed.emit()

    def _handle_quick_replies_changed(self, _row: int, _column: int) -> None:
        if self._rendering_quick_replies:
            return
        self._settings.quick_replies = self.quick_replies()
        self.settings_changed.emit()
        self.render_quick_reply_buttons()

    def _handle_commands_changed(self, _row: int, _column: int) -> None:
        if self._rendering_commands:
            return
        self._settings.commands = self.command_rules()
        self.settings_changed.emit()

    def _apply_saved_table_layouts(self) -> None:
        restore_table_column_widths(self.commands_table, self._settings.commands_column_widths)
        restore_table_column_widths(self.quick_replies_table, self._settings.quick_replies_column_widths)

    def _store_table_layouts(self, *_args: object) -> None:
        self._settings.commands_column_widths = capture_table_column_widths(self.commands_table)
        self._settings.quick_replies_column_widths = capture_table_column_widths(self.quick_replies_table)
        self.settings_changed.emit()

    def _apply_default_command_widths(self) -> None:
        viewport_width = max(self.commands_table.viewport().width(), 880)
        self.commands_table.setColumnWidth(0, max(110, int(viewport_width * 0.14)))
        self.commands_table.setColumnWidth(1, int(viewport_width * 0.22))
        self.commands_table.setColumnWidth(2, int(viewport_width * 0.60))

    def _apply_default_quick_reply_widths(self) -> None:
        viewport_width = max(self.quick_replies_table.viewport().width(), 760)
        self.quick_replies_table.setColumnWidth(0, int(viewport_width * 0.28))
        self.quick_replies_table.setColumnWidth(1, int(viewport_width * 0.68))

    def _add_quick_reply_row(self) -> None:
        self._settings.quick_replies.append(QuickReply("Reply", ""))
        self.render_quick_replies()
        self.render_quick_reply_buttons()
        self.settings_changed.emit()

    def _remove_selected_quick_reply_row(self) -> None:
        row = self.quick_replies_table.currentRow()
        if row < 0 or row >= len(self._settings.quick_replies):
            self.set_engagement_status(False, "Select a quick reply before removing it.")
            return
        del self._settings.quick_replies[row]
        self.render_quick_replies()
        self.render_quick_reply_buttons()
        self.settings_changed.emit()

    def _add_command_row(self) -> None:
        self._settings.commands.append(ChatCommand("!command", ""))
        self.render_commands()
        self.settings_changed.emit()

    def _remove_selected_command_row(self) -> None:
        row = self.commands_table.currentRow()
        if row < 0 or row >= len(self._settings.commands):
            self.set_engagement_status(False, "Select a command before removing it.")
            return
        del self._settings.commands[row]
        self.render_commands()
        self.settings_changed.emit()

    def _emit_send_message(self) -> None:
        text = self.composer.text().strip()
        if not text:
            return
        self.request_send_message.emit(text)
        self.composer.clear()

    def _handle_message_selection(self) -> None:
        message = self.selected_message()
        if message is None:
            self.selected_message_label.setText("Selected message: none")
            return
        preview = message.text if len(message.text) <= 120 else message.text[:117] + "..."
        self.selected_message_label.setText(
            f"Selected message: {message.display_name or message.user_login} said '{preview}'"
        )
        viewer_key = message.user_id or message.user_login
        if viewer_key:
            self._restore_list_selection(self.viewer_list, viewer_key)

    def _handle_viewer_selection(self) -> None:
        key = self.selected_viewer_id()
        card = self._viewer_cards.get(key)
        if card is None:
            self.viewer_name.setText("No viewer selected")
            self.viewer_detail.setText("Select a chatter or simulator viewer to inspect their recent context.")
            return
        roles = ", ".join(card.roles) if card.roles else "viewer"
        follow = "following" if card.is_following else "not marked as following"
        sub = "subscribed" if card.is_subscribed else "not marked as subscribed"
        self.viewer_name.setText(card.display_name or card.user_login or card.user_id)
        self.viewer_detail.setText(
            f"Login: {card.user_login or 'unknown'}\n"
            f"Roles: {roles}\n"
            f"Status: {follow}, {sub}\n"
            f"Messages seen: {card.message_count}\n"
            f"Last seen: {card.last_seen or 'unknown'}\n"
            f"Last message: {card.last_message or 'No messages seen yet.'}"
        )

    @staticmethod
    def _restore_list_selection(widget: QListWidget, target_value: str) -> None:
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == target_value:
                widget.setCurrentRow(index)
                return

    @staticmethod
    def _muted_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("mutedText")
        label.setWordWrap(True)
        return label


class ChatPlugin(AppPlugin):
    plugin_id = "chat"
    display_name = "Chat"
    nav_order = 18
    load_order = 18

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._settings = ChatPluginConfig()
        self._page: ChatPage | None = None
        self.chat_service: TwitchChatService | None = None

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self._settings = ChatPluginConfig.from_dict(context.plugin_settings(self.plugin_id))
        self.chat_service = TwitchChatService(context.qt_parent)
        self._page = ChatPage(self._settings, context.qt_parent)

        self._page.settings_changed.connect(self._save_settings)
        self._page.request_connect.connect(lambda: context.schedule(self._connect()))
        self._page.request_disconnect.connect(lambda: context.schedule(self.chat_service.disconnect()))
        self._page.request_start_simulator.connect(lambda: context.schedule(self._start_simulator()))
        self._page.request_stop_simulator.connect(lambda: context.schedule(self.chat_service.disconnect()))
        self._page.request_send_message.connect(lambda text: context.schedule(self._send_message(text)))
        self._page.request_send_announcement.connect(lambda: context.schedule(self._send_announcement()))
        self._page.request_send_shoutout.connect(lambda: context.schedule(self._send_shoutout()))
        self._page.request_refresh_chatters.connect(lambda: context.schedule(self._refresh_chatters()))
        self._page.request_timeout_user.connect(lambda: context.schedule(self._timeout_selected_user()))
        self._page.request_ban_user.connect(lambda: context.schedule(self._ban_selected_user()))
        self._page.request_unban_user.connect(lambda: context.schedule(self._unban_selected_user()))
        self._page.request_delete_message.connect(lambda: context.schedule(self._delete_selected_message()))
        self._page.request_approve_automod.connect(lambda: context.schedule(self._approve_selected_automod()))
        self._page.request_deny_automod.connect(lambda: context.schedule(self._deny_selected_automod()))
        self._page.request_create_poll.connect(lambda: context.schedule(self._create_poll()))
        self._page.request_create_prediction.connect(lambda: context.schedule(self._create_prediction()))

        self.chat_service.connection_changed.connect(self._handle_connection_changed)
        self.chat_service.room_state_changed.connect(self._handle_room_state_changed)
        self.chat_service.message_received.connect(self._handle_message_received)
        self.chat_service.history_cleared.connect(self._handle_history_cleared)
        self.chat_service.activity_received.connect(self._handle_activity_received)
        self.chat_service.viewer_cards_changed.connect(self._handle_viewer_cards_changed)
        self.chat_service.automod_queue_changed.connect(self._handle_automod_queue_changed)
        self.chat_service.subscription_summary_changed.connect(self._handle_subscription_summary_changed)

        context.register_service("chat.twitch_service", self.chat_service)
        context.register_service("chat.plugin", self)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def on_plugins_loaded(self, _host) -> None:
        if self._context is None or self.chat_service is None:
            return
        if self._settings.twitch.simulator_auto_start:
            self._context.schedule(self._start_simulator())
        elif self._settings.twitch.auto_connect:
            self._context.schedule(self._connect())

    def shutdown(self) -> None:
        if self.chat_service is not None and self._context is not None:
            self._context.schedule(self.chat_service.disconnect(silent=True))

    async def _connect(self) -> None:
        if self.chat_service is None:
            return
        try:
            await self.chat_service.connect(self._settings.twitch)
        except TwitchApiError as exc:
            self._set_connection_error(str(exc))

    async def _start_simulator(self) -> None:
        if self.chat_service is None:
            return
        channel = self._settings.twitch.channel or "streamcontrol"
        await self.chat_service.connect_simulated(channel=channel)

    async def _send_message(self, text: str) -> None:
        if self.chat_service is None:
            return
        try:
            await self.chat_service.send_message(text)
        except TwitchApiError as exc:
            self._set_feed_error(str(exc))

    async def _send_announcement(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        message, color = self._page.announcement_payload()
        try:
            await self.chat_service.send_announcement(message, color)
        except TwitchApiError as exc:
            self._set_engagement_error(str(exc))

    async def _send_shoutout(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        try:
            await self.chat_service.send_shoutout(self._page.shoutout_target_value())
        except TwitchApiError as exc:
            self._set_engagement_error(str(exc))

    async def _refresh_chatters(self) -> None:
        if self.chat_service is None:
            return
        try:
            await self.chat_service.refresh_chatters()
        except TwitchApiError as exc:
            self._set_moderation_error(str(exc))

    async def _timeout_selected_user(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        try:
            await self.chat_service.timeout_user(
                self._page.selected_viewer_id(),
                self._page.timeout_seconds(),
                self._page.moderation_reason_value(),
            )
        except TwitchApiError as exc:
            self._set_moderation_error(str(exc))

    async def _ban_selected_user(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        try:
            await self.chat_service.ban_user(
                self._page.selected_viewer_id(),
                self._page.moderation_reason_value(),
            )
        except TwitchApiError as exc:
            self._set_moderation_error(str(exc))

    async def _unban_selected_user(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        try:
            await self.chat_service.unban_user(self._page.selected_viewer_id())
        except TwitchApiError as exc:
            self._set_moderation_error(str(exc))

    async def _delete_selected_message(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        try:
            await self.chat_service.delete_message(self._page.selected_message_id())
        except TwitchApiError as exc:
            self._set_moderation_error(str(exc))

    async def _approve_selected_automod(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        try:
            await self.chat_service.approve_automod_message(self._page.selected_automod_id())
        except TwitchApiError as exc:
            self._set_moderation_error(str(exc))

    async def _deny_selected_automod(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        try:
            await self.chat_service.deny_automod_message(self._page.selected_automod_id())
        except TwitchApiError as exc:
            self._set_moderation_error(str(exc))

    async def _create_poll(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        question, choices, duration = self._page.poll_definition()
        try:
            await self.chat_service.create_poll(question, choices, duration)
        except TwitchApiError as exc:
            self._set_engagement_error(str(exc))

    async def _create_prediction(self) -> None:
        if self.chat_service is None or self._page is None:
            return
        title, outcomes, duration = self._page.prediction_definition()
        try:
            await self.chat_service.create_prediction(title, outcomes, duration)
        except TwitchApiError as exc:
            self._set_engagement_error(str(exc))

    def _handle_connection_changed(self, connected: bool, message: str) -> None:
        if self._page is not None:
            self._page.set_connection_status(connected, message)

    def _handle_room_state_changed(self, state: dict[str, object]) -> None:
        if self._page is not None:
            self._page.set_room_state(state)

    def _handle_message_received(self, message: ChatMessage) -> None:
        if self._page is None:
            return
        self._page.append_message(message)
        self._run_command_rules(message)

    def _handle_activity_received(self, activity: ChatActivity) -> None:
        if self._page is not None:
            self._page.append_activity(activity)

    def _handle_viewer_cards_changed(self, cards: list[ViewerCard]) -> None:
        if self._page is not None:
            self._page.set_viewer_cards(cards)

    def _handle_automod_queue_changed(self, items: list[AutoModQueueItem]) -> None:
        if self._page is not None:
            self._page.set_automod_queue(items)

    def _handle_subscription_summary_changed(self, summary: dict[str, object]) -> None:
        if self._page is not None:
            self._page.set_subscription_summary(summary)

    def _handle_history_cleared(self) -> None:
        if self._page is not None:
            self._page.clear_messages()
            self._page.clear_activities()

    def _run_command_rules(self, message: ChatMessage) -> None:
        if self.chat_service is None or self._context is None or message.kind != "message":
            return
        if message.user_login.strip().lower() == self.chat_service.current_user_login.strip().lower():
            return
        content = message.text.strip()
        if not content:
            return
        trigger = content.split()[0].lower()
        for command in self._settings.commands:
            normalized = command.trigger.strip().lower()
            if not command.enabled or not normalized:
                continue
            if trigger != normalized:
                continue
            response = command.response.strip()
            if not response:
                return
            self._context.schedule(self.chat_service.send_message(response))
            if self._page is not None:
                self._page.set_feed_status(True, f"Auto-responded to {normalized}.")
            return

    def _set_connection_error(self, message: str) -> None:
        if self._page is not None:
            self._page.set_connection_status(False, message)

    def _set_feed_error(self, message: str) -> None:
        if self._page is not None:
            self._page.set_feed_status(False, message)

    def _set_moderation_error(self, message: str) -> None:
        if self._page is not None:
            self._page.set_moderation_status(False, message)

    def _set_engagement_error(self, message: str) -> None:
        if self._page is not None:
            self._page.set_engagement_status(False, message)

    def _save_settings(self) -> None:
        if self._context is None or self._page is None:
            return
        self._settings.commands = self._page.command_rules()
        self._settings.quick_replies = self._page.quick_replies()
        self._context.save_plugin_settings(self.plugin_id, self._settings.to_dict())
