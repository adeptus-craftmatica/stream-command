from __future__ import annotations

import asyncio
import json
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib import error, parse, request

from PySide6.QtCore import QObject, Signal

from stream_control.core.tls import describe_tls_error, tls_context, websocket_ssl_options
from stream_control.services.twitch_service import TwitchApiError

try:  # pragma: no cover - exercised through runtime integration
    import websocket
except ImportError:  # pragma: no cover - surfaced as a connection error in the UI
    websocket = None


@dataclass(slots=True)
class TwitchChatSettings:
    channel: str = ""
    client_id: str = ""
    access_token: str = ""
    broadcaster_id: str = ""
    moderator_id: str = ""
    auto_connect: bool = False
    simulator_auto_start: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "TwitchChatSettings":
        return cls(
            channel=str(raw.get("channel", "")).strip(),
            client_id=str(raw.get("client_id", "")).strip(),
            access_token=str(raw.get("access_token", "") or raw.get("oauth_token", "")).strip(),
            broadcaster_id=str(raw.get("broadcaster_id", "")).strip(),
            moderator_id=str(raw.get("moderator_id", "")).strip(),
            auto_connect=bool(raw.get("auto_connect", False)),
            simulator_auto_start=bool(raw.get("simulator_auto_start", False)),
        )


@dataclass(slots=True)
class ChatMessage:
    id: str
    timestamp: str
    user_login: str
    display_name: str
    text: str
    color: str = ""
    kind: str = "message"
    badges: str = ""
    is_first_message: bool = False
    is_action: bool = False
    user_id: str = ""


@dataclass(slots=True)
class ChatActivity:
    id: str
    timestamp: str
    kind: str
    summary: str
    detail: str = ""
    user_id: str = ""
    user_login: str = ""
    display_name: str = ""


@dataclass(slots=True)
class ViewerCard:
    user_id: str
    user_login: str
    display_name: str
    color: str = ""
    badges: str = ""
    roles: list[str] = field(default_factory=list)
    last_message: str = ""
    last_seen: str = ""
    message_count: int = 0
    is_following: bool = False
    is_subscribed: bool = False
    last_activity_epoch: float = 0.0


@dataclass(slots=True)
class AutoModQueueItem:
    id: str
    timestamp: str
    user_id: str
    user_login: str
    display_name: str
    text: str
    status: str = "PENDING"
    reason: str = ""


class TwitchChatService(QObject):
    BASE_URL = "https://api.twitch.tv/helix"
    EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"

    connection_changed = Signal(bool, str)
    message_received = Signal(object)
    room_state_changed = Signal(object)
    history_cleared = Signal()
    activity_received = Signal(object)
    viewer_cards_changed = Signal(object)
    automod_queue_changed = Signal(object)
    subscription_summary_changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._simulation_enabled = False
        self._channel = ""
        self._client_id = ""
        self._access_token = ""
        self._broadcaster_id = ""
        self._moderator_id = ""
        self._current_user_id = ""
        self._current_user_login = ""
        self._broadcaster_name = ""
        self._eventsub_session_id = ""
        self._eventsub_subscriptions: list[dict[str, str]] = []
        self._subscription_warnings: list[str] = []
        self._subscription_errors: list[str] = []
        self._transport_error = ""
        self._session_ready = threading.Event()
        self._stop_event = threading.Event()
        self._ws_thread: threading.Thread | None = None
        self._ws_app: Any | None = None
        self._reconnect_url = ""
        self._shutting_down = False
        self._message_count = 0
        self._messages_by_id: dict[str, ChatMessage] = {}
        self._viewer_cards: dict[str, ViewerCard] = {}
        self._automod_queue: dict[str, AutoModQueueItem] = {}
        self._room_state: dict[str, object] = {
            "channel": "",
            "slow_mode": 0,
            "followers_only": -1,
            "subs_only": False,
            "emote_only": False,
            "unique_chat": False,
            "non_moderator_chat_delay": False,
            "non_moderator_chat_delay_duration": 0,
        }
        self._sample_users = [
            ("pixelpilot", "PixelPilot", "#7cc7ff", ["moderator"]),
            ("modmaven", "ModMaven", "#59d699", ["vip"]),
            ("chatcapsule", "ChatCapsule", "#f6b566", ["subscriber"]),
            ("nightscene", "NightScene", "#d79bff", []),
        ]
        self._sample_messages = [
            "Audio sounds clean tonight.",
            "Scene transition looked smooth.",
            "Can we get the song title on screen?",
            "This dashboard is starting to feel legit.",
            "Quick BRB scene swap worked perfectly.",
        ]
        self._simulator_task: asyncio.Task[None] | None = None

    @property
    def is_simulated(self) -> bool:
        return self._simulation_enabled

    @property
    def is_connected(self) -> bool:
        return self._simulation_enabled or self._connected

    @property
    def current_user_login(self) -> str:
        return self._current_user_login

    @property
    def current_user_id(self) -> str:
        return self._current_user_id

    def subscription_summary(self) -> dict[str, object]:
        mode = "simulator" if self._simulation_enabled else ("eventsub" if self._connected else "disconnected")
        return {
            "mode": mode,
            "channel": self._channel,
            "broadcaster_id": self._broadcaster_id,
            "moderator_id": self._moderator_id,
            "current_user_id": self._current_user_id,
            "current_user_login": self._current_user_login,
            "subscription_types": [entry["type"] for entry in self._eventsub_subscriptions],
            "subscription_errors": list(self._subscription_errors),
            "subscription_warnings": list(self._subscription_warnings),
        }

    async def connect(self, settings: TwitchChatSettings) -> None:
        await self.disconnect(silent=True)
        self._loop = asyncio.get_running_loop()
        self._shutting_down = False

        channel = settings.channel.strip().lower().lstrip("#")
        client_id = settings.client_id.strip()
        access_token = self._normalize_token(settings.access_token)

        if websocket is None:
            self.connection_changed.emit(False, "Install websocket-client before using Twitch EventSub chat.")
            return
        if not channel:
            self.connection_changed.emit(False, "Enter the Twitch channel you want to manage before connecting chat.")
            return
        if not client_id:
            self.connection_changed.emit(False, "Enter the Twitch client ID before connecting chat.")
            return
        if not access_token:
            self.connection_changed.emit(False, "Enter a Twitch user access token before connecting chat.")
            return

        self._channel = channel
        self._client_id = client_id
        self._access_token = access_token
        self._subscription_errors.clear()
        self._subscription_warnings.clear()
        self._eventsub_subscriptions.clear()
        self._transport_error = ""
        self._clear_state(emit_signal=False)

        try:
            runtime = await asyncio.to_thread(self._resolve_runtime_context_sync, settings)
        except TwitchApiError as exc:
            self.connection_changed.emit(False, str(exc))
            return

        self._broadcaster_id = runtime["broadcaster_id"]
        self._broadcaster_name = runtime["broadcaster_name"]
        self._channel = runtime["channel"]
        self._moderator_id = runtime["moderator_id"]
        self._current_user_id = runtime["current_user_id"]
        self._current_user_login = runtime["current_user_login"]

        self._start_eventsub_thread()
        session_ready = await asyncio.to_thread(self._session_ready.wait, 8.0)
        if not session_ready or not self._eventsub_session_id:
            await self.disconnect(silent=True)
            detail = self._transport_error or "Twitch never completed the EventSub WebSocket handshake."
            self.connection_changed.emit(False, f"Twitch chat connection failed: {detail}")
            return

        created, warnings = await asyncio.to_thread(self._create_eventsub_subscriptions_sync)
        self._subscription_warnings.extend(warnings)
        self._connected = bool(created)
        self.subscription_summary_changed.emit(self.subscription_summary())

        if not created:
            await self.disconnect(silent=True)
            detail = self._transport_error or "Twitch did not accept any EventSub subscriptions for this chat session."
            self.connection_changed.emit(False, f"Twitch chat connection failed: {detail}")
            return

        try:
            await self.refresh_room_state()
        except TwitchApiError as exc:
            self._subscription_warnings.append(str(exc))
        try:
            await self.refresh_chatters()
        except TwitchApiError as exc:
            self._subscription_warnings.append(str(exc))

        self.subscription_summary_changed.emit(self.subscription_summary())
        warning_summary = ""
        if self._subscription_warnings:
            preview = "; ".join(self._subscription_warnings[:2])
            if len(self._subscription_warnings) > 2:
                preview += "; ..."
            warning_summary = f" Partial feature coverage: {preview}"
        self.connection_changed.emit(
            True,
            f"Connected to Twitch EventSub chat for #{self._channel}.{warning_summary}".strip(),
        )
        self._emit_activity(
            ChatActivity(
                id=f"system-{self._message_count}",
                timestamp=self._timestamp(),
                kind="system",
                summary=f"Twitch chat connected for #{self._channel}.",
                detail="EventSub is active for chat, moderation, and engagement events.",
            )
        )

    async def connect_simulated(self, channel: str = "streamcontrol") -> None:
        await self.disconnect(silent=True)
        self._loop = asyncio.get_running_loop()
        self._simulation_enabled = True
        self._connected = True
        self._channel = channel.strip().lower().lstrip("#") or "streamcontrol"
        self._client_id = "simulator"
        self._access_token = "simulator"
        self._broadcaster_id = "sim-broadcaster"
        self._broadcaster_name = self._channel
        self._moderator_id = "sim-moderator"
        self._current_user_id = "sim-bot"
        self._current_user_login = "streamcontrol"
        self._eventsub_session_id = "simulated-session"
        self._eventsub_subscriptions = [
            {"type": "channel.chat.message", "version": "1"},
            {"type": "channel.chat.notification", "version": "1"},
            {"type": "channel.chat_settings.update", "version": "1"},
            {"type": "automod.message.hold", "version": "2"},
            {"type": "channel.channel_points_custom_reward_redemption.add", "version": "1"},
            {"type": "channel.poll.begin", "version": "1"},
            {"type": "channel.prediction.begin", "version": "1"},
        ]
        self._clear_state(emit_signal=False)
        self._room_state = {
            "channel": self._channel,
            "slow_mode": 0,
            "followers_only": -1,
            "subs_only": False,
            "emote_only": False,
            "unique_chat": False,
            "non_moderator_chat_delay": False,
            "non_moderator_chat_delay_duration": 0,
        }
        self.room_state_changed.emit(dict(self._room_state))
        self.subscription_summary_changed.emit(self.subscription_summary())

        for index, (login, name, color, roles) in enumerate(self._sample_users, start=1):
            self._touch_viewer(
                user_id=f"sim-user-{index}",
                user_login=login,
                display_name=name,
                color=color,
                roles=roles,
            )
        self.connection_changed.emit(True, f"Connected to the built-in Twitch EventSub simulator for #{self._channel}.")
        self._emit_activity(
            ChatActivity(
                id="sim-welcome",
                timestamp=self._timestamp(),
                kind="system",
                summary="Simulator connected.",
                detail="You can test moderation, engagement, and command workflows here without going live.",
            )
        )
        self._simulator_task = asyncio.create_task(self._run_simulator())

    async def disconnect(self, silent: bool = False) -> None:
        self._shutting_down = True
        if self._simulator_task is not None:
            self._simulator_task.cancel()
            self._simulator_task = None

        self._stop_event.set()
        if self._ws_app is not None:
            try:
                self._ws_app.close()
            except Exception:
                pass

        thread = self._ws_thread
        self._ws_thread = None
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 1.5)

        self._ws_app = None
        self._connected = False
        was_simulated = self._simulation_enabled
        self._simulation_enabled = False
        self._eventsub_session_id = ""
        self._eventsub_subscriptions.clear()
        self._subscription_errors.clear()
        self._subscription_warnings.clear()
        self.subscription_summary_changed.emit(self.subscription_summary())

        if not silent:
            if was_simulated:
                self.connection_changed.emit(False, "Twitch chat simulator disconnected.")
            else:
                self.connection_changed.emit(False, "Twitch EventSub chat disconnected.")

        self._stop_event = threading.Event()
        self._session_ready = threading.Event()

    async def send_message(self, text: str, reply_parent_message_id: str = "") -> None:
        message = text.strip()
        if not message:
            self.connection_changed.emit(False, "Type a chat message before sending it.")
            return

        if self._simulation_enabled:
            self._emit_message(
                ChatMessage(
                    id=f"sim-out-{self._message_count}",
                    timestamp=self._timestamp(),
                    user_login=self._current_user_login or "you",
                    display_name="You",
                    text=message,
                    color="#68bfd4",
                    kind="message",
                    user_id=self._current_user_id,
                )
            )
            self.connection_changed.emit(True, "Sent the chat message to the simulator.")
            return

        self._require_live_connection()
        payload: dict[str, Any] = {
            "broadcaster_id": self._broadcaster_id,
            "sender_id": self._current_user_id,
            "message": message,
        }
        if reply_parent_message_id.strip():
            payload["reply_parent_message_id"] = reply_parent_message_id.strip()
        response = await asyncio.to_thread(self._request_json_sync, "POST", "/chat/messages", body=payload)
        entry = self._first_data_entry(response)
        if entry and not bool(entry.get("is_sent", False)):
            drop_reason = dict(entry.get("drop_reason", {}))
            raise_message = drop_reason.get("message") or "Twitch dropped the message."
            self.connection_changed.emit(False, str(raise_message))
            return
        self.connection_changed.emit(True, "Sent the chat message to Twitch.")

    async def send_announcement(self, text: str, color: str = "primary") -> None:
        message = text.strip()
        if not message:
            self.connection_changed.emit(False, "Type an announcement before sending it.")
            return

        if self._simulation_enabled:
            self._emit_activity(
                ChatActivity(
                    id=f"announce-{self._message_count}",
                    timestamp=self._timestamp(),
                    kind="announcement",
                    summary="Announcement sent.",
                    detail=message,
                )
            )
            self.connection_changed.emit(True, "Sent the announcement to the simulator.")
            return

        self._require_live_connection()
        await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/chat/announcements",
            query={"broadcaster_id": self._broadcaster_id, "moderator_id": self._moderator_id},
            body={"message": message, "color": color or "primary"},
        )
        self.connection_changed.emit(True, "Sent the Twitch announcement.")

    async def send_shoutout(self, target: str) -> None:
        cleaned = target.strip().lstrip("#").lstrip("@")
        if not cleaned:
            self.connection_changed.emit(False, "Enter a channel login before sending a shoutout.")
            return

        if self._simulation_enabled:
            self._emit_activity(
                ChatActivity(
                    id=f"shoutout-{self._message_count}",
                    timestamp=self._timestamp(),
                    kind="shoutout",
                    summary=f"Shoutout sent to {cleaned}.",
                    detail="Simulator shoutout completed.",
                )
            )
            self.connection_changed.emit(True, "Sent the shoutout to the simulator.")
            return

        self._require_live_connection()
        target_id, target_name = await asyncio.to_thread(self._resolve_user_identifier_sync, cleaned)
        await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/chat/shoutouts",
            query={
                "from_broadcaster_id": self._broadcaster_id,
                "to_broadcaster_id": target_id,
                "moderator_id": self._moderator_id,
            },
        )
        self.connection_changed.emit(True, f"Sent a Twitch shoutout to {target_name}.")

    async def delete_message(self, message_id: str) -> None:
        cleaned = message_id.strip()
        if not cleaned:
            self.connection_changed.emit(False, "Select a chat message before deleting it.")
            return

        if self._simulation_enabled:
            self._emit_activity(
                ChatActivity(
                    id=f"delete-{cleaned}",
                    timestamp=self._timestamp(),
                    kind="moderation",
                    summary="Deleted a chat message.",
                    detail=f"Simulator removed message {cleaned}.",
                )
            )
            self.connection_changed.emit(True, "Deleted the simulator chat message.")
            return

        self._require_live_connection()
        await asyncio.to_thread(
            self._request_json_sync,
            "DELETE",
            "/moderation/chat",
            query={
                "broadcaster_id": self._broadcaster_id,
                "moderator_id": self._moderator_id,
                "message_id": cleaned,
            },
        )
        self.connection_changed.emit(True, "Requested chat message deletion from Twitch.")

    async def timeout_user(self, user_id: str, duration_seconds: int, reason: str = "") -> None:
        cleaned = user_id.strip()
        if not cleaned:
            self.connection_changed.emit(False, "Select a viewer before timing them out.")
            return

        duration = max(1, min(int(duration_seconds), 1_209_600))
        if self._simulation_enabled:
            viewer = self._viewer_cards.get(cleaned)
            label = viewer.display_name if viewer is not None else cleaned
            self._emit_activity(
                ChatActivity(
                    id=f"timeout-{cleaned}",
                    timestamp=self._timestamp(),
                    kind="moderation",
                    summary=f"Timed out {label} for {duration} seconds.",
                    detail=reason.strip(),
                    user_id=cleaned,
                )
            )
            self.connection_changed.emit(True, "Timed out the simulator viewer.")
            return

        self._require_live_connection()
        payload = {"data": {"user_id": cleaned, "duration": duration}}
        if reason.strip():
            payload["data"]["reason"] = reason.strip()
        await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/moderation/bans",
            query={"broadcaster_id": self._broadcaster_id, "moderator_id": self._moderator_id},
            body=payload,
        )
        self.connection_changed.emit(True, "Timed out the selected Twitch viewer.")

    async def ban_user(self, user_id: str, reason: str = "") -> None:
        cleaned = user_id.strip()
        if not cleaned:
            self.connection_changed.emit(False, "Select a viewer before banning them.")
            return

        if self._simulation_enabled:
            viewer = self._viewer_cards.get(cleaned)
            label = viewer.display_name if viewer is not None else cleaned
            self._emit_activity(
                ChatActivity(
                    id=f"ban-{cleaned}",
                    timestamp=self._timestamp(),
                    kind="moderation",
                    summary=f"Banned {label}.",
                    detail=reason.strip(),
                    user_id=cleaned,
                )
            )
            self.connection_changed.emit(True, "Banned the simulator viewer.")
            return

        self._require_live_connection()
        payload = {"data": {"user_id": cleaned}}
        if reason.strip():
            payload["data"]["reason"] = reason.strip()
        await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/moderation/bans",
            query={"broadcaster_id": self._broadcaster_id, "moderator_id": self._moderator_id},
            body=payload,
        )
        self.connection_changed.emit(True, "Banned the selected Twitch viewer.")

    async def unban_user(self, user_id: str) -> None:
        cleaned = user_id.strip()
        if not cleaned:
            self.connection_changed.emit(False, "Select a viewer before removing their timeout or ban.")
            return

        if self._simulation_enabled:
            viewer = self._viewer_cards.get(cleaned)
            label = viewer.display_name if viewer is not None else cleaned
            self._emit_activity(
                ChatActivity(
                    id=f"unban-{cleaned}",
                    timestamp=self._timestamp(),
                    kind="moderation",
                    summary=f"Removed moderation from {label}.",
                    user_id=cleaned,
                )
            )
            self.connection_changed.emit(True, "Cleared the simulator timeout or ban.")
            return

        self._require_live_connection()
        await asyncio.to_thread(
            self._request_json_sync,
            "DELETE",
            "/moderation/bans",
            query={
                "broadcaster_id": self._broadcaster_id,
                "moderator_id": self._moderator_id,
                "user_id": cleaned,
            },
        )
        self.connection_changed.emit(True, "Removed the timeout or ban from Twitch.")

    async def approve_automod_message(self, message_id: str) -> None:
        await self._manage_automod_message(message_id, "ALLOW", "Approved the held AutoMod message.")

    async def deny_automod_message(self, message_id: str) -> None:
        await self._manage_automod_message(message_id, "DENY", "Denied the held AutoMod message.")

    async def create_poll(self, question: str, choices: list[str], duration_seconds: int) -> None:
        prompt = question.strip()
        entries = [choice.strip() for choice in choices if choice.strip()]
        if not prompt:
            self.connection_changed.emit(False, "Enter a poll question before creating a poll.")
            return
        if len(entries) < 2:
            self.connection_changed.emit(False, "Add at least two poll choices before creating a poll.")
            return

        duration = max(15, min(int(duration_seconds), 1_800))
        if self._simulation_enabled:
            self._emit_activity(
                ChatActivity(
                    id=f"poll-{self._message_count}",
                    timestamp=self._timestamp(),
                    kind="poll",
                    summary=f"Poll started: {prompt}",
                    detail=", ".join(entries),
                )
            )
            self.connection_changed.emit(True, "Created the simulator poll.")
            return

        self._require_live_connection()
        await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/polls",
            body={
                "broadcaster_id": self._broadcaster_id,
                "title": prompt,
                "choices": [{"title": choice} for choice in entries[:5]],
                "duration": duration,
            },
        )
        self.connection_changed.emit(True, "Created the Twitch poll.")

    async def create_prediction(self, title: str, outcomes: list[str], window_seconds: int) -> None:
        prompt = title.strip()
        entries = [choice.strip() for choice in outcomes if choice.strip()]
        if not prompt:
            self.connection_changed.emit(False, "Enter a prediction title before creating a prediction.")
            return
        if len(entries) < 2:
            self.connection_changed.emit(False, "Add at least two prediction outcomes before creating a prediction.")
            return

        duration = max(15, min(int(window_seconds), 1_800))
        if self._simulation_enabled:
            self._emit_activity(
                ChatActivity(
                    id=f"prediction-{self._message_count}",
                    timestamp=self._timestamp(),
                    kind="prediction",
                    summary=f"Prediction started: {prompt}",
                    detail=", ".join(entries),
                )
            )
            self.connection_changed.emit(True, "Created the simulator prediction.")
            return

        self._require_live_connection()
        await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/predictions",
            body={
                "broadcaster_id": self._broadcaster_id,
                "title": prompt,
                "outcomes": [{"title": choice} for choice in entries[:10]],
                "prediction_window": duration,
            },
        )
        self.connection_changed.emit(True, "Created the Twitch prediction.")

    async def refresh_chatters(self) -> None:
        if self._simulation_enabled:
            self.viewer_cards_changed.emit(self._sorted_viewer_cards())
            self.connection_changed.emit(True, "Refreshed the simulated viewer list.")
            return

        self._require_live_connection()
        entries = await asyncio.to_thread(self._fetch_chatters_sync)
        for entry in entries:
            self._touch_viewer(
                user_id=str(entry.get("user_id", "")),
                user_login=str(entry.get("user_login", "")),
                display_name=str(entry.get("user_name", "")) or str(entry.get("user_login", "")),
            )
        self.viewer_cards_changed.emit(self._sorted_viewer_cards())
        self.connection_changed.emit(True, "Refreshed the Twitch viewer list.")

    async def refresh_room_state(self) -> None:
        if self._simulation_enabled:
            self.room_state_changed.emit(dict(self._room_state))
            return

        self._require_live_connection()
        payload = await asyncio.to_thread(
            self._request_json_sync,
            "GET",
            "/chat/settings",
            query={"broadcaster_id": self._broadcaster_id, "moderator_id": self._moderator_id},
        )
        entry = self._first_data_entry(payload)
        if not entry:
            raise TwitchApiError("Twitch did not return any chat settings for this channel.")
        self._room_state = self._coerce_room_state(entry)
        self.room_state_changed.emit(dict(self._room_state))

    def clear_history(self) -> None:
        self._messages_by_id.clear()
        self.history_cleared.emit()

    @staticmethod
    def _normalize_token(token: str) -> str:
        cleaned = token.strip()
        if cleaned.lower().startswith("bearer "):
            cleaned = cleaned[7:].strip()
        if cleaned.lower().startswith("oauth:"):
            cleaned = cleaned[6:].strip()
        return cleaned

    @staticmethod
    def _parse_irc_line(line: str) -> dict[str, Any]:
        rest = line
        tags: dict[str, str] = {}
        prefix = ""

        if rest.startswith("@"):
            tags_part, rest = rest.split(" ", 1)
            for raw_tag in tags_part[1:].split(";"):
                key, _, value = raw_tag.partition("=")
                tags[key] = value

        if rest.startswith(":"):
            prefix_part, rest = rest.split(" ", 1)
            prefix = prefix_part[1:]

        trailing = ""
        if " :" in rest:
            rest, trailing = rest.split(" :", 1)

        parts = rest.split()
        command = parts[0] if parts else ""
        params = parts[1:] if len(parts) > 1 else []
        return {
            "tags": tags,
            "prefix": prefix,
            "command": command,
            "params": params,
            "trailing": trailing,
        }

    def _require_live_connection(self) -> None:
        if not self._connected or self._simulation_enabled:
            if self._simulation_enabled:
                return
            raise TwitchApiError("Connect Twitch chat before using live moderation or engagement actions.")

    def _resolve_runtime_context_sync(self, settings: TwitchChatSettings) -> dict[str, str]:
        current_user = self._fetch_current_user_sync(settings.client_id, self._access_token)
        broadcaster_id = settings.broadcaster_id.strip()
        broadcaster_name = settings.channel.strip().lstrip("#")
        if broadcaster_id:
            broadcaster = self._fetch_user_by_id_sync(settings.client_id, self._access_token, broadcaster_id)
            broadcaster_id = str(broadcaster.get("id", broadcaster_id))
            broadcaster_name = str(broadcaster.get("display_name", broadcaster_name or broadcaster.get("login", "")))
            channel = str(broadcaster.get("login", broadcaster_name)).strip().lower()
        else:
            broadcaster = self._fetch_user_by_login_sync(settings.client_id, self._access_token, broadcaster_name)
            broadcaster_id = str(broadcaster.get("id", "")).strip()
            if not broadcaster_id:
                raise TwitchApiError("Twitch did not return a broadcaster ID for that channel.")
            broadcaster_name = str(broadcaster.get("display_name", broadcaster_name or broadcaster.get("login", "")))
            channel = str(broadcaster.get("login", broadcaster_name)).strip().lower()
        moderator_id = settings.moderator_id.strip() or str(current_user.get("id", "")).strip()
        current_user_id = str(current_user.get("id", "")).strip()
        current_user_login = str(current_user.get("login", "")).strip().lower()
        if not current_user_id or not current_user_login:
            raise TwitchApiError("Twitch did not return the authenticated user for this access token.")
        return {
            "channel": channel,
            "broadcaster_id": broadcaster_id,
            "broadcaster_name": broadcaster_name,
            "moderator_id": moderator_id,
            "current_user_id": current_user_id,
            "current_user_login": current_user_login,
        }

    def _fetch_current_user_sync(self, client_id: str, access_token: str) -> dict[str, Any]:
        payload = self._request_json_sync("GET", "/users", client_id=client_id, access_token=access_token)
        entry = self._first_data_entry(payload)
        if not entry:
            raise TwitchApiError("Twitch did not return the current user for this access token.")
        return entry

    def _fetch_user_by_login_sync(self, client_id: str, access_token: str, login: str) -> dict[str, Any]:
        payload = self._request_json_sync(
            "GET",
            "/users",
            client_id=client_id,
            access_token=access_token,
            query={"login": login.strip().lower()},
        )
        entry = self._first_data_entry(payload)
        if not entry:
            raise TwitchApiError(f"Twitch could not find the channel '{login}'.")
        return entry

    def _fetch_user_by_id_sync(self, client_id: str, access_token: str, user_id: str) -> dict[str, Any]:
        payload = self._request_json_sync(
            "GET",
            "/users",
            client_id=client_id,
            access_token=access_token,
            query={"id": user_id.strip()},
        )
        entry = self._first_data_entry(payload)
        if not entry:
            raise TwitchApiError(f"Twitch could not find the user ID '{user_id}'.")
        return entry

    def _resolve_user_identifier_sync(self, login_or_id: str) -> tuple[str, str]:
        cleaned = login_or_id.strip().lstrip("#").lstrip("@")
        if not cleaned:
            raise TwitchApiError("Enter a Twitch login or user ID first.")
        if cleaned.isdigit():
            user = self._fetch_user_by_id_sync(self._client_id, self._access_token, cleaned)
        else:
            user = self._fetch_user_by_login_sync(self._client_id, self._access_token, cleaned)
        user_id = str(user.get("id", "")).strip()
        user_name = str(user.get("display_name", "") or user.get("login", "")).strip()
        if not user_id:
            raise TwitchApiError(f"Twitch did not return an ID for '{cleaned}'.")
        return user_id, user_name or cleaned

    def _fetch_chatters_sync(self) -> list[dict[str, Any]]:
        query = {
            "broadcaster_id": self._broadcaster_id,
            "moderator_id": self._moderator_id,
            "first": 100,
        }
        chatters: list[dict[str, Any]] = []
        cursor = ""
        while True:
            if cursor:
                query["after"] = cursor
            else:
                query.pop("after", None)
            payload = self._request_json_sync("GET", "/chat/chatters", query=query)
            chatters.extend(list(payload.get("data", [])))
            cursor = str(dict(payload.get("pagination", {})).get("cursor", "")).strip()
            if not cursor or len(chatters) >= 500:
                break
        return chatters

    def _create_eventsub_subscriptions_sync(self) -> tuple[bool, list[str]]:
        specs = self._subscription_specs()
        created_any = False
        warnings: list[str] = []
        for spec in specs:
            try:
                self._request_json_sync(
                    "POST",
                    "/eventsub/subscriptions",
                    body={
                        "type": spec["type"],
                        "version": spec["version"],
                        "condition": spec["condition"],
                        "transport": {
                            "method": "websocket",
                            "session_id": self._eventsub_session_id,
                        },
                    },
                )
                self._eventsub_subscriptions.append({"type": spec["type"], "version": spec["version"]})
                created_any = True
            except TwitchApiError as exc:
                message = f"{spec['label']}: {exc}"
                if spec["required"]:
                    self._subscription_errors.append(message)
                else:
                    warnings.append(message)
        return created_any, warnings

    def _subscription_specs(self) -> list[dict[str, Any]]:
        base_reader = {"broadcaster_user_id": self._broadcaster_id, "user_id": self._current_user_id}
        moderator_scope = {"broadcaster_user_id": self._broadcaster_id, "moderator_user_id": self._moderator_id}
        specs = [
            {
                "type": "channel.chat.message",
                "version": "1",
                "condition": base_reader,
                "label": "Chat messages",
                "required": True,
            },
            {
                "type": "channel.chat.notification",
                "version": "1",
                "condition": base_reader,
                "label": "Chat notifications",
                "required": True,
            },
            {
                "type": "channel.chat.message_delete",
                "version": "1",
                "condition": base_reader,
                "label": "Chat deletions",
                "required": False,
            },
            {
                "type": "channel.chat_settings.update",
                "version": "1",
                "condition": base_reader,
                "label": "Chat settings",
                "required": False,
            },
            {
                "type": "automod.message.hold",
                "version": "2",
                "condition": moderator_scope,
                "label": "AutoMod queue",
                "required": False,
            },
            {
                "type": "automod.message.update",
                "version": "2",
                "condition": moderator_scope,
                "label": "AutoMod reviews",
                "required": False,
            },
            {
                "type": "channel.follow",
                "version": "2",
                "condition": moderator_scope,
                "label": "Follows",
                "required": False,
            },
            {
                "type": "channel.subscribe",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Subscriptions",
                "required": False,
            },
            {
                "type": "channel.subscription.gift",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Gift subscriptions",
                "required": False,
            },
            {
                "type": "channel.subscription.message",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Resubscription messages",
                "required": False,
            },
            {
                "type": "channel.subscription.end",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Subscription endings",
                "required": False,
            },
            {
                "type": "channel.cheer",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Cheers",
                "required": False,
            },
            {
                "type": "channel.raid",
                "version": "1",
                "condition": {"to_broadcaster_user_id": self._broadcaster_id},
                "label": "Incoming raids",
                "required": False,
            },
            {
                "type": "channel.channel_points_custom_reward_redemption.add",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Channel point redeems",
                "required": False,
            },
            {
                "type": "channel.poll.begin",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Polls",
                "required": False,
            },
            {
                "type": "channel.poll.progress",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Poll updates",
                "required": False,
            },
            {
                "type": "channel.poll.end",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Poll results",
                "required": False,
            },
            {
                "type": "channel.prediction.begin",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Predictions",
                "required": False,
            },
            {
                "type": "channel.prediction.progress",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Prediction updates",
                "required": False,
            },
            {
                "type": "channel.prediction.lock",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Prediction lock events",
                "required": False,
            },
            {
                "type": "channel.prediction.end",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Prediction results",
                "required": False,
            },
            {
                "type": "channel.shoutout.create",
                "version": "1",
                "condition": moderator_scope,
                "label": "Outgoing shoutouts",
                "required": False,
            },
            {
                "type": "channel.shoutout.receive",
                "version": "1",
                "condition": {"broadcaster_user_id": self._broadcaster_id},
                "label": "Incoming shoutouts",
                "required": False,
            },
        ]
        return specs

    def _start_eventsub_thread(self) -> None:
        self._session_ready = threading.Event()
        self._stop_event = threading.Event()
        self._eventsub_session_id = ""
        self._reconnect_url = ""
        self._transport_error = ""
        self._ws_thread = threading.Thread(target=self._run_eventsub_forever, daemon=True)
        self._ws_thread.start()

    def _run_eventsub_forever(self) -> None:  # pragma: no cover - exercised manually
        url = self.EVENTSUB_URL
        while not self._stop_event.is_set():
            app = websocket.WebSocketApp(
                url,
                on_message=self._on_ws_message,
                on_error=self._on_ws_error,
                on_close=self._on_ws_close,
            )
            self._ws_app = app
            try:
                app.run_forever(skip_utf8_validation=True, sslopt=websocket_ssl_options())
            except Exception as exc:
                self._transport_error = describe_tls_error(exc)
                self._session_ready.set()
                self._notify_async(
                    lambda: self.connection_changed.emit(False, f"Twitch EventSub error: {describe_tls_error(exc)}")
                )
            if self._stop_event.is_set():
                return
            if self._reconnect_url:
                url = self._reconnect_url
                self._reconnect_url = ""
                continue
            return

    def _on_ws_message(self, _ws: Any, message: str) -> None:  # pragma: no cover - exercised manually
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        metadata = dict(payload.get("metadata", {}))
        message_type = str(metadata.get("message_type", "")).strip()
        if message_type == "session_welcome":
            session = dict(dict(payload.get("payload", {})).get("session", {}))
            self._eventsub_session_id = str(session.get("id", "")).strip()
            self._session_ready.set()
            self._notify_async(self._emit_subscription_summary)
            return
        if message_type == "session_reconnect":
            session = dict(dict(payload.get("payload", {})).get("session", {}))
            self._reconnect_url = str(session.get("reconnect_url", "")).strip()
            if self._ws_app is not None:
                try:
                    self._ws_app.close()
                except Exception:
                    pass
            return
        if message_type == "revocation":
            subscription = dict(dict(payload.get("payload", {})).get("subscription", {}))
            reason = str(subscription.get("status", "")).replace("_", " ").strip() or "revoked"
            sub_type = str(subscription.get("type", "subscription")).strip()
            self._notify_async(
                lambda: self._emit_activity(
                    ChatActivity(
                        id=f"revocation-{sub_type}",
                        timestamp=self._timestamp(),
                        kind="system",
                        summary=f"Twitch revoked the {sub_type} subscription.",
                        detail=reason.title(),
                    )
                )
            )
            return
        if message_type != "notification":
            return
        payload_body = dict(payload.get("payload", {}))
        subscription = dict(payload_body.get("subscription", {}))
        event = dict(payload_body.get("event", {}))
        subscription_type = str(subscription.get("type", "")).strip()
        self._notify_async(lambda: self._handle_eventsub_notification(subscription_type, event))

    def _on_ws_error(self, _ws: Any, error_value: Any) -> None:  # pragma: no cover - exercised manually
        self._transport_error = describe_tls_error(error_value)
        self._session_ready.set()

    def _on_ws_close(self, _ws: Any, _status_code: Any, _message: Any) -> None:  # pragma: no cover - exercised manually
        self._session_ready.set()
        if self._stop_event.is_set() or self._reconnect_url:
            return
        self._notify_async(self._handle_transport_close)

    def _notify_async(self, callback: Any) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(callback)

    def _handle_transport_close(self) -> None:
        if self._shutting_down or self._simulation_enabled or not self._connected:
            return
        self._connected = False
        self.subscription_summary_changed.emit(self.subscription_summary())
        detail = self._transport_error or "Twitch EventSub closed the chat session."
        self.connection_changed.emit(False, detail)

    def _handle_eventsub_notification(self, subscription_type: str, event: dict[str, Any]) -> None:
        handlers = {
            "channel.chat.message": self._handle_chat_message_event,
            "channel.chat.notification": self._handle_chat_notification_event,
            "channel.chat_settings.update": self._handle_chat_settings_update_event,
            "channel.chat.message_delete": self._handle_chat_message_delete_event,
            "automod.message.hold": self._handle_automod_hold_event,
            "automod.message.update": self._handle_automod_update_event,
            "channel.follow": self._handle_follow_event,
            "channel.subscribe": self._handle_subscribe_event,
            "channel.subscription.gift": self._handle_subscription_gift_event,
            "channel.subscription.message": self._handle_subscription_message_event,
            "channel.subscription.end": self._handle_subscription_end_event,
            "channel.cheer": self._handle_cheer_event,
            "channel.raid": self._handle_raid_event,
            "channel.channel_points_custom_reward_redemption.add": self._handle_redeem_event,
            "channel.poll.begin": self._handle_poll_event,
            "channel.poll.progress": self._handle_poll_event,
            "channel.poll.end": self._handle_poll_event,
            "channel.prediction.begin": self._handle_prediction_event,
            "channel.prediction.progress": self._handle_prediction_event,
            "channel.prediction.lock": self._handle_prediction_event,
            "channel.prediction.end": self._handle_prediction_event,
            "channel.shoutout.create": self._handle_shoutout_create_event,
            "channel.shoutout.receive": self._handle_shoutout_receive_event,
        }
        handler = handlers.get(subscription_type)
        if handler is not None:
            handler(event)

    def _handle_chat_message_event(self, event: dict[str, Any]) -> None:
        badges = self._format_badges(event.get("badges", []))
        message = ChatMessage(
            id=str(event.get("message_id", "")).strip() or f"eventsub-{self._message_count}",
            timestamp=self._timestamp_from_event(event, "sent_at", "created_at"),
            user_login=str(event.get("chatter_user_login", "")),
            display_name=str(event.get("chatter_user_name", "")) or str(event.get("chatter_user_login", "")),
            text=self._extract_message_text(event.get("message")) or str(event.get("text", "")),
            color=str(event.get("color", "")),
            kind="message",
            badges=badges,
            is_first_message=bool(event.get("is_first_message", False) or event.get("first_time_chatter", False)),
            is_action=str(event.get("message_type", "")).strip().lower() == "action",
            user_id=str(event.get("chatter_user_id", "")),
        )
        self._emit_message(message)

    def _handle_chat_notification_event(self, event: dict[str, Any]) -> None:
        notice_type = str(event.get("notice_type", "")).replace("_", " ").strip() or "event"
        detail = self._extract_message_text(event.get("message")) or str(event.get("system_message", "")).strip()
        user_login = str(event.get("chatter_user_login", "")) or str(event.get("user_login", ""))
        display_name = str(event.get("chatter_user_name", "")) or str(event.get("user_name", "")) or user_login or "Twitch"
        self._emit_message(
            ChatMessage(
                id=str(event.get("message_id", "")).strip() or f"notice-{self._message_count}",
                timestamp=self._timestamp_from_event(event, "started_at", "created_at"),
                user_login=user_login,
                display_name=display_name,
                text=detail or notice_type.title(),
                kind="event",
                badges=self._format_badges(event.get("badges", [])),
                user_id=str(event.get("chatter_user_id", "") or event.get("user_id", "")),
            )
        )

    def _handle_chat_settings_update_event(self, event: dict[str, Any]) -> None:
        self._room_state = self._coerce_room_state(event)
        self.room_state_changed.emit(dict(self._room_state))
        followers_only = self._room_state.get("followers_only", -1)
        if isinstance(followers_only, int) and followers_only >= 0:
            follower_label = f"Followers-only {followers_only} min"
        else:
            follower_label = "Followers-only off"
        self._emit_activity(
            ChatActivity(
                id=f"room-{self._message_count}",
                timestamp=self._timestamp(),
                kind="settings",
                summary="Chat room settings updated.",
                detail=f"Slow mode {self._room_state['slow_mode']} sec. {follower_label}.",
            )
        )

    def _handle_chat_message_delete_event(self, event: dict[str, Any]) -> None:
        deleted_text = self._extract_message_text(event.get("message")) or "A chat message was deleted."
        self._emit_activity(
            ChatActivity(
                id=f"delete-{event.get('message_id', self._message_count)}",
                timestamp=self._timestamp_from_event(event, "sent_at", "created_at"),
                kind="moderation",
                summary="Message deleted.",
                detail=deleted_text,
                user_id=str(event.get("target_user_id", "") or event.get("user_id", "")),
                user_login=str(event.get("target_user_login", "") or event.get("user_login", "")),
                display_name=str(event.get("target_user_name", "") or event.get("user_name", "")),
            )
        )

    def _handle_automod_hold_event(self, event: dict[str, Any]) -> None:
        item = AutoModQueueItem(
            id=str(event.get("message_id", "")).strip() or f"automod-{self._message_count}",
            timestamp=self._timestamp_from_event(event, "held_at", "created_at"),
            user_id=str(event.get("user_id", "")),
            user_login=str(event.get("user_login", "")),
            display_name=str(event.get("user_name", "")) or str(event.get("user_login", "")),
            text=self._extract_message_text(event.get("message")) or str(event.get("message", "")),
            status="PENDING",
            reason="Held by AutoMod",
        )
        self._automod_queue[item.id] = item
        self.automod_queue_changed.emit(self._sorted_automod_queue())
        self._emit_activity(
            ChatActivity(
                id=f"automod-hold-{item.id}",
                timestamp=item.timestamp,
                kind="automod",
                summary=f"AutoMod held a message from {item.display_name or item.user_login}.",
                detail=item.text,
                user_id=item.user_id,
                user_login=item.user_login,
                display_name=item.display_name,
            )
        )

    def _handle_automod_update_event(self, event: dict[str, Any]) -> None:
        message_id = str(event.get("message_id", "")).strip()
        status = str(event.get("status", "")).strip().upper() or "UPDATED"
        item = self._automod_queue.get(message_id)
        if item is not None:
            item.status = status
            if status != "PENDING":
                self._automod_queue.pop(message_id, None)
        self.automod_queue_changed.emit(self._sorted_automod_queue())
        self._emit_activity(
            ChatActivity(
                id=f"automod-update-{message_id or self._message_count}",
                timestamp=self._timestamp_from_event(event, "updated_at", "created_at"),
                kind="automod",
                summary=f"AutoMod review updated: {status.title()}",
                detail=self._extract_message_text(event.get("message")),
                user_id=str(event.get("user_id", "")),
                user_login=str(event.get("user_login", "")),
                display_name=str(event.get("user_name", "")),
            )
        )

    def _handle_follow_event(self, event: dict[str, Any]) -> None:
        self._touch_viewer(
            user_id=str(event.get("user_id", "")),
            user_login=str(event.get("user_login", "")),
            display_name=str(event.get("user_name", "")),
            is_following=True,
        )
        self._emit_activity(
            ChatActivity(
                id=f"follow-{event.get('user_id', self._message_count)}",
                timestamp=self._timestamp_from_event(event, "followed_at"),
                kind="follow",
                summary=f"{event.get('user_name', event.get('user_login', 'A viewer'))} followed the channel.",
                user_id=str(event.get("user_id", "")),
                user_login=str(event.get("user_login", "")),
                display_name=str(event.get("user_name", "")),
            )
        )

    def _handle_subscribe_event(self, event: dict[str, Any]) -> None:
        self._touch_viewer(
            user_id=str(event.get("user_id", "")),
            user_login=str(event.get("user_login", "")),
            display_name=str(event.get("user_name", "")),
            roles=["subscriber"],
            is_subscribed=True,
        )
        self._emit_activity(
            ChatActivity(
                id=f"sub-{event.get('user_id', self._message_count)}",
                timestamp=self._timestamp_from_event(event, "started_at"),
                kind="subscription",
                summary=f"{event.get('user_name', event.get('user_login', 'A viewer'))} subscribed.",
                detail=f"Tier {event.get('tier', '1000')}",
                user_id=str(event.get("user_id", "")),
                user_login=str(event.get("user_login", "")),
                display_name=str(event.get("user_name", "")),
            )
        )

    def _handle_subscription_gift_event(self, event: dict[str, Any]) -> None:
        gifter = str(event.get("user_name", "") or event.get("user_login", "") or "An anonymous gifter")
        total = int(event.get("total", 0) or 0)
        self._emit_activity(
            ChatActivity(
                id=f"gift-{self._message_count}",
                timestamp=self._timestamp_from_event(event, "started_at"),
                kind="subscription",
                summary=f"{gifter} gifted {total} subscriptions.",
                detail=f"Tier {event.get('tier', '1000')}",
                user_id=str(event.get("user_id", "")),
                user_login=str(event.get("user_login", "")),
                display_name=str(event.get("user_name", "")),
            )
        )

    def _handle_subscription_message_event(self, event: dict[str, Any]) -> None:
        self._touch_viewer(
            user_id=str(event.get("user_id", "")),
            user_login=str(event.get("user_login", "")),
            display_name=str(event.get("user_name", "")),
            roles=["subscriber"],
            is_subscribed=True,
        )
        self._emit_activity(
            ChatActivity(
                id=f"resub-{event.get('user_id', self._message_count)}",
                timestamp=self._timestamp_from_event(event, "cumulative_months"),
                kind="subscription",
                summary=f"{event.get('user_name', event.get('user_login', 'A viewer'))} resubscribed.",
                detail=self._extract_message_text(event.get("message"))
                or f"{event.get('cumulative_months', 0)} total months",
                user_id=str(event.get("user_id", "")),
                user_login=str(event.get("user_login", "")),
                display_name=str(event.get("user_name", "")),
            )
        )

    def _handle_subscription_end_event(self, event: dict[str, Any]) -> None:
        self._emit_activity(
            ChatActivity(
                id=f"sub-end-{event.get('user_id', self._message_count)}",
                timestamp=self._timestamp(),
                kind="subscription",
                summary=f"{event.get('user_name', event.get('user_login', 'A viewer'))}'s subscription ended.",
                detail=f"Tier {event.get('tier', '1000')}",
                user_id=str(event.get("user_id", "")),
                user_login=str(event.get("user_login", "")),
                display_name=str(event.get("user_name", "")),
            )
        )

    def _handle_cheer_event(self, event: dict[str, Any]) -> None:
        self._touch_viewer(
            user_id=str(event.get("user_id", "")),
            user_login=str(event.get("user_login", "")),
            display_name=str(event.get("user_name", "")),
        )
        bits = int(event.get("bits", 0) or 0)
        self._emit_activity(
            ChatActivity(
                id=f"cheer-{self._message_count}",
                timestamp=self._timestamp_from_event(event, "started_at"),
                kind="cheer",
                summary=f"{event.get('user_name', event.get('user_login', 'A viewer'))} cheered {bits} bits.",
                detail=str(event.get("message", "")).strip(),
                user_id=str(event.get("user_id", "")),
                user_login=str(event.get("user_login", "")),
                display_name=str(event.get("user_name", "")),
            )
        )

    def _handle_raid_event(self, event: dict[str, Any]) -> None:
        raider = str(event.get("from_broadcaster_user_name", "") or event.get("from_broadcaster_user_login", "A broadcaster"))
        self._emit_activity(
            ChatActivity(
                id=f"raid-{self._message_count}",
                timestamp=self._timestamp(),
                kind="raid",
                summary=f"Incoming raid from {raider}.",
                detail=f"{event.get('viewers', 0)} viewers",
                user_id=str(event.get("from_broadcaster_user_id", "")),
                user_login=str(event.get("from_broadcaster_user_login", "")),
                display_name=raider,
            )
        )

    def _handle_redeem_event(self, event: dict[str, Any]) -> None:
        reward = dict(event.get("reward", {}))
        title = str(reward.get("title", "")).strip() or "Channel point redemption"
        self._touch_viewer(
            user_id=str(event.get("user_id", "")),
            user_login=str(event.get("user_login", "")),
            display_name=str(event.get("user_name", "")),
        )
        user_input = str(event.get("user_input", "")).strip()
        self._emit_activity(
            ChatActivity(
                id=f"redeem-{event.get('id', self._message_count)}",
                timestamp=self._timestamp_from_event(event, "redeemed_at"),
                kind="redeem",
                summary=f"{event.get('user_name', event.get('user_login', 'A viewer'))} redeemed '{title}'.",
                detail=user_input,
                user_id=str(event.get("user_id", "")),
                user_login=str(event.get("user_login", "")),
                display_name=str(event.get("user_name", "")),
            )
        )

    def _handle_poll_event(self, event: dict[str, Any]) -> None:
        title = str(event.get("title", "")).strip() or "Poll updated"
        choice_titles = [
            str(choice.get("title", "")).strip()
            for choice in event.get("choices", [])
            if isinstance(choice, dict) and str(choice.get("title", "")).strip()
        ]
        detail = ", ".join(choice_titles[:5])
        status = str(event.get("status", "")).replace("_", " ").strip().title()
        self._emit_activity(
            ChatActivity(
                id=f"poll-{event.get('id', self._message_count)}",
                timestamp=self._timestamp_from_event(event, "started_at", "ended_at"),
                kind="poll",
                summary=f"Poll {status.lower() or 'updated'}: {title}",
                detail=detail,
            )
        )

    def _handle_prediction_event(self, event: dict[str, Any]) -> None:
        title = str(event.get("title", "")).strip() or "Prediction updated"
        outcome_titles = [
            str(outcome.get("title", "")).strip()
            for outcome in event.get("outcomes", [])
            if isinstance(outcome, dict) and str(outcome.get("title", "")).strip()
        ]
        detail = ", ".join(outcome_titles[:10])
        status = str(event.get("status", "")).replace("_", " ").strip().title()
        self._emit_activity(
            ChatActivity(
                id=f"prediction-{event.get('id', self._message_count)}",
                timestamp=self._timestamp_from_event(event, "started_at", "ended_at", "locked_at"),
                kind="prediction",
                summary=f"Prediction {status.lower() or 'updated'}: {title}",
                detail=detail,
            )
        )

    def _handle_shoutout_create_event(self, event: dict[str, Any]) -> None:
        target = str(event.get("to_broadcaster_user_name", "") or event.get("to_broadcaster_user_login", "another channel"))
        self._emit_activity(
            ChatActivity(
                id=f"shoutout-create-{self._message_count}",
                timestamp=self._timestamp_from_event(event, "started_at"),
                kind="shoutout",
                summary=f"Shoutout sent to {target}.",
                detail=f"Cooldown ends at {event.get('cooldown_ends_at', 'unknown')}",
            )
        )

    def _handle_shoutout_receive_event(self, event: dict[str, Any]) -> None:
        source = str(
            event.get("from_broadcaster_user_name", "")
            or event.get("from_broadcaster_user_login", "")
            or "another channel"
        )
        self._emit_activity(
            ChatActivity(
                id=f"shoutout-receive-{self._message_count}",
                timestamp=self._timestamp_from_event(event, "started_at"),
                kind="shoutout",
                summary=f"Received a shoutout from {source}.",
            )
        )

    async def _manage_automod_message(self, message_id: str, action: str, success_message: str) -> None:
        cleaned = message_id.strip()
        if not cleaned:
            self.connection_changed.emit(False, "Select a held AutoMod message first.")
            return

        if self._simulation_enabled:
            self._automod_queue.pop(cleaned, None)
            self.automod_queue_changed.emit(self._sorted_automod_queue())
            self.connection_changed.emit(True, success_message)
            return

        self._require_live_connection()
        await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/moderation/automod/message",
            body={"user_id": self._moderator_id, "msg_id": cleaned, "action": action},
        )
        self._automod_queue.pop(cleaned, None)
        self.automod_queue_changed.emit(self._sorted_automod_queue())
        self.connection_changed.emit(True, success_message)

    async def _run_simulator(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            roll = self._message_count % 7
            if roll in {0, 1, 2, 3}:
                index = self._message_count % len(self._sample_users)
                login, name, color, roles = self._sample_users[index]
                text = random.choice(self._sample_messages)
                self._emit_message(
                    ChatMessage(
                        id=f"sim-{self._message_count}",
                        timestamp=self._timestamp(),
                        user_login=login,
                        display_name=name,
                        text=text,
                        color=color,
                        badges=",".join(f"{role}/1" for role in roles),
                        is_first_message=(self._message_count % 6 == 0),
                        user_id=f"sim-user-{index + 1}",
                    )
                )
            elif roll == 4:
                self._handle_follow_event(
                    {
                        "user_id": "sim-user-9",
                        "user_login": "freshfollow",
                        "user_name": "FreshFollow",
                        "followed_at": datetime.now().isoformat(),
                    }
                )
            elif roll == 5:
                self._handle_redeem_event(
                    {
                        "id": f"sim-redeem-{self._message_count}",
                        "user_id": "sim-user-2",
                        "user_login": "modmaven",
                        "user_name": "ModMaven",
                        "redeemed_at": datetime.now().isoformat(),
                        "reward": {"title": "Hydrate"},
                        "user_input": "Take a sip!",
                    }
                )
            else:
                self._handle_automod_hold_event(
                    {
                        "message_id": f"sim-automod-{self._message_count}",
                        "user_id": "sim-user-4",
                        "user_login": "nightscene",
                        "user_name": "NightScene",
                        "message": {"text": "Potentially spicy message"},
                    }
                )

    def _emit_message(self, message: ChatMessage) -> None:
        self._message_count += 1
        self._messages_by_id[message.id] = message
        self._touch_viewer(
            user_id=message.user_id,
            user_login=message.user_login,
            display_name=message.display_name or message.user_login,
            color=message.color,
            badges=message.badges,
            last_message=message.text,
            roles=self._roles_from_badges(message.badges),
        )
        self.message_received.emit(message)

    def _emit_activity(self, activity: ChatActivity) -> None:
        self._message_count += 1
        self.activity_received.emit(activity)

    def _emit_subscription_summary(self) -> None:
        self.subscription_summary_changed.emit(self.subscription_summary())

    def _touch_viewer(
        self,
        *,
        user_id: str,
        user_login: str,
        display_name: str,
        color: str = "",
        badges: str = "",
        roles: list[str] | None = None,
        last_message: str = "",
        is_following: bool | None = None,
        is_subscribed: bool | None = None,
    ) -> None:
        key = user_id.strip() or user_login.strip().lower()
        if not key:
            return
        card = self._viewer_cards.get(key)
        if card is None:
            card = ViewerCard(
                user_id=user_id.strip(),
                user_login=user_login.strip().lower(),
                display_name=display_name.strip() or user_login.strip() or user_id.strip(),
            )
        card.color = color or card.color
        card.badges = badges or card.badges
        merged_roles = {role for role in card.roles if role}
        for role in roles or []:
            if role:
                merged_roles.add(role)
        for role in self._roles_from_badges(card.badges):
            if role:
                merged_roles.add(role)
        card.roles = sorted(merged_roles)
        if display_name.strip():
            card.display_name = display_name.strip()
        if user_id.strip():
            card.user_id = user_id.strip()
        if user_login.strip():
            card.user_login = user_login.strip().lower()
        if last_message.strip():
            card.last_message = last_message.strip()
            card.message_count += 1
        if is_following is not None:
            card.is_following = is_following or card.is_following
        if is_subscribed is not None:
            card.is_subscribed = is_subscribed or card.is_subscribed
        now = datetime.now()
        card.last_seen = now.strftime("%H:%M:%S")
        card.last_activity_epoch = now.timestamp()
        self._viewer_cards[key] = card
        self.viewer_cards_changed.emit(self._sorted_viewer_cards())

    def _sorted_viewer_cards(self) -> list[ViewerCard]:
        def role_rank(card: ViewerCard) -> int:
            roles = set(card.roles)
            if "broadcaster" in roles:
                return 0
            if "moderator" in roles:
                return 1
            if "vip" in roles:
                return 2
            if "subscriber" in roles:
                return 3
            return 4

        return sorted(
            self._viewer_cards.values(),
            key=lambda card: (role_rank(card), -card.last_activity_epoch, card.display_name.lower()),
        )

    def _sorted_automod_queue(self) -> list[AutoModQueueItem]:
        return sorted(self._automod_queue.values(), key=lambda item: item.timestamp, reverse=True)

    def _clear_state(self, emit_signal: bool = True) -> None:
        self._messages_by_id.clear()
        self._viewer_cards.clear()
        self._automod_queue.clear()
        if emit_signal:
            self.history_cleared.emit()
            self.viewer_cards_changed.emit([])
            self.automod_queue_changed.emit([])

    @staticmethod
    def _format_badges(raw_badges: Any) -> str:
        if isinstance(raw_badges, str):
            return raw_badges
        badges: list[str] = []
        for entry in raw_badges if isinstance(raw_badges, list) else []:
            if not isinstance(entry, dict):
                continue
            set_id = str(entry.get("set_id", "")).strip()
            badge_id = str(entry.get("id", "")).strip() or "1"
            if set_id:
                badges.append(f"{set_id}/{badge_id}")
        return ",".join(badges)

    @staticmethod
    def _roles_from_badges(badges: str) -> list[str]:
        roles: list[str] = []
        for fragment in badges.split(","):
            set_id = fragment.split("/", 1)[0].strip().lower()
            if set_id in {"broadcaster", "moderator", "vip", "subscriber"}:
                roles.append(set_id)
        return roles

    @staticmethod
    def _extract_message_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, dict):
            return ""
        text = str(value.get("text", "")).strip()
        if text:
            return text
        fragments = value.get("fragments", [])
        if not isinstance(fragments, list):
            return ""
        return "".join(
            str(fragment.get("text", ""))
            for fragment in fragments
            if isinstance(fragment, dict) and str(fragment.get("text", ""))
        ).strip()

    @staticmethod
    def _coerce_room_state(payload: dict[str, Any]) -> dict[str, object]:
        followers_only = -1
        if bool(payload.get("follower_mode", False)):
            followers_only = int(payload.get("follower_mode_duration_minutes", 0) or 0)
        return {
            "channel": str(
                payload.get("broadcaster_user_login", "")
                or payload.get("channel", "")
                or ""
            ).strip().lstrip("#"),
            "slow_mode": int(payload.get("slow_mode_wait_time", 0) or 0),
            "followers_only": followers_only,
            "subs_only": bool(payload.get("subscriber_mode", False)),
            "emote_only": bool(payload.get("emote_mode", False)),
            "unique_chat": bool(payload.get("unique_chat_mode", False)),
            "non_moderator_chat_delay": bool(payload.get("non_moderator_chat_delay", False)),
            "non_moderator_chat_delay_duration": int(payload.get("non_moderator_chat_delay_duration", 0) or 0),
        }

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%H:%M:%S")

    @staticmethod
    def _timestamp_from_event(event: dict[str, Any], *keys: str) -> str:
        for key in keys:
            raw = str(event.get(key, "")).strip()
            if not raw:
                continue
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed.astimezone().strftime("%H:%M:%S")
        return TwitchChatService._timestamp()

    @staticmethod
    def _first_data_entry(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data", [])
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                return first
        return {}

    def _request_json_sync(
        self,
        method: str,
        path: str,
        *,
        client_id: str | None = None,
        access_token: str | None = None,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_client_id = (client_id or self._client_id).strip()
        resolved_access_token = self._normalize_token(access_token or self._access_token)
        if not resolved_client_id:
            raise TwitchApiError("Enter a Twitch client ID first.")
        if not resolved_access_token:
            raise TwitchApiError("Enter a Twitch user access token first.")

        url = f"{self.BASE_URL}{path}"
        if query:
            url = f"{url}?{parse.urlencode(query, doseq=True)}"

        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")

        req = request.Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {resolved_access_token}",
                "Client-Id": resolved_client_id,
                "Content-Type": "application/json",
                "User-Agent": "StreamControl/0.1",
            },
        )

        try:
            with request.urlopen(req, timeout=10, context=tls_context()) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raw = exc.read()
            message = self._error_message(raw) or f"Twitch API request failed with HTTP {exc.code}."
            raise TwitchApiError(message) from exc
        except error.URLError as exc:
            raise TwitchApiError(f"Could not reach Twitch: {describe_tls_error(exc.reason)}") from exc

        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TwitchApiError("Twitch returned an unreadable response.") from exc

    @staticmethod
    def _error_message(raw: bytes) -> str:
        if not raw:
            return ""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", errors="ignore").strip()
        return str(payload.get("message", "")).strip()
