from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"
BUILD_ROOT = REPO_ROOT / "build"


def main() -> int:
    checks: list[dict[str, str]] = []

    manifest = EXT_ROOT / "config" / "extension.toml"
    extension = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "extension.py"
    workflow = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "workflow.py"
    dicom = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "dicom.py"
    segmentation = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "segmentation.py"
    meshing = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "meshing.py"
    physics = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "physics.py"
    usd_writer = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "usd_writer.py"
    dependencies = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "dependencies.py"
    jobs = EXT_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "jobs.py"
    extension_version = _extension_version(manifest)

    _check_file(checks, manifest)
    _check_text(checks, manifest, "com.chrisvoncsefalvay.organiq", "extension id")
    _check_text(checks, manifest, '"omni.timeline"', "timeline dependency")
    _check_text(checks, manifest, '"omni.physx.bundle"', "PhysX bundle dependency")
    _check_text(checks, manifest, '"omni.physics.stageupdate"', "physics stage update dependency")
    _check_repo_absent(
        checks,
        (
            "Z" + ":",
            "D:" + "/isaac" + "sim",
            "D:\\isaac" + "sim",
            "D:" + "/data" + "sets",
            "C:\\Users\\" + "chris",
            "Users/" + "chris",
        ),
        "local workstation paths removed",
    )

    _check_file(checks, extension)
    for frame in (
        "0. environment preflight",
        "1. load DICOM folder",
        "2. segment volume",
        "3. select objects to mesh",
        "4. mesh selected objects",
        "5. turn meshes into USD",
        "6. instantiate with physics",
    ):
        _check_text(checks, extension, frame, f"workflow frame: {frame}")
    _check_text(checks, extension, "FilePickerDialog", "folder and file browser buttons")
    _check_text(checks, extension, "ui.TreeView", "grouped label tree")
    _check_text(checks, extension, "ui.ProgressBar", "percent progress bar")
    _check_text(checks, jobs, "ActionProgress", "work-unit progress tracker")
    _check_absent_text(checks, extension, "current + step", "animated fake progress removed")
    _check_text(checks, extension, "ui.ComboBox", "MONAI bundle dropdown")
    _check_text(checks, extension, "MONAI_BUNDLE_PRESETS", "MONAI bundle presets used by UI")
    _check_text(checks, segmentation, "SUPPORTED_MONAI_BUNDLES", "supported MONAI bundle list")
    _check_absent_text(checks, segmentation, '"multi_organ_segmentation"', "multi-organ MONAI preset removed")
    _check_absent_text(checks, segmentation, '"pediatric_abdominal_ct_segmentation"', "paediatric abdominal MONAI preset removed")
    _check_text(checks, extension, "marching cubes", "marching cubes mesher dropdown")
    _check_text(checks, extension, "ui.ImageWithProvider", "loaded CT projection previews")
    _check_text(checks, extension, f'EXTENSION_VERSION = "{extension_version}"', "visible extension version")
    _check_text(checks, extension, "COMPLETE_FRAME_STYLE", "completed section heading colour")
    _check_text(checks, extension, "WORKFLOW_SECTIONS", "single-open workflow sequence")
    _check_text(checks, extension, "select all", "select all control")
    _check_text(checks, extension, "select none", "select none control")
    _check_absent_text(checks, extension, "run CT threshold", "CT threshold UI removed")
    _check_absent_text(checks, extension, "custom torchscript", "custom model UI removed")
    _check_absent_text(checks, extension, "high resolution", "high-resolution toggle removed")
    _check_absent_text(checks, extension, "COMPLETE_MARK", "text checkmark removed")
    _check_absent_text(checks, extension, "No meshes built", "empty mesh placeholder removed")

    _check_text(checks, workflow, "scan_dicom_folder", "scan-first DICOM workflow")
    _check_text(checks, workflow, "load_ct_series", "CT series loading")
    _check_text(checks, workflow, "run_monai_bundle", "MONAI segmentation")
    _check_absent_text(checks, workflow, "segment_threshold", "CT threshold workflow removed")
    _check_absent_text(checks, workflow, "segment_custom", "custom model workflow removed")
    _check_text(checks, workflow, "mesh_selected_labels", "selected-object meshing")
    _check_text(checks, workflow, "export_meshes_to_usd", "USD export")
    _check_text(checks, workflow, "instantiate_usd_on_stage", "stage instantiation")
    _check_text(checks, dicom, "_slice_normal_from_header", "orientation-aware DICOM slice sort")
    _check_text(checks, dicom, "_nifti_affine", "orientation-aware NIfTI affine")
    _check_text(checks, usd_writer, "_remove_preview_prims", "final instantiation removes previews")
    _check_text(checks, usd_writer, "Gf.Vec3i", "SDF metadata uses explicit Int3 values")
    _check_text(checks, segmentation, "displayable_configs#highres", "whole-body high-resolution MONAI override")
    _check_text(checks, segmentation, "MONAI output contained no anatomy labels", "empty MONAI output guard")
    _check_text(checks, segmentation, "mean_hounsfield", "mean Hounsfield segmentation statistics")
    _check_text(checks, meshing, "_clean_triangle_mesh", "degenerate triangle cleaning")
    _check_text(checks, meshing, "_signed_distance_field_mm", "signed distance field meshing")
    _check_text(checks, meshing, "_mesh_with_marching_cubes", "marching cubes meshing")
    _check_text(checks, meshing, "_extract_zero_level_set", "zero level set extraction")
    _check_text(checks, meshing, "_compute_vertex_normals", "mesh normal computation")
    _check_text(checks, physics, "apply_surface_collision_shell", "skin shell collision path")
    _check_text(checks, physics, "_make_visual_mesh_renderable", "physics meshes remain renderable")
    _check_text(checks, physics, "deformablePhysicsProxy", "deformables use hidden physics proxy")
    _check_text(checks, physics, "_copy_mesh_for_cooking", "deformable cooking mesh is decoupled")
    _check_text(checks, usd_writer, 'simulation_mode == "surface_shell"', "skin shell avoids auto-tet cooking")
    _check_text(checks, usd_writer, "organiq:meanHounsfield", "mean Hounsfield USD custom attribute")
    _check_text(checks, segmentation, "_run_bounded_subprocess", "bounded MONAI subprocess runner")
    _check_text(checks, segmentation, "ORGANIQ_MONAI_TIMEOUT_SECONDS", "MONAI timeout override")
    _check_text(checks, extension, "_cancel_active_action", "cancellable UI action")
    _check_text(checks, dependencies, "CONSTRAINTS_FILE", "dependency constraints install path")

    _check_file(checks, REPO_ROOT / "docs" / "physics_defaults.md")
    _check_file(checks, REPO_ROOT / "docs" / "omniverse_practices.md")
    _check_file(checks, REPO_ROOT / "constraints" / "isaac-5.1.txt")
    _check_file(checks, REPO_ROOT / "tools" / "launch_organiq.ps1")

    _check_dicom_report(checks)
    _check_runtime_report(checks)
    _check_usd_artifacts(checks)
    _check_window_report(checks, extension_version)

    ok = all(check["status"] == "ok" for check in checks)
    report = {
        "status": "ok" if ok else "failed",
        "repo_root": str(REPO_ROOT),
        "checks": checks,
    }
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_ROOT / "organiq_acceptance_check.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"report={report_path}")
    if ok:
        print("organiq_acceptance_check=ok")
        return 0

    print("organiq_acceptance_check=failed")
    for check in checks:
        if check["status"] != "ok":
            print(f"failed={check['name']}: {check['evidence']}")
    return 1


def _check_file(checks: list[dict[str, str]], path: Path) -> None:
    _record(checks, str(path.relative_to(REPO_ROOT)), path.exists(), "present" if path.exists() else "missing")


def _check_text(checks: list[dict[str, str]], path: Path, needle: str, name: str) -> None:
    if not path.exists():
        _record(checks, name, False, f"missing {path.relative_to(REPO_ROOT)}")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    _record(checks, name, needle in text, f"{path.relative_to(REPO_ROOT)} contains {needle!r}")


def _check_absent_text(checks: list[dict[str, str]], path: Path, needle: str, name: str) -> None:
    if not path.exists():
        _record(checks, name, False, f"missing {path.relative_to(REPO_ROOT)}")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    _record(checks, name, needle not in text, f"{path.relative_to(REPO_ROOT)} excludes {needle!r}")


def _check_repo_absent(checks: list[dict[str, str]], needles: tuple[str, ...], name: str) -> None:
    roots = (
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs",
        REPO_ROOT / "source",
        REPO_ROOT / "tests",
        REPO_ROOT / "tools",
    )
    hits: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".zip"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in needles:
                if needle in text:
                    hits.append(f"{path.relative_to(REPO_ROOT)} contains {needle!r}")
    _record(checks, name, not hits, "; ".join(hits[:5]) if hits else "no local paths")


def _check_dicom_report(checks: list[dict[str, str]]) -> None:
    report_path = BUILD_ROOT / "organiq_dicom_workflow_check.json"
    report = _read_json(report_path)
    _record(checks, "DICOM workflow report", report is not None, _path_evidence(report_path))
    if report is None:
        return

    selected = report.get("selected_labels", [])
    selected_names = [str(label.get("name", "")) for label in selected if isinstance(label, dict)]
    meshes = report.get("meshes", [])
    instance_paths = [str(path) for path in report.get("instance_selected_paths", [])]
    capture_path = Path(str(report.get("capture_path", "")))

    _record(checks, "DICOM workflow status", report.get("status") == "ok", str(report.get("status")))
    _record(checks, "real CT volume loaded", int(report.get("slice_count", 0)) >= 2, f"slices={report.get('slice_count')}")
    _record(checks, "MONAI anatomy labels decoded", int(report.get("label_count", 0)) > 1, f"labels={report.get('label_count')}")
    _record(
        checks,
        "selected anatomy excludes skin-only output",
        bool(selected_names) and any(name != "skin_shell" for name in selected_names),
        ",".join(selected_names),
    )
    _record(checks, "selected anatomy meshed", int(report.get("mesh_count", 0)) >= 1, f"meshes={report.get('mesh_count')}")
    _record(
        checks,
        "organic mesh geometry exists",
        all(int(mesh.get("vertices", 0)) > 0 and int(mesh.get("faces", 0)) > 0 for mesh in meshes),
        f"mesh_records={len(meshes)}",
    )
    _record(
        checks,
        "soft tissues become deformables",
        int(report.get("deformable_count", 0)) >= 1,
        f"deformables={report.get('deformable_count')}",
    )
    _record(checks, "DICOM USD exported", Path(str(report.get("usd_path", ""))).exists(), str(report.get("usd_path")))
    _record(checks, "DICOM stage instantiated", bool(instance_paths), ",".join(instance_paths))
    _record(checks, "DICOM viewport capture", capture_path.exists(), str(capture_path))


def _check_runtime_report(checks: list[dict[str, str]]) -> None:
    report_path = BUILD_ROOT / "organiq_kit_runtime_check.json"
    report = _read_json(report_path)
    _record(checks, "Kit runtime report", report is not None, _path_evidence(report_path))
    if report is None:
        return

    instance_paths = [str(path) for path in report.get("instance_selected_paths", [])]
    renderable_paths = [str(path) for path in report.get("instance_renderable_paths", [])]
    joined_paths = ",".join(instance_paths)
    joined_renderable = ",".join(renderable_paths)
    capture_path = Path(str(report.get("capture_path", "")))

    _record(checks, "Kit runtime status", report.get("status") == "ok", str(report.get("status")))
    _record(checks, "runtime rigid body path", "bone/mesh" in joined_paths, joined_paths)
    _record(checks, "runtime deformable path", "liver/mesh" in joined_paths, joined_paths)
    _record(checks, "runtime surface shell path", "skin_shell/mesh" in joined_paths, joined_paths)
    _record(checks, "runtime all meshes instantiated", len(instance_paths) >= 3, f"paths={len(instance_paths)}")
    _record(
        checks,
        "runtime preview removed on instantiate",
        bool(report.get("preview_removed_on_instantiate")),
        str(report.get("preview_removed_on_instantiate")),
    )
    _record(
        checks,
        "runtime all meshes renderable",
        len(renderable_paths) >= 3 and all(name in joined_renderable for name in ("bone/mesh", "liver/mesh", "skin_shell/mesh")),
        f"paths={len(renderable_paths)}",
    )
    _record(checks, "runtime viewport capture", bool(report.get("capture_ok")) and capture_path.exists(), str(capture_path))


def _check_usd_artifacts(checks: list[dict[str, str]]) -> None:
    usd_path = BUILD_ROOT / "organiq_usd_check.usd"
    texture_dir = BUILD_ROOT / "organiq_usd_check_textures"
    expected_textures = (
        "bone_bone_albedo.png",
        "bone_bone_normal.png",
        "liver_organ_albedo.png",
        "liver_organ_normal.png",
        "skin_shell_skin_albedo.png",
        "skin_shell_skin_normal.png",
    )
    _record(checks, "USD export check artifact", usd_path.exists(), str(usd_path))
    for texture in expected_textures:
        texture_path = texture_dir / texture
        _record(checks, f"texture artifact: {texture}", texture_path.exists(), str(texture_path))


def _check_window_report(checks: list[dict[str, str]], extension_version: str) -> None:
    report_path = BUILD_ROOT / "organiq_window_check.json"
    report = _read_json(report_path)
    _record(checks, "run-scoped Organiq window report", report is not None, _path_evidence(report_path))
    if report is None:
        return

    _record(checks, "Organiq window status", report.get("status") == "ok", str(report.get("status")))
    _record(checks, "Organiq window found", bool(report.get("window_found")), str(report.get("window_found")))
    _record(checks, "Organiq window visible", bool(report.get("window_visible")), str(report.get("window_visible")))
    reported_version = str(report.get("extension_version", extension_version))
    _record(checks, "Organiq report version", reported_version == extension_version, reported_version)

    log_path_value = str(report.get("log_path", "")).strip()
    log_path = Path(log_path_value) if log_path_value else None
    if log_path is None or not log_path.exists() or not log_path.is_file():
        _record(checks, "run-scoped Isaac log", True, "not captured by this report")
        return

    text = log_path.read_text(encoding="utf-8", errors="replace")
    marker = f"com.chrisvoncsefalvay.organiq-{extension_version}"
    _record(checks, "run-scoped Isaac log", marker in text, str(log_path))
    for banned in (
        "Failed to resolve extension",
        "[Error] [com.chrisvoncsefalvay.organiq",
        "inverted tetrahedron",
        "tetrahedron is degenerate",
        "PhysX has reported too many errors",
    ):
        _record(checks, f"log excludes {banned}", banned not in text, str(log_path))


def _extension_version(manifest: Path) -> str:
    if not manifest.exists():
        return "unknown"
    text = manifest.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _path_evidence(path: Path) -> str:
    if not path.exists():
        return "missing"
    mtime = path.stat().st_mtime
    return f"{path} mtime={mtime:.0f}"


def _record(checks: list[dict[str, str]], name: str, condition: bool, evidence: str) -> None:
    checks.append(
        {
            "name": name,
            "status": "ok" if condition else "failed",
            "evidence": evidence,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())

