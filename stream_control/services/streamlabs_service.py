from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import QObject, Signal
from pyslobs import ConnectionConfig, ScenesService, SlobsConnection
from pyslobs.slobs.streamingservice import StreamingService

from stream_control.core.models import StreamlabsSettings


class StreamlabsService(QObject):
    connection_changed = Signal(bool, str)
    scenes_changed = Signal(object)
    stream_status_changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._connection: SlobsConnection | None = None
        self._scenes_service: ScenesService | None = None
        self._streaming_service: StreamingService | None = None
        self._background_task: asyncio.Task[None] | None = None
        self._scene_cache: list[dict[str, str]] = []
        self._simulation_enabled = False
        self._simulated_streaming = False
        self._simulated_current_scene = "slobs-starting"
        self._simulated_scenes = [
            {"id": "slobs-starting", "name": "Starting Soon"},
            {"id": "slobs-live", "name": "Live"},
            {"id": "slobs-brb", "name": "Be Right Back"},
            {"id": "slobs-chatting", "name": "Just Chatting"},
        ]

    @property
    def is_simulated(self) -> bool:
        return self._simulation_enabled

    @property
    def is_connected(self) -> bool:
        return self._simulation_enabled or self._connection is not None

    async def connect(self, settings: StreamlabsSettings) -> None:
        self.disconnect(silent=True)
        try:
            config = ConnectionConfig(
                token=settings.token,
                domain=settings.host,
                port=settings.port,
            )
            self._connection = SlobsConnection(config)
            self._background_task = asyncio.create_task(self._connection.background_processing())
            self._background_task.add_done_callback(self._on_background_task_done)
            self._scenes_service = ScenesService(self._connection)
            self._streaming_service = StreamingService(self._connection)
            self.connection_changed.emit(
                True,
                f"Connected to Streamlabs Desktop at {settings.host}:{settings.port}.",
            )
            await self.refresh_scenes()
            await self.refresh_stream_status()
        except Exception as exc:
            self.disconnect()
            self.scenes_changed.emit({"scenes": [], "current": None})
            self.stream_status_changed.emit(self._disconnected_stream_status())
            self.connection_changed.emit(False, f"Streamlabs Desktop connection failed: {exc}")

    async def connect_simulated(self) -> None:
        self.disconnect(silent=True)
        self._simulation_enabled = True
        self._simulated_streaming = False
        self._simulated_current_scene = self._simulated_scenes[0]["id"]
        self.connection_changed.emit(True, "Connected to the built-in Streamlabs simulator.")
        await self.refresh_scenes()
        await self.refresh_stream_status()

    async def refresh_scenes(self) -> None:
        if self._simulation_enabled:
            payload = {
                "scenes": list(self._simulated_scenes),
                "current": self._simulated_current_scene,
            }
            self._scene_cache = list(payload["scenes"])
            self.scenes_changed.emit(payload)
            return
        if self._scenes_service is None:
            self.connection_changed.emit(False, "Streamlabs Desktop is not connected.")
            return

        try:
            scenes = await self._scenes_service.get_scenes()
            active = await self._scenes_service.active_scene()
            payload = {
                "scenes": [{"id": scene.id, "name": scene.name} for scene in scenes],
                "current": getattr(active, "id", None),
            }
            self._scene_cache = list(payload["scenes"])
            self.scenes_changed.emit(payload)
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not refresh Streamlabs scenes: {exc}")

    async def refresh_stream_status(self) -> dict[str, object]:
        if self._simulation_enabled:
            payload = self._simulated_stream_status()
            self.stream_status_changed.emit(payload)
            return payload
        if self._streaming_service is None:
            payload = self._disconnected_stream_status()
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, "Streamlabs Desktop is not connected.")
            return payload
        try:
            model = await self._streaming_service.get_model()
            payload = self._normalize_stream_status_payload(model)
            self.stream_status_changed.emit(payload)
            return payload
        except Exception as exc:
            payload = self._disconnected_stream_status(
                f"Could not refresh Streamlabs Desktop stream status: {exc}"
            )
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

    async def start_streaming(self) -> dict[str, object]:
        if self._simulation_enabled:
            self._simulated_streaming = True
            self.connection_changed.emit(True, "Streamlabs simulator is now live.")
            return await self.refresh_stream_status()
        return await self._toggle_streaming(True)

    async def stop_streaming(self) -> dict[str, object]:
        if self._simulation_enabled:
            self._simulated_streaming = False
            self.connection_changed.emit(True, "Streamlabs simulator stopped streaming.")
            return await self.refresh_stream_status()
        return await self._toggle_streaming(False)

    async def set_active_scene(self, scene_id: str) -> None:
        if self._simulation_enabled:
            scene_exists = any(scene["id"] == scene_id for scene in self._simulated_scenes)
            if not scene_exists:
                self.connection_changed.emit(False, f"Unknown simulated Streamlabs scene: {scene_id}")
                return
            self._simulated_current_scene = scene_id
            scene_name = next(scene["name"] for scene in self._simulated_scenes if scene["id"] == scene_id)
            self.connection_changed.emit(True, f"Streamlabs simulator switched to '{scene_name}'.")
            await self.refresh_scenes()
            return
        if self._scenes_service is None:
            return
        try:
            await self._scenes_service.make_scene_active(scene_id)
            await self.refresh_scenes()
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not switch Streamlabs scene: {exc}")

    def disconnect(self, silent: bool = False) -> None:
        if self._simulation_enabled:
            self._simulation_enabled = False
            self._simulated_streaming = False
            self._scene_cache = []
            self.scenes_changed.emit({"scenes": [], "current": None})
            self.stream_status_changed.emit(self._disconnected_stream_status())
            if not silent:
                self.connection_changed.emit(False, "Streamlabs simulator disconnected.")
            return
        if (
            self._background_task is None
            and self._connection is None
            and self._scenes_service is None
            and self._streaming_service is None
        ):
            return
        if self._background_task is not None:
            self._background_task.cancel()
            self._background_task = None
        if self._connection is not None:
            self._connection.close()
        self._connection = None
        self._scenes_service = None
        self._streaming_service = None
        self._scene_cache = []
        if not silent:
            self.connection_changed.emit(False, "Streamlabs Desktop disconnected.")
        self.scenes_changed.emit({"scenes": [], "current": None})
        self.stream_status_changed.emit(self._disconnected_stream_status())

    def _on_background_task_done(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            self.connection_changed.emit(False, f"Streamlabs Desktop listener stopped: {exception}")

    async def _toggle_streaming(self, target_live: bool) -> dict[str, object]:
        if self._streaming_service is None:
            payload = self._disconnected_stream_status("Connect Streamlabs Desktop before changing live state.")
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

        current = await self.refresh_stream_status()
        if current["is_live"] == target_live:
            message = (
                "Streamlabs Desktop is already live."
                if target_live
                else "Streamlabs Desktop is already offline."
            )
            self.connection_changed.emit(True, message)
            return current

        try:
            await asyncio.wait_for(self._streaming_service.toggle_streaming(), timeout=5)
        except asyncio.TimeoutError:
            # Streamlabs may complete the toggle without resolving the RPC call.
            pass
        except Exception as exc:
            payload = self._disconnected_stream_status(
                f"Could not change Streamlabs Desktop live state: {exc}"
            )
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

        await asyncio.sleep(1.0)
        payload = await self.refresh_stream_status()
        if payload["is_live"] == target_live:
            message = (
                "Streamlabs Desktop is now live."
                if target_live
                else "Streamlabs Desktop stream stopped."
            )
            self.connection_changed.emit(True, message)
        else:
            self.connection_changed.emit(False, "Streamlabs Desktop did not confirm the requested live state yet.")
        return payload

    def _simulated_stream_status(self) -> dict[str, object]:
        return {
            "service": "Streamlabs Desktop",
            "mode": "simulator",
            "connected": True,
            "is_live": self._simulated_streaming,
            "status": "Live" if self._simulated_streaming else "Offline",
            "detail": (
                "Streamlabs simulator is live."
                if self._simulated_streaming
                else "Streamlabs simulator is standing by."
            ),
        }

    @staticmethod
    def _disconnected_stream_status(detail: str = "Streamlabs Desktop is disconnected.") -> dict[str, object]:
        return {
            "service": "Streamlabs Desktop",
            "mode": "disconnected",
            "connected": False,
            "is_live": False,
            "status": "Disconnected",
            "detail": detail,
        }

    @staticmethod
    def _normalize_stream_status_payload(model: Any) -> dict[str, object]:
        raw_status = str(getattr(model, "streaming_status", "")).strip()
        normalized = raw_status.lower()
        is_live = normalized in {"live", "starting", "reconnecting", "streaming", "active"}
        detail = raw_status.replace("_", " ").title() if raw_status else ""
        if not detail:
            detail = "Streamlabs Desktop is live." if is_live else "Streamlabs Desktop is idle."
        return {
            "service": "Streamlabs Desktop",
            "mode": "real",
            "connected": True,
            "is_live": is_live,
            "status": "Live" if is_live else "Offline",
            "detail": detail,
        }
