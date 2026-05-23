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

from com.chrisvoncsefalvay.organiq.paths import DEFAULT_MODEL_ROOT
from com.chrisvoncsefalvay.organiq.segmentation import DEFAULT_MONAI_BUNDLE
from com.chrisvoncsefalvay.organiq.usd_writer import preview_meshes_on_stage
from com.chrisvoncsefalvay.organiq.viewport import frame_paths_next_update
from com.chrisvoncsefalvay.organiq.workflow import OrganiqWorkflow


async def _run() -> int:
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_ROOT / "organiq_dicom_workflow_check.json"
    capture_path = BUILD_ROOT / "organiq_dicom_workflow_capture.png"
    usd_path = BUILD_ROOT / "organiq_dicom_workflow.usd"
    dicom_folder_value = os.environ.get("ORGANIQ_DICOM_FOLDER")
    _require(dicom_folder_value, "Set ORGANIQ_DICOM_FOLDER to a CT DICOM folder")
    dicom_folder = Path(dicom_folder_value)
    _require(dicom_folder.exists(), f"DICOM folder does not exist: {dicom_folder}")

    app = omni.kit.app.get_app()
    for _ in range(12):
        await app.next_update_async()

    context = omni.usd.get_context()
    await context.new_stage_async()
    stage = context.get_stage()
    _require(stage is not None, "Kit did not create a USD stage")
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    workflow = OrganiqWorkflow()
    series = workflow.scan(dicom_folder)
    _require(series, "No CT series were found")
    volume = workflow.load_series(series[0].series_uid)
    segmentation = workflow.segment_monai(DEFAULT_MONAI_BUNDLE, DEFAULT_MODEL_ROOT, highres=False)
    _require(segmentation.labels, "MONAI produced no non-background labels")
    anatomy_labels = [label for label in segmentation.labels if label.name != "skin_shell"]
    _require(anatomy_labels, "MONAI produced only the synthetic skin shell")

    selected_values = _select_label_values(segmentation)
    workflow.selected_label_values = set(selected_values)
    meshes = workflow.mesh_selected(smooth=True)
    _require(meshes, "No meshes were produced from selected labels")

    preview = preview_meshes_on_stage(stage, meshes)
    preview_frame = await frame_paths_next_update(preview.mesh_paths, camera_path=preview.camera_path, update_count=6)
    _require(preview_frame.camera_set, "preview did not switch the viewport camera")
    _require(preview_frame.framed, "preview did not frame the viewport")

    export = workflow.export_usd(usd_path)
    instance_path = workflow.instantiate(stage, "/World/Organiq_dicom_instance")
    instance_frame = await frame_paths_next_update(
        (instance_path,),
        expand_to_meshes=True,
        camera_path=stage.GetPrimAtPath(instance_path).GetAttribute("organiq:viewCamera").Get(),
        update_count=12,
    )
    _require(instance_frame.camera_set, "instance did not switch the viewport camera")
    _require(instance_frame.framed, "instance did not frame the viewport")
    _require(instance_frame.selected_paths, "instance framing selected no paths")
    await _capture_viewport(capture_path)

    report = {
        "status": "ok",
        "dicom_folder": str(dicom_folder),
        "series_uid": series[0].series_uid,
        "slice_count": volume.series.file_count if volume.series else None,
        "volume_shape": list(volume.data.shape),
        "spacing_mm": list(volume.spacing_mm),
        "segmentation_source": segmentation.source,
        "segmentation_output": str(segmentation.output_path) if segmentation.output_path else None,
        "label_count": len(segmentation.labels),
        "selected_labels": [
            {
                "value": label.value,
                "name": label.name,
                "voxels": label.voxel_count,
                "mean_hounsfield": label.mean_hounsfield,
            }
            for label in segmentation.labels
            if label.value in selected_values
        ],
        "mesh_count": len(meshes),
        "meshes": [
            {
                "label_value": mesh.label_value,
                "label_name": mesh.label_name,
                "vertices": len(mesh.vertices_m),
                "faces": len(mesh.faces),
                "mean_hounsfield": mesh.mean_hounsfield,
            }
            for mesh in meshes
        ],
        "usd_path": str(export.path),
        "rigid_count": export.rigid_count,
        "deformable_count": export.deformable_count,
        "preview_camera": preview.camera_path,
        "preview_selected_paths": list(preview_frame.selected_paths),
        "instance_path": instance_path,
        "instance_selected_paths": list(instance_frame.selected_paths),
        "capture_path": str(capture_path),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    carb.log_info(f"Organiq DICOM workflow check passed: {report_path}")
    print("organiq_dicom_workflow_check=ok")
    print(f"report={report_path}")
    print(f"capture={capture_path}")
    return 0


def _select_label_values(segmentation) -> tuple[int, ...]:
    priorities = (
        "liver",
        "kidney_right",
        "kidney_left",
        "spleen",
        "lung_upper_lobe_left",
        "lung_lower_lobe_left",
        "vertebrae_L1",
        "rib_left_8",
    )
    by_name = {label.name: label for label in segmentation.labels if label.name != "skin_shell"}
    selected = [by_name[name].value for name in priorities if name in by_name]
    if selected:
        return tuple(selected[:3])
    labels = sorted(
        (label for label in segmentation.labels if label.name != "skin_shell"),
        key=lambda label: label.voxel_count,
        reverse=True,
    )
    return tuple(label.value for label in labels[:3])


async def _capture_viewport(capture_path: Path) -> None:
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
    try:
        import imageio.v3 as iio

        image = iio.imread(capture_path)
        _require(int(image.max()) > int(image.min()), "viewport capture is visually blank")
    except ImportError:
        pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _entry() -> None:
    exit_code = 1
    try:
        exit_code = await _run()
    except Exception as exc:
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        report_path = BUILD_ROOT / "organiq_dicom_workflow_check.json"
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
        carb.log_error(f"Organiq DICOM workflow check failed: {exc}")
        print(f"organiq_dicom_workflow_check=failed: {exc}")
        print(f"report={report_path}")
    finally:
        omni.kit.app.get_app().post_uncancellable_quit(exit_code)


asyncio.ensure_future(_entry())

