"""Launch the Organiq physics showcase in a visible Isaac Sim session."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGE_PATH = REPO_ROOT / "build" / "organiq_physics_showcase.usd"
DEFAULT_REPORT_PATH = REPO_ROOT / "build" / "organiq_physics_showcase_simapp_opened.json"
DEFAULT_CAMERA_PATH = "/World/Showcase/Cameras/main_camera"
DEFAULT_SHOWCASE_PATH = "/World/Showcase"


def _write_report(report_path: Path, payload: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _default_experience_path() -> str | None:
    explicit = os.environ.get("ORGANIQ_ISAAC_EXPERIENCE")
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        candidate = parent / "apps" / "isaacsim.exp.full.kit"
        if candidate.exists():
            return str(candidate)
    return None


def main() -> int:
    stage_path = Path(os.environ.get("ORGANIQ_SHOWCASE_USD", DEFAULT_STAGE_PATH)).resolve()
    report_path = Path(os.environ.get("ORGANIQ_SHOWCASE_OPEN_REPORT", DEFAULT_REPORT_PATH)).resolve()
    capture_path = Path(
        os.environ.get("ORGANIQ_SHOWCASE_CAPTURE_PATH", str(report_path.with_suffix(".png")))
    ).resolve()
    capture_enabled = os.environ.get("ORGANIQ_SHOWCASE_CAPTURE", "0").strip().lower() in {"1", "true", "yes"}

    payload: dict[str, Any] = {
        "stage_path": str(stage_path),
        "stage_exists": stage_path.exists(),
        "opened": False,
    }

    if not stage_path.exists():
        payload["error"] = "Stage file does not exist"
        _write_report(report_path, payload)
        return 1

    from isaacsim import SimulationApp

    simulation_app = SimulationApp(
        {
            "headless": False,
            "open_usd": str(stage_path),
            "create_new_stage": False,
            "width": 1600,
            "height": 1000,
            "window_width": 1600,
            "window_height": 1000,
        },
        experience=_default_experience_path(),
    )

    try:
        import carb
        import omni.timeline
        import omni.usd
        from pxr import UsdGeom

        context = omni.usd.get_context()
        stage = context.get_stage()
        if not _stage_matches_path(stage, stage_path):
            payload["explicit_open_stage_return"] = bool(context.open_stage(str(stage_path)))
        for _ in range(360):
            simulation_app.update()
            stage = context.get_stage()
            if _stage_matches_path(stage, stage_path):
                break

        stage = context.get_stage()
        if stage is None:
            raise RuntimeError("No USD stage is available")

        custom_data = dict(stage.GetRootLayer().customLayerData or {})
        camera_path = str(
            os.environ.get("ORGANIQ_SHOWCASE_CAMERA")
            or custom_data.get("cameraPrim")
            or _discover_camera_path(stage)
            or DEFAULT_CAMERA_PATH
        )
        showcase_path = str(
            os.environ.get("ORGANIQ_SHOWCASE_ROOT")
            or custom_data.get("showcasePrim")
            or _discover_showcase_root(stage)
            or DEFAULT_SHOWCASE_PATH
        )
        initial_time_code = float(
            os.environ.get("ORGANIQ_SHOWCASE_INITIAL_FRAME")
            or custom_data.get("initialTimeCode")
            or _discover_initial_time_code(stage, showcase_path)
            or stage.GetStartTimeCode()
            or 1.0
        )
        camera_prim = stage.GetPrimAtPath(camera_path)
        showcase_prim = stage.GetPrimAtPath(showcase_path)
        payload["root_layer"] = stage.GetRootLayer().identifier
        payload["camera"] = camera_path
        payload["showcase_prim"] = showcase_path
        payload["camera_valid"] = bool(camera_prim and camera_prim.IsValid())
        payload["showcase_valid"] = bool(showcase_prim and showcase_prim.IsValid())

        timeline = omni.timeline.get_timeline_interface()
        time_codes_per_second = float(stage.GetTimeCodesPerSecond() or 60.0)
        target_time_seconds = initial_time_code / time_codes_per_second
        try:
            if timeline.is_playing():
                timeline.stop()
        except Exception:
            pass
        timeline.set_time_codes_per_second(time_codes_per_second)
        timeline.set_start_time(float(stage.GetStartTimeCode()) / time_codes_per_second)
        timeline.set_end_time(float(stage.GetEndTimeCode()) / time_codes_per_second)
        timeline.set_current_time(target_time_seconds)
        try:
            timeline.commit()
        except Exception:
            pass
        timeline.set_current_time(target_time_seconds)
        payload["timeline_start"] = timeline.get_start_time()
        payload["timeline_end"] = timeline.get_end_time()
        payload["timeline_current_time"] = timeline.get_current_time()
        payload["initial_time_code"] = initial_time_code
        payload["target_time_seconds"] = target_time_seconds
        payload["timeline_window_visible"] = _show_timeline_window(simulation_app)

        try:
            from omni.kit.viewport.utility import frame_viewport_selection, get_active_viewport

            viewport = get_active_viewport()
            if viewport:
                viewport.camera_path = camera_path
                simulation_app.update()
                context.get_selection().set_selected_prim_paths([showcase_path], True)
                frame_viewport_selection()
                viewport.camera_path = camera_path
                simulation_app.update()
                payload["viewport_configured"] = True
            else:
                payload["viewport_configured"] = False
        except Exception as exc:  # noqa: BLE001 - viewport setup is diagnostic only.
            payload["viewport_error"] = f"{type(exc).__name__}: {exc}"

        if capture_enabled:
            payload["capture_pending"] = True
            _write_report(report_path, payload)
            try:
                payload["capture_path"] = str(capture_path)
                payload["capture_stats"] = _capture_viewport_snapshot(capture_path, simulation_app)
                payload["capture_pending"] = False
            except Exception as exc:  # noqa: BLE001 - capture is diagnostic only.
                payload["capture_error"] = f"{type(exc).__name__}: {exc}"
                payload["capture_pending"] = False

        bounds = UsdGeom.BBoxCache(1.0, [UsdGeom.Tokens.default_]).ComputeWorldBound(showcase_prim)
        payload["showcase_bounds"] = [float(value) for value in bounds.ComputeAlignedRange().GetSize()]
        payload["opened"] = bool(payload["camera_valid"] and payload["showcase_valid"])
        _write_report(report_path, payload)
        carb.log_info(f"Organiq showcase opened from {stage_path}")

        while simulation_app.is_running():
            simulation_app.update()
    except Exception as exc:  # noqa: BLE001 - the report is for launch diagnostics.
        payload["error"] = f"{type(exc).__name__}: {exc}"
        _write_report(report_path, payload)
        return 1
    finally:
        if not payload.get("opened"):
            simulation_app.close()

    return 0


def _show_timeline_window(simulation_app: Any) -> bool:
    try:
        manager = simulation_app.app.get_extension_manager()
        manager.set_extension_enabled_immediate("omni.kit.widget.timeline", True)
        manager.set_extension_enabled_immediate("omni.anim.widget.timeline", True)
        manager.set_extension_enabled_immediate("omni.anim.window.timeline", True)
        simulation_app.update()
        import omni.ui as ui

        ui.Workspace.show_window("Timeline toolbar", True)
        simulation_app.update()
        window = ui.Workspace.get_window("Timeline toolbar")
        return bool(window and window.visible)
    except Exception:
        return False


def _capture_viewport_snapshot(capture_path: Path, simulation_app: Any) -> dict[str, float | int | bool]:
    import asyncio

    async def _capture() -> bool:
        from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport, next_viewport_frame_async

        viewport = get_active_viewport()
        if viewport is None:
            raise RuntimeError("No active viewport is available")
        await next_viewport_frame_async(viewport)
        capture = capture_viewport_to_file(viewport, file_path=str(capture_path))
        if capture is None:
            raise RuntimeError("Viewport capture did not return a capture object")
        return bool(await capture.wait_for_result(completion_frames=120))

    capture_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(12):
        simulation_app.update()
    loop = asyncio.get_event_loop()
    capture_ok = bool(loop.run_until_complete(asyncio.wait_for(_capture(), timeout=20.0)))
    for _ in range(6):
        simulation_app.update()
    try:
        import omni.kit.renderer_capture

        omni.kit.renderer_capture.acquire_renderer_capture_interface().wait_async_capture()
    except Exception:
        pass
    if not capture_path.exists() or capture_path.stat().st_size == 0:
        raise RuntimeError("Viewport capture was not written")

    stats: dict[str, float | int | bool] = {
        "capture_ok": capture_ok,
        "width": 0,
        "height": 0,
        "visible_pixels": 0,
        "bbox_width_fraction": 0.0,
        "bbox_height_fraction": 0.0,
        "visually_blank": False,
    }
    try:
        import imageio.v3 as iio
        import numpy as np

        image = iio.imread(capture_path)
        rgb = np.asarray(image[..., :3], dtype=np.float32)
        stats["width"] = int(rgb.shape[1])
        stats["height"] = int(rgb.shape[0])
        if int(rgb.max()) <= int(rgb.min()):
            stats["visually_blank"] = True
            return stats
        border = np.concatenate(
            (
                rgb[:24].reshape(-1, 3),
                rgb[-24:].reshape(-1, 3),
                rgb[:, :24].reshape(-1, 3),
                rgb[:, -24:].reshape(-1, 3),
            )
        )
        background = np.median(border, axis=0)
        delta = np.linalg.norm(rgb - background.reshape(1, 1, 3), axis=2)
        visible_mask = delta > 22.0
        ys, xs = np.where(visible_mask)
        stats["visible_pixels"] = int(np.count_nonzero(visible_mask))
        stats["visually_blank"] = bool(stats["visible_pixels"] < 2500)
        if xs.size and ys.size:
            stats["bbox_width_fraction"] = float((int(xs.max()) - int(xs.min()) + 1) / float(rgb.shape[1]))
            stats["bbox_height_fraction"] = float((int(ys.max()) - int(ys.min()) + 1) / float(rgb.shape[0]))
    except ImportError:
        pass
    return stats


def _discover_camera_path(stage: Any) -> str | None:
    for path in (
        "/World/PhysicsProbe/Cameras/main_camera",
        "/World/Showcase/Cameras/main_camera",
    ):
        if stage.GetPrimAtPath(path).IsValid():
            return path
    return None


def _discover_showcase_root(stage: Any) -> str | None:
    for path in (
        "/World/PhysicsProbe",
        "/World/Showcase",
    ):
        if stage.GetPrimAtPath(path).IsValid():
            return path
    return None


def _discover_initial_time_code(stage: Any, showcase_path: str) -> float | None:
    if showcase_path == "/World/PhysicsProbe" and stage.GetPrimAtPath(showcase_path).IsValid():
        return 118.0
    if showcase_path == "/World/Showcase" and stage.GetPrimAtPath(showcase_path).IsValid():
        return 96.0
    return None


def _stage_matches_path(stage: Any, stage_path: Path) -> bool:
    if stage is None:
        return False
    try:
        layer = stage.GetRootLayer()
        candidates = [layer.realPath, layer.identifier]
        expected = stage_path.resolve().as_posix().lower()
        for candidate in candidates:
            if not candidate:
                continue
            normalised = Path(str(candidate)).resolve().as_posix().lower()
            if normalised == expected:
                return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    raise SystemExit(main())
