from __future__ import annotations

import json

from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths


class ConfigStore:
    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths

    def load(self) -> AppConfig:
        if not self._paths.config_file.exists():
            return AppConfig()

        raw = json.loads(self._paths.config_file.read_text(encoding="utf-8"))
        return AppConfig.from_dict(raw)

    def save(self, config: AppConfig) -> None:
        self._paths.config_file.write_text(
            json.dumps(config.to_dict(), indent=2),
            encoding="utf-8",
        )
