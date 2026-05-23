from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path

import carb
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


async def _run() -> int:
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_ROOT / "organiq_window_check.json"
    app = omni.kit.app.get_app()
    window = None

    for _ in range(300):
        window = ui.Workspace.get_window("Organiq")
        if window is not None and window.visible:
            break
        await app.next_update_async()

    visible = bool(window is not None and window.visible)
    report = {
        "status": "ok" if visible else "failed",
        "repo_root": str(REPO_ROOT),
        "extension_id": "com.chrisvoncsefalvay.organiq",
        "extension_version": _extension_version(),
        "window_found": window is not None,
        "window_visible": visible,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not visible:
        raise AssertionError("Organiq window did not auto-show")
    carb.log_info(f"Organiq window check passed: {report_path}")
    print("organiq_window_check=ok")
    print(f"report={report_path}")
    return 0


async def _entry() -> None:
    exit_code = 1
    try:
        exit_code = await _run()
    except Exception as exc:
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        report_path = BUILD_ROOT / "organiq_window_check.json"
        report_path.write_text(
            json.dumps({"status": "failed", "error": str(exc), "traceback": traceback.format_exc()}, indent=2),
            encoding="utf-8",
        )
        carb.log_error(f"Organiq window check failed: {exc}")
        print(f"organiq_window_check=failed: {exc}")
        print(f"report={report_path}")
    finally:
        omni.kit.app.get_app().post_uncancellable_quit(exit_code)


asyncio.ensure_future(_entry())
