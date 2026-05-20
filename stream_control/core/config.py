from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths

logger = logging.getLogger(__name__)


class ConfigStore:
    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths

    def load(self) -> AppConfig:
        config_file = self._paths.config_file
        if not config_file.exists():
            return AppConfig()

        try:
            raw = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            recovery_path = self._quarantine_config(config_file)
            logger.warning(
                "Failed to read config %s; using defaults%s. Details: %s",
                config_file,
                f" after moving the unreadable file to {recovery_path}" if recovery_path is not None else "",
                exc,
            )
            return AppConfig()

        return AppConfig.from_dict(raw)

    def save(self, config: AppConfig) -> None:
        config_file = self._paths.config_file
        config_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), indent=2) + "\n"

        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f"{config_file.stem}.",
            suffix=".tmp",
            dir=str(config_file.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, config_file)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _quarantine_config(self, config_file: Path) -> Path | None:
        if not config_file.exists():
            return None

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        for attempt in range(1000):
            suffix = "" if attempt == 0 else f"-{attempt}"
            candidate = config_file.with_name(f"{config_file.stem}.corrupt-{timestamp}{suffix}{config_file.suffix}")
            if candidate.exists():
                continue
            try:
                config_file.replace(candidate)
            except OSError:
                return None
            return candidate
        return None
