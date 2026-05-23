from __future__ import annotations

import asyncio
import json
from pathlib import Path

import omni.kit.app
import omni.ui as ui


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = REPO_ROOT / "build"
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"


def _extension_version() -> str:
    manifest = EXT_ROOT / "config" / "extension.toml"
    if not manifest.exists():
        return "unknown"
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def _write_report(status: str, window_found: bool, window_visible: bool) -> None:
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report = {
        "status": status,
        "repo_root": str(REPO_ROOT),
        "extension_id": "com.chrisvoncsefalvay.organiq",
        "extension_version": _extension_version(),
        "window_found": window_found,
        "window_visible": window_visible,
    }
    (BUILD_ROOT / "organiq_launch_check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


async def show_organiq_window() -> None:
    app = omni.kit.app.get_app()
    for _ in range(240):
        window = ui.Workspace.get_window("Organiq")
        if window:
            window.visible = True
            viewport = ui.Workspace.get_window("Viewport")
            if viewport:
                window.dock_in(viewport, ui.DockPosition.LEFT, 0.24)
            _write_report("ok", True, bool(window.visible))
            return
        await app.next_update_async()
    _write_report("failed", False, False)


asyncio.ensure_future(show_organiq_window())
