from __future__ import annotations

import os
from pathlib import Path


def _default_work_root() -> Path:
    override = os.environ.get("ORGANIQ_WORK_ROOT")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Organiq"
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "organiq"
    return Path.home() / ".cache" / "organiq"


DEFAULT_WORK_ROOT = _default_work_root()
DEFAULT_MODEL_ROOT = DEFAULT_WORK_ROOT / "models"
DEFAULT_OUTPUT_ROOT = DEFAULT_WORK_ROOT / "outputs"


def ensure_work_roots() -> None:
    DEFAULT_MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
