from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject

from stream_control.core.credentials import (
    BROADCAST_TWITCH_ACCESS_TOKEN,
    CHAT_TWITCH_ACCESS_TOKEN,
    STREAMLABS_TOKEN,
    CredentialStore,
)
from stream_control.core.models import AppConfig, ObsSettings, OverlaySettings, StreamlabsSettings
from stream_control.core.platform import is_macos, macos_hotkey_permissions
from stream_control.services.overlay_server import OverlayServerStatus

if TYPE_CHECKING:
    from stream_control.services.obs_service import ObsService
    from stream_control.services.streamlabs_service import StreamlabsService
    from stream_control.services.twitch_chat_service import TwitchChatService


@dataclass(slots=True)
class SetupCheck:
    key: str
    title: str
    status: str
    summary: str
    detail: str
    action: str = ""


@dataclass(slots=True)
class SetupSnapshot:
    headline: str
    summary: str
    next_steps: list[str]
    checks: list[SetupCheck]
    safe_test_active: bool
    can_start_safe_test: bool

    def check_map(self) -> dict[str, SetupCheck]:
        return {check.key: check for check in self.checks}


class SetupDiagnosticsService(QObject):
    async def build_snapshot(
        self,
        app_config: AppConfig,
        credential_store: CredentialStore,
        obs_service: ObsService | None,
        streamlabs_service: StreamlabsService | None,
        chat_service: TwitchChatService | None,
        overlay_status: OverlayServerStatus | None = None,
    ) -> SetupSnapshot:
        integrations_raw = app_config.plugin_settings("integrations")
        broadcast_raw = app_config.plugin_settings("broadcast")
        chat_raw = app_config.plugin_settings("chat")
        music_raw = app_config.plugin_settings("music")

        obs_settings = ObsSettings(**integrations_raw.get("obs", {}))
        streamlabs_settings = StreamlabsSettings(**integrations_raw.get("streamlabs", {}))
        overlay_settings = OverlaySettings(**music_raw.get("overlay", {}))

        output_target = str(broadcast_raw.get("output_target", "auto") or "auto")
        twitch_credentials = dict(broadcast_raw.get("twitch", {}))
        twitch_client_id = str(twitch_credentials.get("client_id", "")).strip()
        twitch_access_token_present = bool(
            str(twitch_credentials.get("access_token", "")).strip()
            or credential_store.has_secret(BROADCAST_TWITCH_ACCESS_TOKEN)
        )
        stream_title = str(broadcast_raw.get("stream_title", "")).strip()
        category_name = str(broadcast_raw.get("category_name", "")).strip()

        chat_settings = dict(chat_raw.get("twitch", {}))
        chat_channel = str(chat_settings.get("channel", "")).strip()
        chat_client_id = str(chat_settings.get("client_id", "")).strip()
        chat_token_present = bool(
            str(chat_settings.get("access_token", "") or chat_settings.get("oauth_token", "")).strip()
            or credential_store.has_secret(CHAT_TWITCH_ACCESS_TOKEN)
        )
        streamlabs_token_present = bool(
            streamlabs_settings.token.strip() or credential_store.has_secret(STREAMLABS_TOKEN)
        )

        safe_test_active = any(
            service is not None and service.is_simulated
            for service in (obs_service, streamlabs_service, chat_service)
        )
        real_connection_active = any(
            service is not None and service.is_connected and not service.is_simulated
            for service in (obs_service, streamlabs_service, chat_service)
        )

        obs_probe_task = None
        obs_process_task = None
        if obs_service is None or not obs_service.is_connected:
            obs_probe_task = asyncio.create_task(self._probe_endpoint(obs_settings.host, obs_settings.port))
            obs_process_task = asyncio.create_task(self._detect_process(["obs64.exe", "obs"]))

        streamlabs_probe_task = None
        streamlabs_process_task = None
        if streamlabs_service is None or not streamlabs_service.is_connected:
            streamlabs_probe_task = asyncio.create_task(
                self._probe_endpoint(streamlabs_settings.host, streamlabs_settings.port)
            )
            streamlabs_process_task = asyncio.create_task(
                self._detect_process(["Streamlabs Desktop.exe", "Streamlabs OBS.exe", "streamlabs"])
            )

        overlay_probe_task = None
        if overlay_settings.enabled:
            overlay_probe_task = asyncio.create_task(
                self._probe_endpoint(overlay_settings.host, overlay_settings.port)
            )

        obs_probe = await obs_probe_task if obs_probe_task is not None else (False, "")
        obs_process = await obs_process_task if obs_process_task is not None else None
        streamlabs_probe = await streamlabs_probe_task if streamlabs_probe_task is not None else (False, "")
        streamlabs_process = await streamlabs_process_task if streamlabs_process_task is not None else None
        overlay_probe = await overlay_probe_task if overlay_probe_task is not None else (False, "")

        checks = [
            self._build_output_check(
                output_target,
                obs_service,
                streamlabs_service,
                obs_probe[0],
                streamlabs_probe[0],
            ),
            self._build_obs_check(
                obs_service,
                obs_settings,
                obs_probe,
                obs_process,
                output_target,
                bool(streamlabs_service is not None and streamlabs_service.is_connected),
            ),
            self._build_streamlabs_check(
                streamlabs_service,
                streamlabs_settings,
                streamlabs_token_present,
                streamlabs_probe,
                streamlabs_process,
                output_target,
                bool(obs_service is not None and obs_service.is_connected),
            ),
            self._build_broadcast_check(twitch_client_id, twitch_access_token_present, stream_title, category_name),
            self._build_chat_check(chat_service, chat_channel, chat_client_id, chat_token_present),
        ]
        if is_macos():
            checks.append(self._build_macos_permissions_check())
        checks.append(self._build_overlay_check(overlay_settings, overlay_probe, overlay_status))

        output_check = next(check for check in checks if check.key == "output")
        broadcast_check = next(check for check in checks if check.key == "broadcast")

        next_steps = [check.action for check in checks if check.action and check.status == "attention"]
        if not next_steps and not safe_test_active:
            next_steps.append("Use Start Safe Test Session any time you want to rehearse without touching a live setup.")
        if not next_steps:
            next_steps.append("Your core setup looks healthy. Move into the feature plugins and start building presets and routines.")

        live_ready = output_check.status == "ready"
        test_ready = output_check.status in {"ready", "testing"} or safe_test_active
        twitch_ready = broadcast_check.status == "ready"

        if live_ready and twitch_ready:
            headline = "Ready For A Live Session"
            summary = "A real output controller is connected, and Twitch metadata syncing is configured."
        elif test_ready:
            headline = "Ready For Safe Testing"
            summary = "The app can rehearse against a simulator or a connected output path without forcing you live."
        else:
            headline = "Setup Needs Attention"
            summary = "The core output path still needs a little setup before this feels dependable."

        if broadcast_check.status != "ready":
            summary += " Twitch metadata sync is still optional until you want title and category control."

        return SetupSnapshot(
            headline=headline,
            summary=summary,
            next_steps=next_steps,
            checks=checks,
            safe_test_active=safe_test_active,
            can_start_safe_test=not safe_test_active and not real_connection_active,
        )

    def _build_output_check(
        self,
        output_target: str,
        obs_service: ObsService | None,
        streamlabs_service: StreamlabsService | None,
        obs_reachable: bool,
        streamlabs_reachable: bool,
    ) -> SetupCheck:
        candidates = [
            ("obs", "OBS Studio", obs_service),
            ("streamlabs", "Streamlabs Desktop", streamlabs_service),
        ]
        if output_target != "auto":
            candidates = [item for item in candidates if item[0] == output_target]

        real_connected = next(
            (
                (label, service)
                for _, label, service in candidates
                if service is not None and service.is_connected and not service.is_simulated
            ),
            None,
        )
        if real_connected is not None:
            label, _service = real_connected
            return SetupCheck(
                key="output",
                title="Live Output Path",
                status="ready",
                summary=f"{label} is connected in real mode.",
                detail="Go Live and Stop Streaming can now target a real output app.",
            )

        simulated = next(
            (
                (label, service)
                for _, label, service in candidates
                if service is not None and service.is_connected and service.is_simulated
            ),
            None,
        )
        if simulated is not None:
            label, _service = simulated
            return SetupCheck(
                key="output",
                title="Live Output Path",
                status="testing",
                summary=f"{label} simulator is active.",
                detail="You can rehearse scenes and stream controls without connecting a real output app.",
                action="Stop the simulator and connect a real output app on Integrations when you want to validate the live path.",
            )

        unavailable = [label for _, label, service in candidates if service is None]
        if candidates and all(service is None for _, _, service in candidates):
            joined = ", ".join(unavailable)
            return SetupCheck(
                key="output",
                title="Live Output Path",
                status="attention",
                summary=f"{joined} control is unavailable in this app session.",
                detail="The related integration plugin did not finish loading, so Stream Control cannot open a live output connection for that app right now.",
                action="Relaunch after reinstalling the app or its missing dependencies, then reconnect your output app on Integrations.",
            )

        if obs_reachable or streamlabs_reachable:
            return SetupCheck(
                key="output",
                title="Live Output Path",
                status="attention",
                summary="An output app is reachable, but Stream Control is not connected yet.",
                detail="The network path is open. The next step is simply connecting the app from the Integrations page.",
                action="Open Integrations and click Connect for the output app you want to drive.",
            )

        return SetupCheck(
            key="output",
            title="Live Output Path",
            status="attention",
            summary="No real output app is connected yet.",
            detail="You can still rehearse with simulators, but Go Live cannot target a real app until OBS Studio or Streamlabs Desktop is connected.",
            action="Connect OBS Studio or Streamlabs Desktop on Integrations, or start a safe test session here first.",
        )

    def _build_obs_check(
        self,
        obs_service: ObsService | None,
        settings: ObsSettings,
        probe: tuple[bool, str],
        process_running: bool | None,
        output_target: str,
        other_output_connected: bool,
    ) -> SetupCheck:
        relevant = output_target == "obs" or settings.auto_connect or (
            output_target == "auto" and not other_output_connected
        )
        if obs_service is None:
            return SetupCheck(
                key="obs",
                title="OBS Studio",
                status="attention" if relevant else "optional",
                summary="OBS control is unavailable in this app session.",
                detail="The OBS integration plugin did not finish loading, so Stream Control cannot open an OBS WebSocket connection right now.",
                action="Relaunch after reinstalling the app or its missing dependencies if you want OBS to be your live controller.",
            )
        if obs_service.is_simulated:
            return SetupCheck(
                key="obs",
                title="OBS Studio",
                status="testing",
                summary="OBS simulator is active.",
                detail="Scene and stream controls are being rehearsed without a real OBS session.",
            )
        if obs_service.is_connected:
            return SetupCheck(
                key="obs",
                title="OBS Studio",
                status="ready",
                summary=f"Connected to {settings.host}:{settings.port}.",
                detail="OBS WebSocket control is active and ready for scene switching and stream control.",
            )
        if probe[0]:
            return SetupCheck(
                key="obs",
                title="OBS Studio",
                status="attention" if relevant else "optional",
                summary=f"OBS WebSocket is reachable at {settings.host}:{settings.port}.",
                detail="The port is open, which usually means the next step is just connecting from the Integrations page.",
                action="Open Integrations and click Connect under OBS Studio.",
            )
        if process_running:
            return SetupCheck(
                key="obs",
                title="OBS Studio",
                status="attention" if relevant else "optional",
                summary="OBS appears to be running, but its WebSocket is not reachable.",
                detail=f"Nothing answered on {settings.host}:{settings.port}, which often means OBS needs its WebSocket settings applied.",
                action="In OBS, open Tools > obs-websocket Settings, confirm the port, then click Apply and OK.",
            )
        return SetupCheck(
            key="obs",
            title="OBS Studio",
            status="attention" if relevant else "optional",
            summary="OBS is not connected.",
            detail="That is fine if Streamlabs Desktop is your live output. If OBS is your target, it still needs a reachable WebSocket.",
            action="Start OBS Studio and enable obs-websocket if you want OBS to be your live controller.",
        )

    def _build_streamlabs_check(
        self,
        streamlabs_service: StreamlabsService | None,
        settings: StreamlabsSettings,
        token_present: bool,
        probe: tuple[bool, str],
        process_running: bool | None,
        output_target: str,
        other_output_connected: bool,
    ) -> SetupCheck:
        relevant = output_target == "streamlabs" or settings.auto_connect or token_present or (
            output_target == "auto" and not other_output_connected
        )
        if streamlabs_service is None:
            return SetupCheck(
                key="streamlabs",
                title="Streamlabs Desktop",
                status="attention" if relevant else "optional",
                summary="Streamlabs Desktop control is unavailable in this app session.",
                detail="The Streamlabs integration plugin did not finish loading, so Stream Control cannot open a Streamlabs Desktop remote-control session right now.",
                action="Relaunch after reinstalling the app or its missing dependencies if you want Streamlabs Desktop control here.",
            )
        if streamlabs_service.is_simulated:
            return SetupCheck(
                key="streamlabs",
                title="Streamlabs Desktop",
                status="testing",
                summary="Streamlabs simulator is active.",
                detail="You can rehearse scene and stream controls without touching a real Streamlabs Desktop session.",
            )
        if streamlabs_service.is_connected:
            return SetupCheck(
                key="streamlabs",
                title="Streamlabs Desktop",
                status="ready",
                summary=f"Connected to {settings.host}:{settings.port}.",
                detail="Remote control is active and ready for scene switching and stream state checks.",
            )
        if not token_present:
            return SetupCheck(
                key="streamlabs",
                title="Streamlabs Desktop",
                status="attention" if output_target == "streamlabs" else "optional",
                summary="Remote token is missing.",
                detail="Streamlabs Desktop requires the Remote Control token before the app can authenticate.",
                action="In Streamlabs Desktop, open Settings > Remote Control > Show Details, then paste the token into Integrations.",
            )
        if probe[0]:
            return SetupCheck(
                key="streamlabs",
                title="Streamlabs Desktop",
                status="attention" if relevant else "optional",
                summary=f"Streamlabs Desktop is reachable at {settings.host}:{settings.port}.",
                detail="The socket is listening, so the next step is connecting with the saved token.",
                action="Open Integrations and click Connect under Streamlabs Desktop.",
            )
        if process_running:
            return SetupCheck(
                key="streamlabs",
                title="Streamlabs Desktop",
                status="attention" if relevant else "optional",
                summary="Streamlabs Desktop appears to be running, but Remote Control is not reachable.",
                detail=f"Nothing answered on {settings.host}:{settings.port}, which usually means Remote Control has not been fully enabled yet.",
                action="In Streamlabs Desktop, open Settings > Remote Control, show the details panel, and confirm the token and port.",
            )
        return SetupCheck(
            key="streamlabs",
            title="Streamlabs Desktop",
            status="attention" if relevant else "optional",
            summary="Streamlabs Desktop is not connected.",
            detail="That is fine if OBS Studio is your main output path. If you want Streamlabs control, it still needs Remote Control enabled.",
            action="Start Streamlabs Desktop and enable Remote Control if you want to drive it from Stream Control.",
        )

    def _build_broadcast_check(
        self,
        client_id: str,
        has_access_token: bool,
        stream_title: str,
        category_name: str,
    ) -> SetupCheck:
        if not client_id and not has_access_token:
            return SetupCheck(
                key="broadcast",
                title="Twitch Broadcast Sync",
                status="optional",
                summary="Twitch broadcast metadata is not configured yet.",
                detail="You can still test scene switching and output control without this. Add it when you want title and category syncing.",
                action="Open Broadcast and add a Twitch client ID plus a user access token with channel:manage:broadcast.",
            )
        if not client_id or not has_access_token:
            return SetupCheck(
                key="broadcast",
                title="Twitch Broadcast Sync",
                status="attention",
                summary="Broadcast credentials are only partially configured.",
                detail="The Broadcast plugin needs both a Twitch client ID and a user access token before it can update title or category.",
                action="Finish the Twitch credentials on the Broadcast page.",
            )
        if not stream_title and not category_name:
            return SetupCheck(
                key="broadcast",
                title="Twitch Broadcast Sync",
                status="ready",
                summary="Twitch credentials are ready.",
                detail="Metadata sync can authenticate now. Add saved titles and categories whenever you want reusable live presets.",
            )
        return SetupCheck(
            key="broadcast",
            title="Twitch Broadcast Sync",
            status="ready",
            summary="Twitch credentials and stream metadata are ready.",
            detail="Broadcast Control can sync title and category before you go live or while you are live.",
        )

    def _build_chat_check(
        self,
        chat_service: TwitchChatService | None,
        channel: str,
        client_id: str,
        has_access_token: bool,
    ) -> SetupCheck:
        if chat_service is not None and chat_service.is_simulated:
            return SetupCheck(
                key="chat",
                title="Chat Management",
                status="testing",
                summary="Chat simulator is active.",
                detail="You can rehearse feed filtering, moderation tools, and engagement actions without joining a real Twitch chat room.",
            )
        if chat_service is not None and chat_service.is_connected:
            return SetupCheck(
                key="chat",
                title="Chat Management",
                status="ready",
                summary="Connected to Twitch chat.",
                detail="The in-app feed, moderation tools, and engagement actions are connected through Twitch EventSub and Helix.",
            )
        if not channel and not client_id and not has_access_token:
            return SetupCheck(
                key="chat",
                title="Chat Management",
                status="optional",
                summary="Chat is not configured yet.",
                detail="That is okay if you are focusing on output control first. Add chat credentials when you want the EventSub feed, viewer cards, moderation, or engagement tools.",
                action="Open Chat and add the Twitch channel, client ID, and user access token.",
            )
        if not channel or not client_id or not has_access_token:
            return SetupCheck(
                key="chat",
                title="Chat Management",
                status="attention",
                summary="Chat settings are only partially configured.",
                detail="The Chat plugin needs a channel, client ID, and user access token to connect cleanly.",
                action="Finish the channel, client ID, and token fields on the Chat page.",
            )
        return SetupCheck(
            key="chat",
            title="Chat Management",
            status="attention",
            summary="Chat credentials are saved, but chat is not connected.",
            detail="The setup is close. The remaining step is connecting from the Chat page or starting the chat simulator here.",
            action="Open Chat and click Connect, or start a safe test session from Setup Center.",
        )

    def _build_overlay_check(
        self,
        settings: OverlaySettings,
        probe: tuple[bool, str],
        runtime_status: OverlayServerStatus | None,
    ) -> SetupCheck:
        if not settings.enabled:
            return SetupCheck(
                key="overlay",
                title="Music Overlay",
                status="optional",
                summary="The now-playing overlay server is disabled.",
                detail="That is fine if you do not want a browser source for music yet.",
                action="Open Music and enable or configure the overlay when you want on-stream track titles.",
            )
        if runtime_status is not None and runtime_status.running:
            return SetupCheck(
                key="overlay",
                title="Music Overlay",
                status="ready",
                summary=f"Overlay server is reachable at {runtime_status.url}.",
                detail="OBS or Streamlabs can use this browser source right now for now-playing information.",
            )
        if runtime_status is not None and runtime_status.last_error:
            return SetupCheck(
                key="overlay",
                title="Music Overlay",
                status="attention",
                summary="The overlay server failed to start.",
                detail=f"Music tried to start the overlay server but got this error: {runtime_status.last_error}",
                action="Open Music to review the overlay status, then free the port or change the overlay host/port if another app is using it.",
            )
        if probe[0]:
            return SetupCheck(
                key="overlay",
                title="Music Overlay",
                status="ready",
                summary=f"Overlay server is reachable at {settings.now_playing_url}.",
                detail="OBS or Streamlabs can use this browser source right now for now-playing information.",
            )
        return SetupCheck(
            key="overlay",
            title="Music Overlay",
            status="attention",
            summary="The overlay server is enabled but not reachable yet.",
            detail=f"No response came back from {settings.now_playing_url}, so the Music plugin may not have started its overlay server or the port may be blocked.",
            action="Open Music once after launch and confirm the overlay URL is present, or change the overlay port if another app is using it.",
        )

    def _build_macos_permissions_check(self) -> SetupCheck:
        permissions = macos_hotkey_permissions()
        if permissions is None or permissions.is_ready:
            return SetupCheck(
                key="permissions",
                title="macOS Permissions",
                status="ready",
                summary="macOS privacy permissions are ready.",
                detail="Global hotkeys should be able to listen for shortcuts from this Mac session.",
            )

        missing = " and ".join(permissions.missing_items)
        return SetupCheck(
            key="permissions",
            title="macOS Permissions",
            status="attention",
            summary=f"Global hotkeys still need {missing} access.",
            detail=(
                "macOS is currently blocking background shortcut listening. Until that is granted, the Hotkeys page "
                "can still save bindings, but the Mac will not fire them globally."
            ),
            action=(
                "Open System Settings > Privacy & Security and allow Stream Control or the Python interpreter under "
                "Accessibility and Input Monitoring, then relaunch the app."
            ),
        )

    async def _probe_endpoint(self, host: str, port: int) -> tuple[bool, str]:
        if not host.strip():
            return False, "Host is blank."
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host.strip(), int(port)), timeout=0.75)
        except Exception as exc:
            return False, str(exc)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        return True, f"{host}:{port} is reachable."

    async def _detect_process(self, needles: list[str]) -> bool | None:
        return await asyncio.to_thread(self._detect_process_sync, needles)

    @staticmethod
    def _detect_process_sync(needles: list[str]) -> bool | None:
        normalized = [needle.lower() for needle in needles if needle]
        if not normalized:
            return None
        try:
            if sys.platform.startswith("win"):
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                haystack = result.stdout.lower()
                return any(needle in haystack for needle in normalized)
            result = subprocess.run(
                ["ps", "ax", "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
            )
            haystack = result.stdout.lower()
            return any(needle in haystack for needle in normalized)
        except Exception:
            return None
