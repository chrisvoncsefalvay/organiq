from __future__ import annotations

import asyncio
import json
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

from com.chrisvoncsefalvay.organiq.models import DistanceFieldMetadata, MeshArtifact
from com.chrisvoncsefalvay.organiq.usd_writer import export_meshes_to_usd, instantiate_usd_on_stage, preview_meshes_on_stage
from com.chrisvoncsefalvay.organiq.viewport import frame_paths_next_update, renderable_mesh_paths


def _cube_mesh(label_value: int, label_name: str, offset: tuple[float, float, float]) -> MeshArtifact:
    ox, oy, oz = offset
    vertices = (
        (ox, oy, oz),
        (ox + 0.02, oy, oz),
        (ox + 0.02, oy + 0.02, oz),
        (ox, oy + 0.02, oz),
        (ox, oy, oz + 0.02),
        (ox + 0.02, oy, oz + 0.02),
        (ox + 0.02, oy + 0.02, oz + 0.02),
        (ox, oy + 0.02, oz + 0.02),
    )
    faces = (
        (0, 1, 2),
        (0, 2, 3),
        (4, 6, 5),
        (4, 7, 6),
        (0, 4, 5),
        (0, 5, 1),
        (1, 5, 6),
        (1, 6, 2),
        (2, 6, 7),
        (2, 7, 3),
        (3, 7, 4),
        (3, 4, 0),
    )
    distance_field = DistanceFieldMetadata(
        shape=(6, 6, 6),
        spacing_mm=(1.0, 1.0, 1.0),
        narrow_band_mm=12.0,
        min_distance_mm=-2.0,
        max_distance_mm=3.0,
    )
    return MeshArtifact(
        label_value,
        label_name,
        vertices,
        faces,
        8,
        mean_hounsfield=40.0 + float(label_value),
        distance_field=distance_field,
    )


async def _run() -> int:
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_ROOT / "organiq_kit_runtime_check.json"
    capture_path = BUILD_ROOT / "organiq_kit_runtime_capture.png"
    usd_path = BUILD_ROOT / "organiq_kit_runtime.usd"

    app = omni.kit.app.get_app()
    for _ in range(12):
        await app.next_update_async()

    context = omni.usd.get_context()
    await context.new_stage_async()
    stage = context.get_stage()
    _require(stage is not None, "Kit did not create a USD stage")
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    meshes = (
        _cube_mesh(1, "bone", (0.0, 0.0, 0.0)),
        _cube_mesh(2, "liver", (0.04, 0.0, 0.0)),
        _cube_mesh(32760, "skin_shell", (0.08, 0.0, 0.0)),
    )

    preview = preview_meshes_on_stage(stage, meshes)
    preview_frame = await frame_paths_next_update(preview.mesh_paths, camera_path=preview.camera_path, update_count=6)
    _require(preview_frame.camera_set, "preview did not switch the active viewport camera")
    _require(preview_frame.framed, "preview did not frame the active viewport")
    _require(set(preview.mesh_paths).issubset(set(preview_frame.selected_paths)), "preview meshes were not selected")

    export = export_meshes_to_usd(meshes, usd_path)
    instance_path = instantiate_usd_on_stage(stage, export.path, "/World/Organiq_runtime_instance")
    preview_removed = not bool(stage.GetPrimAtPath(preview.root_path))
    _require(preview_removed, "preview prims were not removed before final instantiation")
    instance_frame = await frame_paths_next_update(
        (instance_path,),
        expand_to_meshes=True,
        camera_path=stage.GetPrimAtPath(instance_path).GetAttribute("organiq:viewCamera").Get(),
        update_count=12,
    )
    _require(instance_frame.camera_set, "instance did not switch the active viewport camera")
    _require(instance_frame.framed, "instance did not frame the active viewport")
    expected_paths = {
        f"{instance_path}/organiq/bone/mesh",
        f"{instance_path}/organiq/liver/mesh",
        f"{instance_path}/organiq/skin_shell/mesh",
    }
    _require(expected_paths.issubset(set(instance_frame.selected_paths)), "instance framing did not select all meshes")
    rendered_paths = set(renderable_mesh_paths(stage, instance_path))
    _require(expected_paths.issubset(rendered_paths), "instance does not have all renderable mesh paths")
    expected_count = stage.GetPrimAtPath(instance_path).GetAttribute("organiq:expectedMeshCount").Get()
    _require(expected_count == len(meshes), f"instance expected mesh count is {expected_count}")
    _require(
        stage.GetPrimAtPath(instance_path).GetAttribute("organiq:viewCamera").Get() == f"{instance_path}/view_camera",
        "instance view camera was not authored",
    )

    capture_ok = await _capture_viewport(capture_path)
    report = {
        "status": "ok",
        "preview_root": preview.root_path,
        "preview_removed_on_instantiate": preview_removed,
        "preview_camera": preview.camera_path,
        "preview_selected_paths": list(preview_frame.selected_paths),
        "instance_path": instance_path,
        "instance_selected_paths": list(instance_frame.selected_paths),
        "instance_renderable_paths": sorted(rendered_paths),
        "expected_mesh_count": expected_count,
        "usd_path": str(export.path),
        "capture_path": str(capture_path),
        "capture_ok": capture_ok,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    carb.log_info(f"Organiq Kit runtime check passed: {report_path}")
    print("organiq_kit_runtime_check=ok")
    print(f"report={report_path}")
    print(f"capture={capture_path}")
    return 0


async def _capture_viewport(capture_path: Path) -> bool:
    from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport, next_viewport_frame_async

    viewport = get_active_viewport()
    _require(viewport is not None, "no active viewport is available")
    await next_viewport_frame_async(viewport)
    capture = capture_viewport_to_file(viewport, file_path=str(capture_path))
    _require(capture is not None, "viewport capture did not return a capture object")
    result = await capture.wait_for_result(completion_frames=60)
    _require(bool(result), "viewport capture did not complete")
    try:
        import omni.kit.renderer_capture

        omni.kit.renderer_capture.acquire_renderer_capture_interface().wait_async_capture()
    except Exception:
        pass
    _require(capture_path.exists() and capture_path.stat().st_size > 0, "viewport capture was not written")
    try:
        import imageio.v3 as iio

        image = iio.imread(capture_path)
        _require(int(image.max()) > int(image.min()), "viewport capture is visually blank")
    except ImportError:
        pass
    return True


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _entry() -> None:
    exit_code = 1
    try:
        exit_code = await _run()
    except Exception as exc:
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        report_path = BUILD_ROOT / "organiq_kit_runtime_check.json"
        report_path.write_text(
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
        carb.log_error(f"Organiq Kit runtime check failed: {exc}")
        print(f"organiq_kit_runtime_check=failed: {exc}")
        print(f"report={report_path}")
    finally:
        omni.kit.app.get_app().post_uncancellable_quit(exit_code)


asyncio.ensure_future(_entry())

