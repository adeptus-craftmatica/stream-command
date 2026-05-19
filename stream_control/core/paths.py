from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir


@dataclass(slots=True)
class AppPaths:
    root: Path
    config_file: Path

    @classmethod
    def build(cls) -> "AppPaths":
        root = Path(user_data_dir("StreamControl", "StreamControl", roaming=True))
        root.mkdir(parents=True, exist_ok=True)
        return cls(root=root, config_file=root / "config.json")
