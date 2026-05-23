from __future__ import annotations

import asyncio
import gc
import json
import os
import traceback
from pathlib import Path

import carb
import omni.kit.app


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = REPO_ROOT / "build"
REPORT_PATH = BUILD_ROOT / "organiq_content_browser_check.json"
USD_ENV = "ORGANIQ_USD_PATH"
EXTENSION_CLASS = "com.chrisvoncsefalvay.organiq.extension.OrganiqExtension"


def _find_organiq_extension():
    for item in gc.get_objects():
        item_type = type(item)
        class_name = f"{item_type.__module__}.{item_type.__name__}"
        if class_name == EXTENSION_CLASS:
            return item
    return None


async def _run() -> int:
    from com.chrisvoncsefalvay.organiq.paths import DEFAULT_OUTPUT_ROOT
    from omni.kit.window.content_browser import get_content_window

    usd_path = Path(os.environ.get(USD_ENV, DEFAULT_OUTPUT_ROOT / "organiq_scene.usd"))
    if not usd_path.exists():
        raise FileNotFoundError(f"USD path does not exist: {usd_path}")

    app = omni.kit.app.get_app()
    extension = None
    for _ in range(300):
        extension = _find_organiq_extension()
        if extension is not None:
            break
        await app.next_update_async()
    if extension is None:
        raise RuntimeError("Organiq extension instance was not found")

    revealed = await extension._show_in_content_browser_async(usd_path, select_file=True, show_content=True, navigate=True)
    for _ in range(12):
        await app.next_update_async()

    content_browser = get_content_window()
    current_directory = content_browser.get_current_directory() if content_browser else None
    selections = content_browser.get_current_selections() if content_browser else []
    output_root = Path(DEFAULT_OUTPUT_ROOT).resolve()
    bookmarked = False
    if content_browser and content_browser.api and content_browser.api.view:
        bookmarked = bool(content_browser.api.view.is_bookmark(None, path=str(output_root)))

    selected_usd = any(Path(str(selection).replace("file://", "")).name == usd_path.name for selection in selections)
    ok = bool(revealed and bookmarked and current_directory and selected_usd)
    report = {
        "status": "ok" if ok else "failed",
        "usd_path": str(usd_path),
        "current_directory": str(current_directory),
        "selections": [str(selection) for selection in selections],
        "bookmarked": bookmarked,
        "revealed": bool(revealed),
    }
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report={REPORT_PATH}")
    print(f"organiq_content_browser_check={report['status']}")
    return 0 if ok else 1


async def _entry() -> None:
    exit_code = 1
    try:
        exit_code = await _run()
    except Exception as exc:
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        carb.log_error(f"Organiq Content browser check failed: {exc}")
        print(f"organiq_content_browser_check=failed: {exc}")
        print(f"report={REPORT_PATH}")
    finally:
        omni.kit.app.get_app().post_uncancellable_quit(exit_code)


asyncio.ensure_future(_entry())
