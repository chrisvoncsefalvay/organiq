from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

import carb
import omni.kit.app
import omni.usd
from pxr import UsdGeom


REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"
BUILD_ROOT = REPO_ROOT / "build"
sys.path.insert(0, str(EXT_ROOT))

from com.chrisvoncsefalvay.organiq.usd_writer import instantiate_usd_on_stage
from com.chrisvoncsefalvay.organiq.viewport import frame_paths_next_update, renderable_mesh_paths


LAST_REPORT: dict[str, object] = {}


def _default_usd_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Organiq" / "outputs" / "organiq_scene.usd"
    return Path.home() / ".cache" / "organiq" / "outputs" / "organiq_scene.usd"


async def _run() -> int:
    global LAST_REPORT
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_ROOT / "organiq_instantiated_usd_visibility_check.json"
    capture_path = BUILD_ROOT / "organiq_instantiated_usd_visibility.png"
    usd_path = Path(os.environ.get("ORGANIQ_USD_PATH", str(_default_usd_path())))
    _require(usd_path.exists(), f"USD file does not exist: {usd_path}")

    app = omni.kit.app.get_app()
    for _ in range(12):
        await app.next_update_async()

    context = omni.usd.get_context()
    await context.new_stage_async()
    stage = context.get_stage()
    _require(stage is not None, "Kit did not create a USD stage")
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    instance_path = instantiate_usd_on_stage(stage, usd_path, "/World/Organiq_loaded_visibility")
    root = stage.GetPrimAtPath(instance_path)
    _require(root.IsValid(), "instance root missing")
    camera_path = root.GetAttribute("organiq:viewCamera").Get()
    _require(camera_path == f"{instance_path}/view_camera", f"instance view camera is {camera_path}")
    _require(stage.GetPrimAtPath(camera_path).IsA(UsdGeom.Camera), "instance view camera missing")

    for _ in range(12):
        await app.next_update_async()

    mesh_paths = tuple(renderable_mesh_paths(stage, instance_path))
    expected_mesh_count = int(root.GetAttribute("organiq:expectedMeshCount").Get() or 0)
    mesh_diagnostics = _mesh_diagnostics(stage, instance_path, mesh_paths)
    LAST_REPORT = {
        "usd_path": str(usd_path),
        "instance_path": instance_path,
        "camera_path": camera_path,
        "mesh_count": len(mesh_paths),
        "expected_mesh_count": expected_mesh_count,
        "mesh_diagnostics": mesh_diagnostics,
    }
    _require(expected_mesh_count > 0, "instance expected mesh count is zero")
    _require(len(mesh_paths) >= expected_mesh_count, f"renderable meshes {len(mesh_paths)} < expected {expected_mesh_count}")

    frame = await frame_paths_next_update(
        (instance_path,),
        expand_to_meshes=True,
        camera_path=camera_path,
        update_count=18,
    )
    _require(frame.camera_set, "viewport did not switch to the local instance camera")
    _require(frame.framed, "viewport did not frame the instantiated meshes")
    _require(set(mesh_paths).issubset(set(frame.selected_paths)), "not all visual meshes were selected for framing")
    LAST_REPORT.update(
        {
            "selected_paths": list(frame.selected_paths),
            "selected_mesh_diagnostics": _mesh_diagnostics(stage, instance_path, frame.selected_paths),
        }
    )

    capture_stats = await _capture_viewport(capture_path)
    LAST_REPORT["capture_stats"] = capture_stats
    _require(capture_stats["visible_tissue_pixels"] >= 5000, "viewport capture has too little visible tissue")
    _require(capture_stats["tissue_bbox_width_fraction"] >= 0.12, "visible tissue is too narrow in the viewport")
    _require(capture_stats["tissue_bbox_height_fraction"] >= 0.10, "visible tissue is too short in the viewport")
    _require(capture_stats["tissue_bbox_width_fraction"] <= 0.95, "visible tissue is cropped too tightly")
    _require(capture_stats["tissue_bbox_height_fraction"] <= 0.90, "visible tissue is cropped too tightly")

    report = {
        "status": "ok",
        "usd_path": str(usd_path),
        "instance_path": instance_path,
        "camera_path": camera_path,
        "mesh_count": len(mesh_paths),
        "expected_mesh_count": expected_mesh_count,
        "selected_paths": list(frame.selected_paths),
        "capture_path": str(capture_path),
        "capture_stats": capture_stats,
    }
    LAST_REPORT = report
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    carb.log_info(f"Organiq instantiated USD visibility check passed: {report_path}")
    print("organiq_instantiated_usd_visibility_check=ok")
    print(f"report={report_path}")
    print(f"capture={capture_path}")
    return 0


async def _capture_viewport(capture_path: Path) -> dict[str, float | int]:
    from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport, next_viewport_frame_async

    viewport = get_active_viewport()
    _require(viewport is not None, "no active viewport is available")
    await next_viewport_frame_async(viewport)
    capture = capture_viewport_to_file(viewport, file_path=str(capture_path))
    _require(capture is not None, "viewport capture did not return a capture object")
    result = await capture.wait_for_result(completion_frames=90)
    _require(bool(result), "viewport capture did not complete")
    try:
        import omni.kit.renderer_capture

        omni.kit.renderer_capture.acquire_renderer_capture_interface().wait_async_capture()
    except Exception:
        pass
    _require(capture_path.exists() and capture_path.stat().st_size > 0, "viewport capture was not written")

    stats = {
        "red_tissue_pixels": 0,
        "visible_tissue_pixels": 0,
        "width": 0,
        "height": 0,
        "tissue_bbox_width_fraction": 0.0,
        "tissue_bbox_height_fraction": 0.0,
    }
    try:
        import imageio.v3 as iio
        import numpy as np

        image = iio.imread(capture_path)
        _require(int(image.max()) > int(image.min()), "viewport capture is visually blank")
        rgb = np.asarray(image[..., :3], dtype=np.float32)
        red_mask = (rgb[..., 0] > 70.0) & (rgb[..., 0] > rgb[..., 1] * 1.12) & (rgb[..., 0] > rgb[..., 2] * 1.05)
        border = np.concatenate(
            (rgb[:24].reshape(-1, 3), rgb[-24:].reshape(-1, 3), rgb[:, :24].reshape(-1, 3), rgb[:, -24:].reshape(-1, 3))
        )
        background = np.median(border, axis=0)
        delta = np.linalg.norm(rgb - background.reshape(1, 1, 3), axis=2)
        bright_anatomy_mask = (rgb.mean(axis=2) > 135.0) & (delta > 30.0)
        visible_mask = red_mask | bright_anatomy_mask
        ys, xs = np.where(visible_mask)
        bbox_width_fraction = 0.0
        bbox_height_fraction = 0.0
        if xs.size and ys.size:
            bbox_width_fraction = float((int(xs.max()) - int(xs.min()) + 1) / float(rgb.shape[1]))
            bbox_height_fraction = float((int(ys.max()) - int(ys.min()) + 1) / float(rgb.shape[0]))
        stats = {
            "red_tissue_pixels": int(np.count_nonzero(red_mask)),
            "visible_tissue_pixels": int(np.count_nonzero(visible_mask)),
            "width": int(rgb.shape[1]),
            "height": int(rgb.shape[0]),
            "tissue_bbox_width_fraction": bbox_width_fraction,
            "tissue_bbox_height_fraction": bbox_height_fraction,
        }
    except ImportError:
        pass
    return stats


def _mesh_diagnostics(stage, instance_path: str, paths) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    try:
        from pxr import Usd

        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
            useExtentsHint=False,
        )
    except Exception:
        bbox_cache = None
    for path in paths:
        prim = stage.GetPrimAtPath(str(path))
        if not prim or not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        imageable = UsdGeom.Imageable(prim)
        points = mesh.GetPointsAttr().Get() or []
        extent = mesh.GetExtentAttr().Get()
        world_bounds = None
        if bbox_cache is not None:
            aligned = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if not aligned.IsEmpty():
                min_pt = aligned.GetMin()
                max_pt = aligned.GetMax()
                world_bounds = [
                    [float(min_pt[0]), float(min_pt[1]), float(min_pt[2])],
                    [float(max_pt[0]), float(max_pt[1]), float(max_pt[2])],
                ]
        diagnostics.append(
            {
                "path": str(path),
                "relative_path": str(path).replace(instance_path, "", 1),
                "label": prim.GetAttribute("organiq:labelName").Get()
                if prim.GetAttribute("organiq:labelName")
                else None,
                "role": prim.GetAttribute("organiq:role").Get() if prim.GetAttribute("organiq:role") else None,
                "points": len(points),
                "faces": len(mesh.GetFaceVertexCountsAttr().Get() or []),
                "extent": [[float(value) for value in point] for point in extent] if extent else None,
                "world_bounds": world_bounds,
                "visibility": str(imageable.ComputeVisibility()),
                "purpose": str(imageable.ComputePurpose()),
            }
        )
    return diagnostics


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _entry() -> None:
    exit_code = 1
    try:
        exit_code = await _run()
    except Exception as exc:
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        report_path = BUILD_ROOT / "organiq_instantiated_usd_visibility_check.json"
        report_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "details": LAST_REPORT,
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        carb.log_error(f"Organiq instantiated USD visibility check failed: {exc}")
        print(f"organiq_instantiated_usd_visibility_check=failed: {exc}")
        print(f"report={report_path}")
    finally:
        omni.kit.app.get_app().post_uncancellable_quit(exit_code)


asyncio.ensure_future(_entry())
