from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"
ISAAC_ROOT = None
USD_LIB_PREFIXES = ("omni.usd.libs", "omni.usd.schema.physx")


def main() -> int:
    if not _ensure_pxr_available():
        return _run_under_isaac_python()

    sys.path.insert(0, str(EXT_ROOT))

    from pxr import Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

    from com.chrisvoncsefalvay.organiq.models import DistanceFieldMetadata, MeshArtifact
    from com.chrisvoncsefalvay.organiq.usd_writer import (
        export_meshes_to_usd,
        instantiate_usd_on_stage,
        preview_meshes_on_stage,
    )

    meshes = (
        _cube_mesh(MeshArtifact, DistanceFieldMetadata, 1, "bone", (0.0, 0.0, 0.0)),
        _cube_mesh(MeshArtifact, DistanceFieldMetadata, 2, "liver", (0.04, 0.0, 0.0)),
        _cube_mesh(MeshArtifact, DistanceFieldMetadata, 32760, "skin_shell", (0.08, 0.0, 0.0)),
    )
    output = REPO_ROOT / "build" / "organiq_usd_check.usd"
    result = export_meshes_to_usd(meshes, output)
    _require(result.rigid_count == 1, f"expected one rigid body, got {result.rigid_count}")
    _require(result.deformable_count == 1, f"expected one deformable body, got {result.deformable_count}")
    _verify_texture_files(output.parent / f"{output.stem}_textures")

    stage = Usd.Stage.Open(str(result.path))
    _require(stage is not None, f"could not open {result.path}")
    _verify_exported_stage(stage, UsdGeom, UsdLux, UsdPhysics, UsdShade)

    preview_stage = Usd.Stage.CreateInMemory()
    preview = preview_meshes_on_stage(preview_stage, meshes)
    _verify_preview_stage(preview_stage, preview, UsdGeom, UsdLux, UsdShade)

    single_output = REPO_ROOT / "build" / "organiq_usd_single_check.usd"
    single_result = export_meshes_to_usd((meshes[0],), single_output)
    instance_stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(instance_stage, "/World")
    preview_meshes_on_stage(instance_stage, meshes)
    _require(instance_stage.GetPrimAtPath("/World/Organiq_preview"), "preview setup failed")
    instantiate_usd_on_stage(instance_stage, single_result.path, "/World/Organiq_instance")
    instance_path = instantiate_usd_on_stage(instance_stage, result.path, "/World/Organiq_instance")
    _require(not instance_stage.GetPrimAtPath("/World/Organiq_preview"), "preview root was not removed")
    _verify_instanced_stage(instance_stage, instance_path, Sdf, UsdGeom)

    print(f"usd={result.path}")
    print(f"rigid={result.rigid_count}")
    print(f"deformable={result.deformable_count}")
    print(f"preview_camera={preview.camera_path}")
    print(f"instance={instance_path}")
    print("usd_check=ok")
    return 0


def _ensure_pxr_available() -> bool:
    _configure_isaac_usd_paths()
    try:
        from pxr import Sdf  # noqa: F401
    except Exception:
        return False
    return True


def _configure_isaac_usd_paths() -> None:
    if str(EXT_ROOT) not in sys.path:
        sys.path.insert(0, str(EXT_ROOT))
    for extension_path in _find_isaac_extensions():
        if str(extension_path) not in sys.path:
            sys.path.insert(0, str(extension_path))
        bin_path = extension_path / "bin"
        if bin_path.exists():
            os.environ["PATH"] = f"{bin_path}{os.pathsep}{os.environ.get('PATH', '')}"
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(str(bin_path))
                except OSError:
                    pass


def _find_isaac_extensions() -> list[Path]:
    isaac_root = _find_isaac_root()
    if isaac_root is None:
        return []
    extscache = isaac_root / "extscache"
    paths: list[Path] = []
    if not extscache.exists():
        return paths
    for prefix in USD_LIB_PREFIXES:
        matches = sorted(extscache.glob(f"{prefix}-*"), reverse=True)
        if matches:
            paths.append(matches[0])
    return paths


def _run_under_isaac_python() -> int:
    if os.environ.get("ORGANIQ_USD_CHECK_REEXEC") == "1":
        print("usd_check=skipped_no_pxr")
        return 0
    isaac_root = _find_isaac_root()
    if isaac_root is None:
        print("usd_check=skipped_no_isaac_root")
        return 0
    python_exe = isaac_root / "kit" / "python" / "python.exe"
    if not python_exe.exists():
        print("usd_check=skipped_no_isaac_python")
        return 0
    env = os.environ.copy()
    env["ORGANIQ_USD_CHECK_REEXEC"] = "1"
    paths = [str(EXT_ROOT), *[str(path) for path in _find_isaac_extensions()]]
    env["PYTHONPATH"] = os.pathsep.join(paths + [env.get("PYTHONPATH", "")])
    for extension_path in _find_isaac_extensions():
        bin_path = extension_path / "bin"
        if bin_path.exists():
            env["PATH"] = f"{bin_path}{os.pathsep}{env.get('PATH', '')}"
    run = subprocess.run([str(python_exe), str(Path(__file__).resolve())], env=env, check=False)
    return int(run.returncode)


def _find_isaac_root() -> Path | None:
    global ISAAC_ROOT
    if ISAAC_ROOT is not None:
        return ISAAC_ROOT

    roots: list[Path] = []
    for variable in ("ISAACSIM_ROOT", "ISAAC_SIM_ROOT"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))

    package_root_value = os.environ.get("OMNI_USER_PACKAGE_ROOT")
    package_roots = [Path(package_root_value)] if package_root_value else []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        package_roots.append(Path(local_app_data) / "ov" / "pkg")
    for package_root in package_roots:
        if package_root.exists():
            roots.extend(sorted(package_root.glob("isaac-sim*"), reverse=True))

    for root in roots:
        if (root / "kit" / "kit.exe").exists() or (root / "kit" / "python" / "python.exe").exists():
            ISAAC_ROOT = root
            return root
    return None


def _cube_mesh(mesh_type, distance_field_type, label_value: int, label_name: str, offset: tuple[float, float, float]):
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
    distance_field = distance_field_type(
        shape=(6, 6, 6),
        spacing_mm=(1.0, 1.0, 1.0),
        narrow_band_mm=12.0,
        min_distance_mm=-2.0,
        max_distance_mm=3.0,
    )
    return mesh_type(
        label_value,
        label_name,
        vertices,
        faces,
        8,
        mean_hounsfield=40.0 + float(label_value),
        distance_field=distance_field,
    )


def _verify_exported_stage(stage, UsdGeom, UsdLux, UsdPhysics, UsdShade) -> None:
    _require(stage.GetDefaultPrim().GetPath().pathString == "/World", "default prim is not /World")
    _require(abs(UsdGeom.GetStageMetersPerUnit(stage) - 1.0) < 1.0e-9, "stage is not metre-authored")
    _require(UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z, "stage is not z-up")
    _require(abs(UsdPhysics.GetStageKilogramsPerUnit(stage) - 1.0) < 1.0e-9, "stage is not kilogram-authored")

    _require(stage.GetPrimAtPath("/World/camera").IsA(UsdGeom.Camera), "export camera missing")
    _require(stage.GetPrimAtPath("/World/lighting/dome").IsA(UsdLux.DomeLight), "dome light missing")
    _require(stage.GetPrimAtPath("/World/lighting/key").IsA(UsdLux.RectLight), "key light missing")
    _require(stage.GetPrimAtPath("/World/lighting/rim").IsA(UsdLux.DistantLight), "rim light missing")
    _require(stage.GetPrimAtPath("/World/physicsScene").IsA(UsdPhysics.Scene), "physics scene missing")

    _verify_render_mesh(stage, "/World/organiq/bone/mesh", UsdGeom, UsdShade)
    _verify_render_mesh(stage, "/World/organiq/liver/mesh", UsdGeom, UsdShade)
    _verify_render_mesh(stage, "/World/organiq/skin_shell/mesh", UsdGeom, UsdShade)
    _verify_textured_material(stage, "/World/Looks/bone_material", UsdShade)
    _verify_textured_material(stage, "/World/Looks/liver_material", UsdShade)
    _verify_textured_material(stage, "/World/Looks/skin_shell_material", UsdShade)
    _verify_rigid_body(stage, "/World/organiq/bone", "/World/organiq/bone/mesh", UsdPhysics)
    _verify_deformable_body(stage, "/World/organiq/liver", "/World/organiq/liver/mesh", UsdGeom, UsdPhysics)
    _verify_surface_collision_shell(stage, "/World/organiq/skin_shell", "/World/organiq/skin_shell/mesh", UsdPhysics)


def _verify_preview_stage(stage, preview, UsdGeom, UsdLux, UsdShade) -> None:
    _require(stage.GetPrimAtPath(preview.root_path).IsValid(), "preview root missing")
    _require(stage.GetPrimAtPath(preview.camera_path).IsA(UsdGeom.Camera), "preview camera missing")
    _require(stage.GetPrimAtPath(f"{preview.root_path}/lighting/dome").IsA(UsdLux.DomeLight), "preview dome missing")
    _require(stage.GetPrimAtPath(f"{preview.root_path}/lighting/key").IsA(UsdLux.RectLight), "preview key missing")
    _require(stage.GetPrimAtPath(f"{preview.root_path}/lighting/rim").IsA(UsdLux.DistantLight), "preview rim missing")
    for mesh_path in preview.mesh_paths:
        _verify_render_mesh(stage, mesh_path, UsdGeom, UsdShade)


def _verify_instanced_stage(stage, instance_path: str, Sdf, UsdGeom) -> None:
    root = stage.GetPrimAtPath(instance_path)
    _require(root.IsValid(), "instance root missing")
    source_attr = root.GetAttribute("organiq:sourceUsd")
    _require(source_attr and source_attr.Get(), "instance source USD attribute missing")
    expected_mesh_count = root.GetAttribute("organiq:expectedMeshCount").Get()
    _require(expected_mesh_count == 3, f"expected mesh count is {expected_mesh_count}")
    camera_path = root.GetAttribute("organiq:viewCamera").Get()
    _require(camera_path == f"{instance_path}/view_camera", f"instance view camera is {camera_path}")
    _require(stage.GetPrimAtPath(camera_path).IsA(UsdGeom.Camera), "instance camera missing")
    for label_name in ("bone", "liver", "skin_shell"):
        mesh_path = f"{instance_path}/organiq/{label_name}/mesh"
        mesh_prim = stage.GetPrimAtPath(mesh_path)
        _require(mesh_prim.IsA(UsdGeom.Mesh), f"instance mesh missing at {mesh_path}")
        imageable = UsdGeom.Imageable(mesh_prim)
        _require(imageable.ComputeVisibility() == UsdGeom.Tokens.inherited, f"{mesh_path} is not visible")
        _require(imageable.ComputePurpose() == UsdGeom.Tokens.default_, f"{mesh_path} is not default purpose")


def _verify_render_mesh(stage, path: str, UsdGeom, UsdShade) -> None:
    prim = stage.GetPrimAtPath(path)
    _require(prim.IsA(UsdGeom.Mesh), f"{path} is not a mesh")
    mesh = UsdGeom.Mesh(prim)
    _require(len(mesh.GetPointsAttr().Get() or []) > 0, f"{path} has no points")
    _require(len(mesh.GetFaceVertexCountsAttr().Get() or []) > 0, f"{path} has no faces")
    imageable = UsdGeom.Imageable(prim)
    _require(imageable.ComputeVisibility() == UsdGeom.Tokens.inherited, f"{path} is not visible")
    _require(imageable.ComputePurpose() == UsdGeom.Tokens.default_, f"{path} is not default purpose")
    _require(prim.GetAttribute("organiq:meshingMethod").Get(), f"{path} has no meshing method")
    _require(prim.GetAttribute("organiq:meanHounsfield").Get() is not None, f"{path} has no mean HU")
    material_targets = UsdShade.MaterialBindingAPI(prim).GetDirectBindingRel().GetTargets()
    _require(material_targets, f"{path} has no visual material binding")
    _require(stage.GetPrimAtPath(material_targets[0]).IsValid(), f"{path} visual material target is invalid")


def _verify_rigid_body(stage, root_path: str, mesh_path: str, UsdPhysics) -> None:
    root = stage.GetPrimAtPath(root_path)
    mesh = stage.GetPrimAtPath(mesh_path)
    _require(root.HasAPI(UsdPhysics.RigidBodyAPI), "bone root has no rigid body API")
    _require(root.HasAPI(UsdPhysics.MassAPI), "bone root has no mass API")
    _require(mesh.HasAPI(UsdPhysics.CollisionAPI), "bone mesh has no collision API")
    _require(mesh.HasAPI(UsdPhysics.MeshCollisionAPI), "bone mesh has no mesh collision API")
    density = UsdPhysics.MassAPI(root).GetDensityAttr().Get()
    _require(density and density >= 1800.0, f"bone density is wrong: {density}")
    approximation = UsdPhysics.MeshCollisionAPI(mesh).GetApproximationAttr().Get()
    _require(approximation == "convexDecomposition", f"bone collision approximation is {approximation}")
    _verify_physics_material_binding(mesh, "bone mesh")


def _verify_deformable_body(stage, root_path: str, mesh_path: str, UsdGeom, UsdPhysics) -> None:
    root = stage.GetPrimAtPath(root_path)
    mesh = stage.GetPrimAtPath(mesh_path)
    physics_root = stage.GetPrimAtPath(f"{root_path}/physics")
    cooking_mesh = stage.GetPrimAtPath(f"{root_path}/physics/cooking_mesh")
    _require(physics_root.IsValid(), "liver physics proxy root missing")
    _require(cooking_mesh.IsA(UsdGeom.Mesh), "liver cooking mesh missing")
    _require(
        physics_root.GetRelationship("physxDeformableBody:cookingSourceMesh").GetTargets(),
        "liver cooking source missing",
    )
    _require(
        physics_root.GetAttribute("physxDeformable:simulationHexahedralResolution").Get(),
        "liver hex resolution missing",
    )
    _require(physics_root.GetAttribute("physxDeformableBody:resolution").Get(), "liver new hex resolution missing")
    _require(physics_root.GetAttribute("physxDeformable:numberOfTetsPerHex").Get(), "liver tets-per-hex missing")
    _require(mesh.HasAPI(UsdPhysics.CollisionAPI), "liver mesh has no collision API")
    imageable = UsdGeom.Imageable(mesh)
    _require(imageable.ComputeVisibility() == UsdGeom.Tokens.inherited, "liver visual mesh is not visible")
    _require(imageable.ComputePurpose() == UsdGeom.Tokens.default_, "liver visual mesh is not default purpose")
    proxy_imageable = UsdGeom.Imageable(physics_root)
    _require(proxy_imageable.ComputeVisibility() == UsdGeom.Tokens.invisible, "liver physics proxy is visible")
    points = mesh.GetAttribute("deformablePose:default:omniphysics:points").Get()
    purposes = mesh.GetAttribute("deformablePose:default:omniphysics:purposes").Get()
    _require(points, "liver deformable pose points missing")
    _require(purposes and "bindPose" in purposes, "liver bind pose purpose missing")
    _verify_physics_material_binding(root, "liver root")
    _verify_physics_material_binding(physics_root, "liver physics proxy")
    _verify_physics_material_binding(mesh, "liver mesh")


def _verify_surface_collision_shell(stage, root_path: str, mesh_path: str, UsdPhysics) -> None:
    root = stage.GetPrimAtPath(root_path)
    mesh = stage.GetPrimAtPath(mesh_path)
    _require(root.GetAttribute("organiq:simulationMode").Get() == "surface_shell", "skin shell mode is wrong")
    _require(not root.GetRelationship("physxDeformableBody:cookingSourceMesh").GetTargets(), "skin shell is deformable")
    _require(not root.GetAttribute("physxDeformableBody:resolution").HasAuthoredValue(), "skin shell has tet resolution")
    _require(mesh.HasAPI(UsdPhysics.CollisionAPI), "skin shell mesh has no collision API")
    _require(mesh.HasAPI(UsdPhysics.MeshCollisionAPI), "skin shell mesh has no mesh collision API")
    approximation = UsdPhysics.MeshCollisionAPI(mesh).GetApproximationAttr().Get()
    _require(approximation == "none", f"skin shell collision approximation is {approximation}")
    _require(mesh.GetAttribute("organiq:collisionShell").Get(), "skin shell marker missing")
    _verify_physics_material_binding(mesh, "skin shell mesh")


def _verify_physics_material_binding(prim, label: str) -> None:
    targets = prim.GetRelationship("material:binding:physics").GetTargets()
    _require(targets, f"{label} has no physics material binding")


def _verify_textured_material(stage, material_path: str, UsdShade) -> None:
    material = stage.GetPrimAtPath(material_path)
    _require(material.IsA(UsdShade.Material), f"{material_path} is not a material")
    preview = UsdShade.Shader(stage.GetPrimAtPath(f"{material_path}/preview"))
    _require(preview.GetPrim().IsValid(), f"{material_path} preview shader missing")
    _require(preview.GetIdAttr().Get() == "UsdPreviewSurface", f"{material_path} preview shader is wrong")
    surface_output = UsdShade.Material(material).GetSurfaceOutput()
    _require(surface_output and surface_output.HasConnectedSource(), f"{material_path} universal surface missing")
    shader = UsdShade.Shader(stage.GetPrimAtPath(f"{material_path}/shader"))
    _require(shader.GetPrim().IsValid(), f"{material_path} shader missing")
    for input_name in ("diffuse_texture", "normalmap_texture", "bump_factor"):
        value = shader.GetInput(input_name).Get()
        _require(value is not None, f"{material_path} {input_name} missing")


def _verify_texture_files(texture_dir: Path) -> None:
    expected = (
        "bone_bone_albedo.png",
        "bone_bone_normal.png",
        "liver_organ_albedo.png",
        "liver_organ_normal.png",
        "skin_shell_skin_albedo.png",
        "skin_shell_skin_normal.png",
    )
    for name in expected:
        path = texture_dir / name
        _require(path.exists(), f"missing generated texture {path}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())

