from __future__ import annotations

import asyncio
import base64
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from obsws_python import ReqClient

from stream_control.core.models import ObsSettings

_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF2kAAAAASUVORK5CYII="
)


class ObsService(QObject):
    connection_changed = Signal(bool, str)
    scenes_changed = Signal(object)
    stream_status_changed = Signal(object)
    production_state_changed = Signal(object)
    source_items_changed = Signal(object)
    audio_inputs_changed = Signal(object)
    scene_transition_override_changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client: ReqClient | None = None
        self._simulation_enabled = False
        self._audio_restore_levels: dict[str, float] = {}
        self._load_default_simulation_state()

    @property
    def is_simulated(self) -> bool:
        return self._simulation_enabled

    @property
    def is_connected(self) -> bool:
        return self._simulation_enabled or self._client is not None

    async def connect(self, settings: ObsSettings) -> None:
        self.disconnect(silent=True)
        try:
            client = await asyncio.to_thread(
                ReqClient,
                host=settings.host,
                port=settings.port,
                password=settings.password or None,
                timeout=3,
            )
            self._client = client
            self.connection_changed.emit(True, f"Connected to OBS at {settings.host}:{settings.port}.")
            await self.refresh_scenes()
            await self.refresh_stream_status()
            await self.refresh_production_state()
            await self.refresh_source_items()
            await self.refresh_audio_inputs()
        except Exception as exc:
            self._client = None
            self._emit_disconnected_payloads()
            self.connection_changed.emit(False, f"OBS connection failed: {exc}")

    async def connect_simulated(self) -> None:
        self.disconnect(silent=True)
        self._simulation_enabled = True
        self._audio_restore_levels.clear()
        self._load_default_simulation_state()
        self.connection_changed.emit(True, "Connected to the built-in OBS simulator.")
        await self.refresh_scenes()
        await self.refresh_stream_status()
        await self.refresh_production_state()
        await self.refresh_source_items()
        await self.refresh_audio_inputs()

    async def refresh_scenes(self) -> dict[str, object]:
        if self._simulation_enabled:
            payload = {
                "scenes": list(self._simulated_scenes),
                "current": self._simulated_program_scene,
            }
            self.scenes_changed.emit(payload)
            return payload

        if self._client is None:
            payload = {"scenes": [], "current": None}
            self.scenes_changed.emit(payload)
            self.connection_changed.emit(False, "OBS is not connected.")
            return payload

        try:
            response = await asyncio.to_thread(self._client.get_scene_list)
            payload = self._normalize_scene_payload(response)
            self.scenes_changed.emit(payload)
            return payload
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not refresh OBS scenes: {exc}")
            payload = {"scenes": [], "current": None}
            self.scenes_changed.emit(payload)
            return payload

    async def refresh_stream_status(self) -> dict[str, object]:
        if self._simulation_enabled:
            payload = self._simulated_stream_status()
            self.stream_status_changed.emit(payload)
            return payload

        if self._client is None:
            payload = self._disconnected_stream_status()
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, "OBS is not connected.")
            return payload

        try:
            response = await asyncio.to_thread(self._client.get_stream_status)
            payload = self._normalize_stream_status_payload(response)
            self.stream_status_changed.emit(payload)
            return payload
        except Exception as exc:
            payload = self._disconnected_stream_status(f"Could not refresh OBS stream status: {exc}")
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

    async def refresh_production_state(self) -> dict[str, object]:
        if self._simulation_enabled:
            payload = self._simulated_production_state()
            self.production_state_changed.emit(payload)
            return payload

        if self._client is None:
            payload = self._disconnected_production_state()
            self.production_state_changed.emit(payload)
            self.connection_changed.emit(False, "OBS is not connected.")
            return payload

        try:
            payload = await asyncio.to_thread(self._fetch_production_state_from_obs)
            self.production_state_changed.emit(payload)
            return payload
        except Exception as exc:
            payload = self._disconnected_production_state(f"Could not refresh OBS production state: {exc}")
            self.production_state_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

    async def refresh_source_items(self, scene_name: str | None = None) -> dict[str, object]:
        if self._simulation_enabled:
            target_scene = scene_name or self._simulated_program_scene
            payload = self._simulated_source_items_payload(target_scene)
            self.source_items_changed.emit(payload)
            return payload

        if self._client is None:
            payload = {"scene_name": scene_name or "", "items": [], "connected": False}
            self.source_items_changed.emit(payload)
            self.connection_changed.emit(False, "OBS is not connected.")
            return payload

        try:
            payload = await asyncio.to_thread(self._fetch_source_items_from_obs, scene_name)
            self.source_items_changed.emit(payload)
            return payload
        except Exception as exc:
            payload = {
                "scene_name": scene_name or "",
                "items": [],
                "connected": False,
                "detail": f"Could not load OBS sources: {exc}",
            }
            self.source_items_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

    async def refresh_audio_inputs(self) -> dict[str, object]:
        if self._simulation_enabled:
            payload = {
                "connected": True,
                "inputs": [dict(item) for item in self._simulated_audio_inputs],
            }
            self.audio_inputs_changed.emit(payload)
            return payload

        if self._client is None:
            payload = {"connected": False, "inputs": []}
            self.audio_inputs_changed.emit(payload)
            self.connection_changed.emit(False, "OBS is not connected.")
            return payload

        try:
            payload = await asyncio.to_thread(self._fetch_audio_inputs_from_obs)
            self.audio_inputs_changed.emit(payload)
            return payload
        except Exception as exc:
            payload = {"connected": False, "inputs": [], "detail": f"Could not load OBS audio inputs: {exc}"}
            self.audio_inputs_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

    async def refresh_scene_transition_override(self, scene_name: str) -> dict[str, object]:
        if not scene_name:
            payload = {"scene_name": "", "has_override": False, "transition_name": "", "duration": 0}
            self.scene_transition_override_changed.emit(payload)
            return payload

        if self._simulation_enabled:
            payload = self._simulated_transition_override_payload(scene_name)
            self.scene_transition_override_changed.emit(payload)
            return payload

        if self._client is None:
            payload = {
                "scene_name": scene_name,
                "has_override": False,
                "transition_name": "",
                "duration": 0,
                "detail": "OBS is not connected.",
            }
            self.scene_transition_override_changed.emit(payload)
            return payload

        try:
            payload = await asyncio.to_thread(self._fetch_scene_transition_override_from_obs, scene_name)
            self.scene_transition_override_changed.emit(payload)
            return payload
        except Exception as exc:
            payload = {
                "scene_name": scene_name,
                "has_override": False,
                "transition_name": "",
                "duration": 0,
                "detail": f"Could not load OBS scene transition preset: {exc}",
            }
            self.scene_transition_override_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

    async def start_streaming(self) -> dict[str, object]:
        if self._simulation_enabled:
            self._simulated_streaming = True
            return await self.refresh_stream_status()

        if self._client is None:
            payload = self._disconnected_stream_status("Connect OBS Studio before going live.")
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

        try:
            await asyncio.to_thread(self._client.start_stream)
            await asyncio.sleep(0.75)
            payload = await self.refresh_stream_status()
            if payload["is_live"]:
                self.connection_changed.emit(True, "OBS is now live.")
            else:
                self.connection_changed.emit(False, "OBS did not report a live stream yet.")
            return payload
        except Exception as exc:
            payload = self._disconnected_stream_status(f"Could not start OBS streaming: {exc}")
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

    async def stop_streaming(self) -> dict[str, object]:
        if self._simulation_enabled:
            self._simulated_streaming = False
            return await self.refresh_stream_status()

        if self._client is None:
            payload = self._disconnected_stream_status("Connect OBS Studio before stopping a stream.")
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

        try:
            await asyncio.to_thread(self._client.stop_stream)
            await asyncio.sleep(0.5)
            payload = await self.refresh_stream_status()
            if not payload["is_live"]:
                self.connection_changed.emit(True, "OBS stream stopped.")
            else:
                self.connection_changed.emit(False, "OBS still reports an active stream.")
            return payload
        except Exception as exc:
            payload = self._disconnected_stream_status(f"Could not stop OBS streaming: {exc}")
            self.stream_status_changed.emit(payload)
            self.connection_changed.emit(False, str(payload["detail"]))
            return payload

    async def set_current_scene(self, scene_name: str) -> None:
        await self.set_program_scene(scene_name)

    async def set_program_scene(self, scene_name: str) -> dict[str, object]:
        if self._simulation_enabled:
            if not self._simulated_scene_exists(scene_name):
                return {"ok": False, "detail": f"Unknown simulated OBS scene: {scene_name}"}
            self._simulated_program_scene = scene_name
            await self.refresh_scenes()
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Program scene changed to '{scene_name}'."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_current_program_scene, scene_name)
            await self.refresh_scenes()
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Program scene changed to '{scene_name}'."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not switch OBS scene: {exc}")
            return {"ok": False, "detail": f"Could not switch OBS scene: {exc}"}

    async def set_preview_scene(self, scene_name: str) -> dict[str, object]:
        if self._simulation_enabled:
            if not self._simulated_scene_exists(scene_name):
                return {"ok": False, "detail": f"Unknown simulated OBS scene: {scene_name}"}
            self._simulated_preview_scene = scene_name
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Preview scene loaded: '{scene_name}'."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_current_preview_scene, scene_name)
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Preview scene loaded: '{scene_name}'."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not load the preview scene: {exc}")
            return {"ok": False, "detail": f"Could not load the preview scene: {exc}"}

    async def trigger_studio_transition(self) -> dict[str, object]:
        if self._simulation_enabled:
            if not self._simulated_studio_mode_enabled:
                return {"ok": False, "detail": "Enable studio mode before taking preview live."}
            previous_program = self._simulated_program_scene
            self._simulated_program_scene = self._simulated_preview_scene
            self._simulated_preview_scene = previous_program
            await self.refresh_scenes()
            await self.refresh_production_state()
            return {"ok": True, "detail": "Preview scene transitioned to program."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.trigger_studio_mode_transition)
            await asyncio.sleep(0.2)
            await self.refresh_scenes()
            await self.refresh_production_state()
            return {"ok": True, "detail": "Preview scene transitioned to program."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not transition preview to program: {exc}")
            return {"ok": False, "detail": f"Could not transition preview to program: {exc}"}

    async def set_studio_mode_enabled(self, enabled: bool) -> dict[str, object]:
        if self._simulation_enabled:
            self._simulated_studio_mode_enabled = enabled
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Studio mode {'enabled' if enabled else 'disabled'}."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_studio_mode_enabled, enabled)
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Studio mode {'enabled' if enabled else 'disabled'}."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not change studio mode: {exc}")
            return {"ok": False, "detail": f"Could not change studio mode: {exc}"}

    async def set_current_transition(self, transition_name: str) -> dict[str, object]:
        if self._simulation_enabled:
            if transition_name not in self._simulated_transitions:
                return {"ok": False, "detail": f"Unknown simulated transition: {transition_name}"}
            self._simulated_current_transition = transition_name
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Transition changed to '{transition_name}'."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_current_scene_transition, transition_name)
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Transition changed to '{transition_name}'."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not change the transition: {exc}")
            return {"ok": False, "detail": f"Could not change the transition: {exc}"}

    async def set_transition_duration(self, duration_ms: int) -> dict[str, object]:
        duration_ms = max(50, min(20_000, int(duration_ms)))
        if self._simulation_enabled:
            self._simulated_transition_duration = duration_ms
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Transition duration set to {duration_ms} ms."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_current_scene_transition_duration, duration_ms)
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Transition duration set to {duration_ms} ms."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not change transition duration: {exc}")
            return {"ok": False, "detail": f"Could not change transition duration: {exc}"}

    async def set_scene_transition_override(
        self,
        scene_name: str,
        transition_name: str | None,
        duration_ms: int | None,
    ) -> dict[str, object]:
        if not scene_name:
            return {"ok": False, "detail": "Choose a scene before saving a transition preset."}

        if self._simulation_enabled:
            if transition_name:
                self._simulated_transition_overrides[scene_name] = {
                    "transition_name": transition_name,
                    "duration": int(duration_ms or self._simulated_transition_duration),
                }
            else:
                self._simulated_transition_overrides.pop(scene_name, None)
            payload = await self.refresh_scene_transition_override(scene_name)
            return {
                "ok": True,
                "detail": (
                    f"Saved the transition preset for '{scene_name}'."
                    if payload["has_override"]
                    else f"Cleared the transition preset for '{scene_name}'."
                ),
            }

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(
                self._client.set_scene_scene_transition_override,
                scene_name,
                transition_name,
                duration_ms,
            )
            payload = await self.refresh_scene_transition_override(scene_name)
            return {
                "ok": True,
                "detail": (
                    f"Saved the transition preset for '{scene_name}'."
                    if payload["has_override"]
                    else f"Cleared the transition preset for '{scene_name}'."
                ),
            }
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not save the scene transition preset: {exc}")
            return {"ok": False, "detail": f"Could not save the scene transition preset: {exc}"}

    async def set_source_enabled(self, scene_name: str, item_id: int, enabled: bool) -> dict[str, object]:
        if self._simulation_enabled:
            for item in self._simulated_scene_items.get(scene_name, []):
                if int(item["id"]) == int(item_id):
                    item["enabled"] = enabled
                    await self.refresh_source_items(scene_name)
                    return {
                        "ok": True,
                        "detail": f"{item['name']} is now {'visible' if enabled else 'hidden'}.",
                    }
            return {"ok": False, "detail": "Could not find that simulated source item."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_scene_item_enabled, scene_name, item_id, enabled)
            await self.refresh_source_items(scene_name)
            return {"ok": True, "detail": "Source visibility updated."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not update source visibility: {exc}")
            return {"ok": False, "detail": f"Could not update source visibility: {exc}"}

    async def set_input_mute(self, input_name: str, muted: bool) -> dict[str, object]:
        if self._simulation_enabled:
            audio_input = self._simulated_audio_input(input_name)
            if audio_input is None:
                return {"ok": False, "detail": f"Unknown simulated OBS audio input: {input_name}"}
            audio_input["muted"] = muted
            await self.refresh_audio_inputs()
            return {"ok": True, "detail": f"{input_name} is now {'muted' if muted else 'unmuted'}."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_input_mute, input_name, muted)
            await self.refresh_audio_inputs()
            return {"ok": True, "detail": f"{input_name} is now {'muted' if muted else 'unmuted'}."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not change OBS audio mute state: {exc}")
            return {"ok": False, "detail": f"Could not change OBS audio mute state: {exc}"}

    async def set_input_volume_db(self, input_name: str, volume_db: float) -> dict[str, object]:
        if self._simulation_enabled:
            audio_input = self._simulated_audio_input(input_name)
            if audio_input is None:
                return {"ok": False, "detail": f"Unknown simulated OBS audio input: {input_name}"}
            audio_input["volume_db"] = float(volume_db)
            await self.refresh_audio_inputs()
            return {"ok": True, "detail": f"{input_name} level set to {float(volume_db):.1f} dB."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_input_volume, input_name, None, float(volume_db))
            await self.refresh_audio_inputs()
            return {"ok": True, "detail": f"{input_name} level set to {float(volume_db):.1f} dB."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not change OBS audio level: {exc}")
            return {"ok": False, "detail": f"Could not change OBS audio level: {exc}"}

    async def duck_audio_input(self, input_name: str, target_db: float = -18.0) -> dict[str, object]:
        current_level = await self._current_input_volume_db(input_name)
        if current_level is None:
            return {"ok": False, "detail": f"Could not read the current level for {input_name}."}
        self._audio_restore_levels[input_name] = current_level
        return await self.set_input_volume_db(input_name, target_db)

    async def restore_audio_input(self, input_name: str) -> dict[str, object]:
        previous_level = self._audio_restore_levels.get(input_name)
        if previous_level is None:
            return {"ok": False, "detail": f"No saved level is waiting for {input_name}."}
        result = await self.set_input_volume_db(input_name, previous_level)
        if result["ok"]:
            self._audio_restore_levels.pop(input_name, None)
        return result

    async def fade_audio_input(self, input_name: str, target_db: float, duration_ms: int = 600) -> dict[str, object]:
        start_level = await self._current_input_volume_db(input_name)
        if start_level is None:
            return {"ok": False, "detail": f"Could not read the current level for {input_name}."}
        self._audio_restore_levels.setdefault(input_name, start_level)

        steps = max(3, min(24, int(duration_ms / 75) if duration_ms else 6))
        for step in range(1, steps + 1):
            blend = step / steps
            level = start_level + ((float(target_db) - start_level) * blend)
            result = await self._set_input_volume_db_without_refresh(input_name, level)
            if not result["ok"]:
                return result
            await asyncio.sleep(max(duration_ms, 50) / steps / 1000)

        await self.refresh_audio_inputs()
        return {"ok": True, "detail": f"{input_name} faded to {float(target_db):.1f} dB."}

    async def start_replay_buffer(self) -> dict[str, object]:
        if self._simulation_enabled:
            self._simulated_replay_buffer_active = True
            await self.refresh_production_state()
            return {"ok": True, "detail": "Replay buffer started in the OBS simulator."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.start_replay_buffer)
            await asyncio.sleep(0.2)
            await self.refresh_production_state()
            return {"ok": True, "detail": "Replay buffer started."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not start the OBS replay buffer: {exc}")
            return {"ok": False, "detail": f"Could not start the OBS replay buffer: {exc}"}

    async def stop_replay_buffer(self) -> dict[str, object]:
        if self._simulation_enabled:
            self._simulated_replay_buffer_active = False
            await self.refresh_production_state()
            return {"ok": True, "detail": "Replay buffer stopped in the OBS simulator."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.stop_replay_buffer)
            await asyncio.sleep(0.2)
            await self.refresh_production_state()
            return {"ok": True, "detail": "Replay buffer stopped."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not stop the OBS replay buffer: {exc}")
            return {"ok": False, "detail": f"Could not stop the OBS replay buffer: {exc}"}

    async def save_replay_buffer(self) -> dict[str, object]:
        if self._simulation_enabled:
            captures_dir = Path.cwd() / "simulated_obs_replays"
            captures_dir.mkdir(parents=True, exist_ok=True)
            file_path = captures_dir / f"replay-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
            file_path.write_text("Simulated OBS replay save.\n", encoding="utf-8")
            self._simulated_last_replay_path = str(file_path)
            await self.refresh_production_state()
            return {"ok": True, "detail": "Simulated replay saved.", "path": str(file_path)}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.save_replay_buffer)
            await asyncio.sleep(0.4)
            payload = await self.refresh_production_state()
            return {
                "ok": True,
                "detail": "Replay buffer saved.",
                "path": str(payload.get("last_replay_path", "")),
            }
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not save the OBS replay buffer: {exc}")
            return {"ok": False, "detail": f"Could not save the OBS replay buffer: {exc}"}

    async def create_clip_marker(self, marker_name: str = "") -> dict[str, object]:
        label = marker_name.strip() or f"Marker {datetime.now().strftime('%H:%M:%S')}"
        if self._simulation_enabled:
            self._simulated_markers.append(label)
            return {"ok": True, "detail": f"Saved simulated clip marker '{label}'."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.create_record_chapter, label)
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Saved clip marker '{label}'."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not create the OBS clip marker: {exc}")
            return {"ok": False, "detail": f"Could not create the OBS clip marker: {exc}"}

    async def set_current_scene_collection(self, name: str) -> dict[str, object]:
        if self._simulation_enabled:
            if name not in self._simulated_scene_collections:
                return {"ok": False, "detail": f"Unknown simulated scene collection: {name}"}
            self._simulated_current_scene_collection = name
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Switched to scene collection '{name}'."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_current_scene_collection, name)
            await self.refresh_scenes()
            await self.refresh_production_state()
            await self.refresh_source_items()
            return {"ok": True, "detail": f"Switched to scene collection '{name}'."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not switch the OBS scene collection: {exc}")
            return {"ok": False, "detail": f"Could not switch the OBS scene collection: {exc}"}

    async def set_current_profile(self, name: str) -> dict[str, object]:
        if self._simulation_enabled:
            if name not in self._simulated_profiles:
                return {"ok": False, "detail": f"Unknown simulated OBS profile: {name}"}
            self._simulated_current_profile = name
            await self.refresh_production_state()
            return {"ok": True, "detail": f"Switched to profile '{name}'."}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_current_profile, name)
            await self.refresh_production_state()
            await self.refresh_audio_inputs()
            return {"ok": True, "detail": f"Switched to profile '{name}'."}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not switch the OBS profile: {exc}")
            return {"ok": False, "detail": f"Could not switch the OBS profile: {exc}"}

    async def save_source_snapshot(
        self,
        source_name: str,
        target_dir: Path,
        width: int = 1280,
        height: int = 720,
        image_format: str = "png",
    ) -> dict[str, object]:
        if not source_name:
            return {"ok": False, "detail": "Choose a source or scene before taking a snapshot."}

        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / self._snapshot_filename(source_name, image_format)

        if self._simulation_enabled:
            file_path.write_bytes(_PLACEHOLDER_PNG)
            return {"ok": True, "detail": f"Simulated snapshot saved for '{source_name}'.", "path": str(file_path)}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(
                self._client.save_source_screenshot,
                source_name,
                image_format,
                str(file_path),
                int(width),
                int(height),
                -1,
            )
            return {"ok": True, "detail": f"Snapshot saved for '{source_name}'.", "path": str(file_path)}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not capture an OBS snapshot: {exc}")
            return {"ok": False, "detail": f"Could not capture an OBS snapshot: {exc}"}

    def disconnect(self, silent: bool = False) -> None:
        if self._simulation_enabled:
            self._simulation_enabled = False
            self._audio_restore_levels.clear()
            self._emit_disconnected_payloads()
            if not silent:
                self.connection_changed.emit(False, "OBS simulator disconnected.")
            return

        if self._client is None:
            return

        try:
            self._client.disconnect()
        finally:
            self._client = None
            self._audio_restore_levels.clear()
            self._emit_disconnected_payloads()
            if not silent:
                self.connection_changed.emit(False, "OBS disconnected.")

    def _load_default_simulation_state(self) -> None:
        self._simulated_streaming = False
        self._simulated_studio_mode_enabled = True
        self._simulated_scenes = [
            {"id": "Starting Soon", "name": "Starting Soon"},
            {"id": "Live", "name": "Live"},
            {"id": "Be Right Back", "name": "Be Right Back"},
            {"id": "Just Chatting", "name": "Just Chatting"},
        ]
        self._simulated_program_scene = "Starting Soon"
        self._simulated_preview_scene = "Live"
        self._simulated_transitions = ["Fade", "Cut", "Swipe"]
        self._simulated_current_transition = "Fade"
        self._simulated_transition_duration = 300
        self._simulated_transition_overrides = {
            "Be Right Back": {"transition_name": "Swipe", "duration": 450}
        }
        self._simulated_scene_items = {
            "Starting Soon": [
                {"id": 101, "name": "Countdown Loop", "enabled": True, "is_group": False, "locked": False},
                {"id": 102, "name": "Chat Overlay", "enabled": True, "is_group": False, "locked": False},
                {"id": 103, "name": "Sponsor Lower Third", "enabled": False, "is_group": False, "locked": False},
            ],
            "Live": [
                {"id": 201, "name": "Gameplay Capture", "enabled": True, "is_group": False, "locked": False},
                {"id": 202, "name": "Face Cam", "enabled": True, "is_group": False, "locked": False},
                {"id": 203, "name": "Recent Follows", "enabled": True, "is_group": False, "locked": False},
            ],
            "Be Right Back": [
                {"id": 301, "name": "BRB Background", "enabled": True, "is_group": False, "locked": False},
                {"id": 302, "name": "Music Card", "enabled": True, "is_group": False, "locked": False},
            ],
            "Just Chatting": [
                {"id": 401, "name": "Camera 1", "enabled": True, "is_group": False, "locked": False},
                {"id": 402, "name": "Chat Overlay", "enabled": True, "is_group": False, "locked": False},
                {"id": 403, "name": "Topic Banner", "enabled": True, "is_group": False, "locked": False},
            ],
        }
        self._simulated_audio_inputs = [
            {
                "name": "Desktop Audio",
                "kind": "wasapi_output_capture",
                "muted": False,
                "volume_db": -10.0,
                "volume_mul": 0.316,
                "is_special": True,
            },
            {
                "name": "Mic / Aux",
                "kind": "wasapi_input_capture",
                "muted": False,
                "volume_db": -4.0,
                "volume_mul": 0.631,
                "is_special": True,
            },
            {
                "name": "Music Bus",
                "kind": "ffmpeg_source",
                "muted": False,
                "volume_db": -16.0,
                "volume_mul": 0.158,
                "is_special": False,
            },
            {
                "name": "Alerts",
                "kind": "browser_source",
                "muted": False,
                "volume_db": -14.0,
                "volume_mul": 0.2,
                "is_special": False,
            },
        ]
        self._simulated_replay_buffer_active = False
        self._simulated_last_replay_path = ""
        self._simulated_scene_collections = ["Default", "Podcast", "Ranked Session"]
        self._simulated_current_scene_collection = "Default"
        self._simulated_profiles = ["Creator", "Travel", "Podcast"]
        self._simulated_current_profile = "Creator"
        self._simulated_record_active = True
        self._simulated_markers: list[str] = []

    def _emit_disconnected_payloads(self) -> None:
        self.scenes_changed.emit({"scenes": [], "current": None})
        self.stream_status_changed.emit(self._disconnected_stream_status())
        self.production_state_changed.emit(self._disconnected_production_state())
        self.source_items_changed.emit({"scene_name": "", "items": [], "connected": False})
        self.audio_inputs_changed.emit({"connected": False, "inputs": []})
        self.scene_transition_override_changed.emit(
            {"scene_name": "", "has_override": False, "transition_name": "", "duration": 0}
        )

    def _simulated_stream_status(self) -> dict[str, object]:
        return {
            "service": "OBS Studio",
            "mode": "simulator",
            "connected": True,
            "is_live": self._simulated_streaming,
            "status": "Live" if self._simulated_streaming else "Offline",
            "detail": (
                "OBS simulator is live."
                if self._simulated_streaming
                else "OBS simulator is standing by."
            ),
        }

    def _simulated_production_state(self) -> dict[str, object]:
        return {
            "service": "OBS Studio",
            "mode": "simulator",
            "connected": True,
            "status": "Connected",
            "detail": "OBS simulator production tools are ready.",
            "studio_mode_enabled": self._simulated_studio_mode_enabled,
            "program_scene": self._simulated_program_scene,
            "preview_scene": self._simulated_preview_scene,
            "transitions": list(self._simulated_transitions),
            "current_transition": self._simulated_current_transition,
            "transition_duration": self._simulated_transition_duration,
            "replay_buffer_active": self._simulated_replay_buffer_active,
            "replay_buffer_available": True,
            "replay_buffer_detail": "Replay buffer controls are available in the simulator.",
            "last_replay_path": self._simulated_last_replay_path,
            "scene_collections": list(self._simulated_scene_collections),
            "current_scene_collection": self._simulated_current_scene_collection,
            "profiles": list(self._simulated_profiles),
            "current_profile": self._simulated_current_profile,
            "record_active": self._simulated_record_active,
        }

    def _simulated_source_items_payload(self, scene_name: str) -> dict[str, object]:
        items = [dict(item) for item in self._simulated_scene_items.get(scene_name, [])]
        return {
            "connected": True,
            "scene_name": scene_name,
            "items": items,
        }

    def _simulated_transition_override_payload(self, scene_name: str) -> dict[str, object]:
        override = self._simulated_transition_overrides.get(scene_name)
        if override is None:
            return {"scene_name": scene_name, "has_override": False, "transition_name": "", "duration": 0}
        return {
            "scene_name": scene_name,
            "has_override": True,
            "transition_name": str(override["transition_name"]),
            "duration": int(override["duration"]),
        }

    def _simulated_scene_exists(self, scene_name: str) -> bool:
        return any(scene["name"] == scene_name for scene in self._simulated_scenes)

    def _simulated_audio_input(self, input_name: str) -> dict[str, object] | None:
        for audio_input in self._simulated_audio_inputs:
            if audio_input["name"] == input_name:
                return audio_input
        return None

    def _fetch_production_state_from_obs(self) -> dict[str, object]:
        assert self._client is not None

        scenes_response = self._client.get_scene_list()
        scenes_payload = self._normalize_scene_payload(scenes_response)
        program_response = self._client.get_current_program_scene()
        program_scene = str(
            self._response_value(
                program_response,
                "current_program_scene_name",
                "currentProgramSceneName",
                "scene_name",
                "sceneName",
                default=scenes_payload.get("current") or "",
            )
            or ""
        )

        studio_mode_response = self._client.get_studio_mode_enabled()
        studio_mode_enabled = bool(
            self._response_value(
                studio_mode_response,
                "studio_mode_enabled",
                "studioModeEnabled",
                default=False,
            )
        )

        preview_scene = ""
        if studio_mode_enabled:
            try:
                preview_response = self._client.get_current_preview_scene()
                preview_scene = str(
                    self._response_value(
                        preview_response,
                        "current_preview_scene_name",
                        "currentPreviewSceneName",
                        "scene_name",
                        "sceneName",
                        default="",
                    )
                    or ""
                )
            except Exception:
                preview_scene = ""

        transition_list_response = self._client.get_scene_transition_list()
        current_transition_response = self._client.get_current_scene_transition()
        transitions = self._normalize_named_items(
            self._response_value(transition_list_response, "transitions", default=[]),
            "transition_name",
            "transitionName",
            "name",
        )
        current_transition = str(
            self._response_value(
                current_transition_response,
                "current_scene_transition_name",
                "currentSceneTransitionName",
                "transition_name",
                "transitionName",
                default="",
            )
            or ""
        )
        transition_duration = int(
            self._response_value(
                current_transition_response,
                "current_scene_transition_duration",
                "currentSceneTransitionDuration",
                "transition_duration",
                "transitionDuration",
                default=300,
            )
            or 300
        )

        replay_buffer_available = True
        replay_buffer_detail = "Replay buffer controls are available."
        last_replay_path = ""
        try:
            replay_status_response = self._client.get_replay_buffer_status()
            replay_buffer_active = bool(
                self._response_value(
                    replay_status_response,
                    "output_active",
                    "outputActive",
                    default=False,
                )
            )

            try:
                replay_path_response = self._client.get_last_replay_buffer_replay()
                last_replay_path = str(
                    self._response_value(
                        replay_path_response,
                        "saved_replay_path",
                        "savedReplayPath",
                        "saved_replay_file",
                        "savedReplayFile",
                        default="",
                    )
                    or ""
                )
            except Exception:
                last_replay_path = ""
                replay_buffer_detail = "Replay buffer is active, but OBS has not reported a saved replay yet."
        except Exception as exc:
            replay_buffer_active = False
            replay_buffer_available = False
            replay_buffer_detail = self._replay_buffer_unavailable_detail(exc)

        scene_collection_response = self._client.get_scene_collection_list()
        scene_collections = self._normalize_named_items(
            self._response_value(scene_collection_response, "scene_collections", "sceneCollections", default=[]),
            "scene_name",
            "sceneName",
            "name",
        )
        current_scene_collection = str(
            self._response_value(
                scene_collection_response,
                "current_scene_collection_name",
                "currentSceneCollectionName",
                default="",
            )
            or ""
        )

        profile_response = self._client.get_profile_list()
        profiles = self._normalize_named_items(
            self._response_value(profile_response, "profiles", default=[]),
            "profile_name",
            "profileName",
            "name",
        )
        current_profile = str(
            self._response_value(
                profile_response,
                "current_profile_name",
                "currentProfileName",
                default="",
            )
            or ""
        )

        record_active = False
        try:
            record_response = self._client.get_record_status()
            record_active = bool(
                self._response_value(record_response, "output_active", "outputActive", default=False)
            )
        except Exception:
            record_active = False

        return {
            "service": "OBS Studio",
            "mode": "real",
            "connected": True,
            "status": "Connected",
            "detail": "OBS production controls are ready.",
            "studio_mode_enabled": studio_mode_enabled,
            "program_scene": program_scene,
            "preview_scene": preview_scene,
            "transitions": transitions,
            "current_transition": current_transition,
            "transition_duration": transition_duration,
            "replay_buffer_active": replay_buffer_active,
            "replay_buffer_available": replay_buffer_available,
            "replay_buffer_detail": replay_buffer_detail,
            "last_replay_path": last_replay_path,
            "scene_collections": scene_collections,
            "current_scene_collection": current_scene_collection,
            "profiles": profiles,
            "current_profile": current_profile,
            "record_active": record_active,
        }

    def _fetch_source_items_from_obs(self, scene_name: str | None) -> dict[str, object]:
        assert self._client is not None

        if not scene_name:
            current_scene_response = self._client.get_current_program_scene()
            scene_name = str(
                self._response_value(
                    current_scene_response,
                    "current_program_scene_name",
                    "currentProgramSceneName",
                    "scene_name",
                    "sceneName",
                    default="",
                )
                or ""
            )

        if not scene_name:
            return {"connected": True, "scene_name": "", "items": []}

        response = self._client.get_scene_item_list(scene_name)
        items: list[dict[str, object]] = []
        for raw_item in self._response_value(response, "scene_items", "sceneItems", default=[]):
            item_id = int(self._response_value(raw_item, "scene_item_id", "sceneItemId", default=0) or 0)
            enabled = self._response_value(raw_item, "scene_item_enabled", "sceneItemEnabled", default=None)
            if enabled is None and item_id:
                try:
                    enabled_response = self._client.get_scene_item_enabled(scene_name, item_id)
                    enabled = bool(
                        self._response_value(
                            enabled_response,
                            "scene_item_enabled",
                            "sceneItemEnabled",
                            default=True,
                        )
                    )
                except Exception:
                    enabled = True
            items.append(
                {
                    "id": item_id,
                    "name": str(
                        self._response_value(raw_item, "source_name", "sourceName", default="") or ""
                    ),
                    "enabled": bool(enabled),
                    "is_group": bool(self._response_value(raw_item, "is_group", "isGroup", default=False)),
                    "locked": bool(
                        self._response_value(raw_item, "scene_item_locked", "sceneItemLocked", default=False)
                    ),
                }
            )
        return {"connected": True, "scene_name": scene_name, "items": items}

    def _fetch_audio_inputs_from_obs(self) -> dict[str, object]:
        assert self._client is not None

        response = self._client.get_input_list()
        raw_inputs = self._response_value(response, "inputs", default=[])
        special_names = set()
        try:
            special_inputs = self._client.get_special_inputs()
            for key in ("desktop1", "desktop2", "mic1", "mic2", "mic3", "mic4"):
                value = self._response_value(special_inputs, key, default="")
                if value:
                    special_names.add(str(value))
        except Exception:
            special_names = set()

        inputs: list[dict[str, object]] = []
        for raw_input in raw_inputs:
            input_name = str(self._response_value(raw_input, "input_name", "inputName", default="") or "")
            if not input_name:
                continue
            try:
                mute_response = self._client.get_input_mute(input_name)
                volume_response = self._client.get_input_volume(input_name)
            except Exception:
                continue
            inputs.append(
                {
                    "name": input_name,
                    "kind": str(
                        self._response_value(
                            raw_input,
                            "input_kind",
                            "inputKind",
                            "unversioned_input_kind",
                            "unversionedInputKind",
                            default="",
                        )
                        or ""
                    ),
                    "muted": bool(
                        self._response_value(mute_response, "input_muted", "inputMuted", default=False)
                    ),
                    "volume_db": float(
                        self._response_value(
                            volume_response,
                            "input_volume_db",
                            "inputVolumeDb",
                            default=0.0,
                        )
                        or 0.0
                    ),
                    "volume_mul": float(
                        self._response_value(
                            volume_response,
                            "input_volume_mul",
                            "inputVolumeMul",
                            default=1.0,
                        )
                        or 1.0
                    ),
                    "is_special": input_name in special_names,
                }
            )
        inputs.sort(key=lambda item: (not bool(item["is_special"]), str(item["name"]).lower()))
        return {"connected": True, "inputs": inputs}

    def _fetch_scene_transition_override_from_obs(self, scene_name: str) -> dict[str, object]:
        assert self._client is not None

        response = self._client.get_scene_scene_transition_override(scene_name)
        transition_name = str(
            self._response_value(
                response,
                "transition_name",
                "transitionName",
                default="",
            )
            or ""
        )
        duration = int(
            self._response_value(
                response,
                "transition_duration",
                "transitionDuration",
                default=0,
            )
            or 0
        )
        return {
            "scene_name": scene_name,
            "has_override": bool(transition_name),
            "transition_name": transition_name,
            "duration": duration,
        }

    async def _current_input_volume_db(self, input_name: str) -> float | None:
        if self._simulation_enabled:
            audio_input = self._simulated_audio_input(input_name)
            return None if audio_input is None else float(audio_input["volume_db"])

        if self._client is None:
            return None

        try:
            response = await asyncio.to_thread(self._client.get_input_volume, input_name)
        except Exception:
            return None

        return float(
            self._response_value(
                response,
                "input_volume_db",
                "inputVolumeDb",
                default=0.0,
            )
            or 0.0
        )

    async def _set_input_volume_db_without_refresh(self, input_name: str, volume_db: float) -> dict[str, object]:
        if self._simulation_enabled:
            audio_input = self._simulated_audio_input(input_name)
            if audio_input is None:
                return {"ok": False, "detail": f"Unknown simulated OBS audio input: {input_name}"}
            audio_input["volume_db"] = float(volume_db)
            return {"ok": True, "detail": ""}

        if self._client is None:
            return {"ok": False, "detail": "OBS is not connected."}

        try:
            await asyncio.to_thread(self._client.set_input_volume, input_name, None, float(volume_db))
            return {"ok": True, "detail": ""}
        except Exception as exc:
            self.connection_changed.emit(False, f"Could not change OBS audio level: {exc}")
            return {"ok": False, "detail": f"Could not change OBS audio level: {exc}"}

    @staticmethod
    def _snapshot_filename(source_name: str, image_format: str) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source_name).strip("-_.") or "obs-source"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{safe_name}-{timestamp}.{image_format}"

    @staticmethod
    def _normalize_scene_payload(response: Any) -> dict[str, object]:
        scenes: list[dict[str, object]] = []
        for raw_scene in ObsService._response_value(response, "scenes", default=[]):
            name = ObsService._response_value(raw_scene, "scene_name", "sceneName", "name", default="")
            if name:
                scenes.append({"id": str(name), "name": str(name)})
        return {
            "scenes": scenes,
            "current": ObsService._response_value(
                response,
                "current_program_scene_name",
                "currentProgramSceneName",
                default=None,
            ),
        }

    @staticmethod
    def _normalize_stream_status_payload(response: Any) -> dict[str, object]:
        is_live = bool(ObsService._response_value(response, "output_active", "outputActive", default=False))
        output_state = str(
            ObsService._response_value(response, "output_state", "outputState", default="")
        ).strip()
        detail = output_state or (
            "OBS stream output is active." if is_live else "OBS stream output is idle."
        )
        return {
            "service": "OBS Studio",
            "mode": "real",
            "connected": True,
            "is_live": is_live,
            "status": "Live" if is_live else "Offline",
            "detail": detail,
        }

    @staticmethod
    def _disconnected_stream_status(detail: str = "OBS Studio is disconnected.") -> dict[str, object]:
        return {
            "service": "OBS Studio",
            "mode": "disconnected",
            "connected": False,
            "is_live": False,
            "status": "Disconnected",
            "detail": detail,
        }

    @staticmethod
    def _disconnected_production_state(detail: str = "OBS Studio is disconnected.") -> dict[str, object]:
        return {
            "service": "OBS Studio",
            "mode": "disconnected",
            "connected": False,
            "status": "Disconnected",
            "detail": detail,
            "studio_mode_enabled": False,
            "program_scene": "",
            "preview_scene": "",
            "transitions": [],
            "current_transition": "",
            "transition_duration": 300,
            "replay_buffer_active": False,
            "replay_buffer_available": False,
            "replay_buffer_detail": "Replay buffer is unavailable while OBS is disconnected.",
            "last_replay_path": "",
            "scene_collections": [],
            "current_scene_collection": "",
            "profiles": [],
            "current_profile": "",
            "record_active": False,
        }

    @staticmethod
    def _replay_buffer_unavailable_detail(exc: Exception) -> str:
        raw = " ".join(str(exc).split()).strip()
        if not raw:
            return "Replay buffer status is unavailable. Enable Replay Buffer in OBS Output settings if you want clip controls here."
        lowered = raw.lower()
        if "replay" in lowered or "buffer" in lowered or "output" in lowered or "feature" in lowered:
            return (
                "Replay buffer is unavailable in OBS right now. Enable Replay Buffer in OBS Output settings "
                f"if you want clip controls here. Details: {raw}"
            )
        return f"Replay buffer status is unavailable right now. Details: {raw}"

    @staticmethod
    def _normalize_named_items(raw_items: Any, *names: str) -> list[str]:
        values: list[str] = []
        for raw_item in raw_items or []:
            if isinstance(raw_item, str):
                name = raw_item
            else:
                name = ObsService._response_value(raw_item, *names, default="")
            if name:
                values.append(str(name))
        return values

    @staticmethod
    def _response_value(response: Any, *names: str, default: Any = None) -> Any:
        if isinstance(response, dict):
            for name in names:
                if name in response:
                    return response[name]
            return default
        for name in names:
            if hasattr(response, name):
                return getattr(response, name)
        return default
