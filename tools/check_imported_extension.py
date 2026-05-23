from __future__ import annotations

import asyncio
import json
import os
import traceback
from pathlib import Path

import carb
import omni.kit.app


EXTENSION_ID = "com.chrisvoncsefalvay.organiq"
REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = REPO_ROOT / "build"


def _matching_extensions(manager):
    matches = []
    for extension in manager.get_extensions():
        data = extension.get_dict() if hasattr(extension, "get_dict") else dict(extension)
        if data.get("name") == EXTENSION_ID or str(data.get("id", "")).startswith(f"{EXTENSION_ID}-"):
            matches.append(data)
    return matches


async def _run() -> int:
    app = omni.kit.app.get_app()
    manager = app.get_extension_manager()

    matches = []
    for _ in range(120):
        matches = _matching_extensions(manager)
        if matches:
            break
        await app.next_update_async()

    enabled_before = bool(manager.is_extension_enabled(EXTENSION_ID))
    enabled_after = enabled_before
    enable_error = ""
    if matches and not enabled_before:
        try:
            manager.set_extension_enabled_immediate(EXTENSION_ID, True)
            enabled_after = bool(manager.is_extension_enabled(EXTENSION_ID))
        except Exception as exc:
            enable_error = str(exc)

    enabled_id = ""
    if enabled_after:
        try:
            enabled_id = str(manager.get_enabled_extension_id(EXTENSION_ID))
        except Exception:
            enabled_id = ""

    expected_root = os.environ.get("ORGANIQ_IMPORTED_EXTENSION_ROOT", "")
    paths = [str(match.get("path", "")) for match in matches]
    path_matches_expected = True
    if expected_root:
        expected = str(Path(expected_root).resolve()).lower()
        path_matches_expected = any(str(Path(path).resolve()).lower() == expected for path in paths if path)

    ok = bool(matches and enabled_after and path_matches_expected)
    report = {
        "status": "ok" if ok else "failed",
        "extension_id": EXTENSION_ID,
        "enabled_before": enabled_before,
        "enabled_after": enabled_after,
        "enabled_id": enabled_id,
        "enable_error": enable_error,
        "expected_root": expected_root,
        "path_matches_expected": path_matches_expected,
        "matches": [
            {
                "id": str(match.get("id", "")),
                "package_id": str(match.get("package_id", "")),
                "name": str(match.get("name", "")),
                "version": str(match.get("version", "")),
                "path": str(match.get("path", "")),
                "enabled": bool(match.get("enabled", False)),
            }
            for match in matches
        ],
    }
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_ROOT / "organiq_imported_extension_check.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if not ok:
        raise AssertionError(f"Imported extension is not discoverable and enabled: {report_path}")

    carb.log_info(f"Organiq imported extension check passed: {report_path}")
    print("organiq_imported_extension_check=ok")
    print(f"report={report_path}")
    return 0


async def _entry() -> None:
    exit_code = 1
    report_path = BUILD_ROOT / "organiq_imported_extension_check.json"
    try:
        exit_code = await _run()
    except Exception as exc:
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        report = {"status": "failed", "error": str(exc), "traceback": traceback.format_exc()}
        if report_path.exists():
            try:
                report["previous_report"] = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        carb.log_error(f"Organiq imported extension check failed: {exc}")
        print(f"organiq_imported_extension_check=failed: {exc}")
        print(f"report={report_path}")
    finally:
        omni.kit.app.get_app().post_uncancellable_quit(exit_code)


asyncio.ensure_future(_entry())
