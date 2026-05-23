from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

import carb
import omni.kit.app
import omni.kit.commands
import omni.usd
from pxr import Usd, UsdGeom


REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"
BUILD_ROOT = REPO_ROOT / "build"
sys.path.insert(0, str(EXT_ROOT))

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
    report_path = BUILD_ROOT / "organiq_exported_usd_import_visibility_check.json"
    capture_path = BUILD_ROOT / "organiq_exported_usd_import_visibility.png"
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

    import_path = "/World/organiq_scene"
    omni.kit.commands.execute(
        "CreateReferenceCommand",
        usd_context=context,
        path_to=import_path,
        asset_path=str(usd_path),
        instanceable=False,
    )
    for _ in range(20):
        await app.next_update_async()

    mesh_paths = tuple(renderable_mesh_paths(stage, import_path))
    mesh_diagnostics = _mesh_diagnostics(stage, import_path, mesh_paths)
    material_diagnostics = _material_diagnostics(stage, mesh_paths)
    LAST_REPORT = {
        "usd_path": str(usd_path),
        "import_path": import_path,
        "mesh_count": len(mesh_paths),
        "mesh_diagnostics": mesh_diagnostics,
        "material_diagnostics": material_diagnostics,
    }
    _require(stage.GetPrimAtPath(import_path).IsValid(), "imported reference root missing")
    _require(len(mesh_paths) > 0, "direct import has no renderable organ meshes")
    _require(
        any(record.get("visual_binding") for record in material_diagnostics),
        "direct import has no visual material bindings",
    )
    _require(
        not any(record.get("visual_has_mdl_surface") for record in material_diagnostics),
        "direct import is bound to active MDL materials instead of viewport-safe materials",
    )

    camera_path = _first_camera_path(stage, import_path)
    frame = await frame_paths_next_update(
        (import_path,),
        expand_to_meshes=True,
        camera_path=camera_path,
        update_count=18,
    )
    _require(frame.framed, "viewport did not frame the directly imported meshes")
    LAST_REPORT.update({"selected_paths": list(frame.selected_paths), "camera_path": camera_path})

    capture_stats = await _capture_viewport(capture_path)
    LAST_REPORT["capture_stats"] = capture_stats
    _require(capture_stats["visible_tissue_pixels"] >= 5000, "direct import capture has too little visible tissue")
    _require(capture_stats["tissue_bbox_width_fraction"] >= 0.12, "direct import visible tissue is too narrow")
    _require(capture_stats["tissue_bbox_height_fraction"] >= 0.10, "direct import visible tissue is too short")

    report = {
        "status": "ok",
        "usd_path": str(usd_path),
        "import_path": import_path,
        "camera_path": camera_path,
        "mesh_count": len(mesh_paths),
        "selected_paths": list(frame.selected_paths),
        "capture_path": str(capture_path),
        "capture_stats": capture_stats,
        "material_diagnostics": material_diagnostics,
    }
    LAST_REPORT = report
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    carb.log_info(f"Organiq exported USD import visibility check passed: {report_path}")
    print("organiq_exported_usd_import_visibility_check=ok")
    print(f"report={report_path}")
    print(f"capture={capture_path}")
    return 0


def _first_camera_path(stage, root_path: str) -> str | None:
    root_prim = stage.GetPrimAtPath(root_path)
    if not root_prim:
        return None
    for prim in Usd.PrimRange(root_prim):
        if prim.IsA(UsdGeom.Camera):
            return str(prim.GetPath())
    return None


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

    import imageio.v3 as iio
    import numpy as np

    image = iio.imread(capture_path)
    _require(int(image.max()) > int(image.min()), "viewport capture is visually blank")
    rgb = np.asarray(image[..., :3], dtype=np.float32)
    red_mask = (rgb[..., 0] > 70.0) & (rgb[..., 0] > rgb[..., 1] * 1.12) & (rgb[..., 0] > rgb[..., 2] * 1.05)
    border = np.concatenate((rgb[:24].reshape(-1, 3), rgb[-24:].reshape(-1, 3), rgb[:, :24].reshape(-1, 3), rgb[:, -24:].reshape(-1, 3)))
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
    return {
        "red_tissue_pixels": int(np.count_nonzero(red_mask)),
        "visible_tissue_pixels": int(np.count_nonzero(visible_mask)),
        "width": int(rgb.shape[1]),
        "height": int(rgb.shape[0]),
        "tissue_bbox_width_fraction": bbox_width_fraction,
        "tissue_bbox_height_fraction": bbox_height_fraction,
    }


def _mesh_diagnostics(stage, import_path: str, paths) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=False,
    )
    for path in paths:
        prim = stage.GetPrimAtPath(str(path))
        if not prim or not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        imageable = UsdGeom.Imageable(prim)
        aligned = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        world_bounds = None
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
                "relative_path": str(path).replace(import_path, "", 1),
                "label": prim.GetAttribute("organiq:labelName").Get()
                if prim.GetAttribute("organiq:labelName")
                else None,
                "role": prim.GetAttribute("organiq:role").Get() if prim.GetAttribute("organiq:role") else None,
                "points": len(mesh.GetPointsAttr().Get() or []),
                "faces": len(mesh.GetFaceVertexCountsAttr().Get() or []),
                "world_bounds": world_bounds,
                "visibility": str(imageable.ComputeVisibility()),
                "purpose": str(imageable.ComputePurpose()),
            }
        )
    return diagnostics


def _material_diagnostics(stage, paths) -> list[dict[str, object]]:
    from pxr import UsdShade

    diagnostics: list[dict[str, object]] = []
    for path in paths:
        prim = stage.GetPrimAtPath(str(path))
        if not prim:
            continue
        binding = UsdShade.MaterialBindingAPI(prim)
        visual_material = None
        physics_material = None
        try:
            visual_material = binding.ComputeBoundMaterial()[0]
        except Exception:
            visual_material = None
        try:
            physics_material = binding.ComputeBoundMaterial("physics")[0]
        except Exception:
            physics_material = None
        visual_has_mdl_surface = False
        if visual_material and visual_material.GetPrim():
            try:
                mdl_surface = visual_material.GetSurfaceOutput("mdl")
                visual_has_mdl_surface = bool(mdl_surface and mdl_surface.GetAttr())
            except Exception:
                visual_has_mdl_surface = False
        diagnostics.append(
            {
                "path": str(path),
                "visual_binding": str(visual_material.GetPath()) if visual_material and visual_material.GetPrim() else "",
                "visual_has_mdl_surface": visual_has_mdl_surface,
                "physics_binding": str(physics_material.GetPath())
                if physics_material and physics_material.GetPrim()
                else "",
                "direct_visual_targets": [str(target) for target in binding.GetDirectBindingRel().GetTargets()],
                "direct_physics_targets": [
                    str(target) for target in prim.GetRelationship("material:binding:physics").GetTargets()
                ]
                if prim.HasRelationship("material:binding:physics")
                else [],
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
        report_path = BUILD_ROOT / "organiq_exported_usd_import_visibility_check.json"
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
        carb.log_error(f"Organiq exported USD import visibility check failed: {exc}")
        print(f"organiq_exported_usd_import_visibility_check=failed: {exc}")
        print(f"report={report_path}")
    finally:
        omni.kit.app.get_app().post_uncancellable_quit(exit_code)


asyncio.ensure_future(_entry())
