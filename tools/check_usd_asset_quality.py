from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"
BUILD_ROOT = REPO_ROOT / "build"
REPORT_PATH = BUILD_ROOT / "organiq_usd_asset_quality_check.json"
ISAAC_ROOT = None
USD_LIB_PREFIXES = ("omni.usd.libs", "omni.usd.schema.physx")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not _ensure_pxr_available():
        return _run_under_isaac_python(args)

    from pxr import Kind, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    usd_path = Path(args.usd).expanduser().resolve()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    backup_path = None
    if args.repair and usd_path.exists():
        backup_path = _backup_path(usd_path)
        shutil.copy2(usd_path, backup_path)

    stage = Usd.Stage.Open(str(usd_path)) if usd_path.exists() else None
    repair_report: dict[str, Any] = {"repaired": False}
    if args.repair and stage is not None:
        repair_report = _repair_stage(stage, usd_path, Kind, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade)

    if args.repair:
        stage = Usd.Stage.Open(str(usd_path)) if usd_path.exists() else None
    quality = _analyse_stage(stage, usd_path, Kind, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade)
    quality["repair"] = repair_report
    if backup_path is not None:
        quality["backup_path"] = str(backup_path)

    report_path.write_text(json.dumps(quality, indent=2), encoding="utf-8")
    print(f"report={report_path}")
    print(f"organiq_usd_asset_quality_check={quality['status']}")
    return 0 if quality["status"] == "ok" else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check or repair an Organiq USD asset")
    parser.add_argument("--usd", default=str(_default_usd_path()), help="USD file to check")
    parser.add_argument("--report", default=str(REPORT_PATH), help="JSON report path")
    parser.add_argument("--repair", action="store_true", help="repair old Organiq USD layout in place")
    return parser.parse_args(argv)


def _default_usd_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Organiq" / "outputs" / "organiq_scene.usd"
    return Path.home() / ".cache" / "organiq" / "outputs" / "organiq_scene.usd"


def _repair_stage(stage, usd_path: Path, Kind, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade) -> dict[str, Any]:
    root_layer = stage.GetRootLayer()
    component_path = Sdf.Path("/World/organiq")
    component = stage.GetPrimAtPath(str(component_path))
    changed = False
    changes: list[str] = []

    if not component.IsValid():
        return {"repaired": False, "changes": changes, "reason": "/World/organiq missing"}

    if not stage.GetDefaultPrim():
        world = stage.GetPrimAtPath("/World")
        if world.IsValid():
            stage.SetDefaultPrim(world)
            changed = _note(changes, changed, "set /World as default prim")

    world = stage.GetPrimAtPath("/World")
    if world.IsValid():
        _author_model_metadata(world, "organiq", "assembly", Kind.Tokens.assembly, usd_path, Sdf, Usd)
        changed = _note(changes, changed, "author world model metadata")

    _author_model_metadata(component, "organiq", "anatomyComponent", Kind.Tokens.component, usd_path, Sdf, Usd)
    component.CreateAttribute("organiq:instanceableReferenceTarget", Sdf.ValueTypeNames.Bool).Set(True)
    changed = _note(changes, changed, "author anatomy component metadata")

    bounds = _label_mesh_bounds(stage, str(component_path), Usd, UsdGeom)
    _author_extents_hint(world, bounds, Gf=None, UsdGeom=UsdGeom)
    _author_extents_hint(component, bounds, Gf=None, UsdGeom=UsdGeom)
    meshes = _label_meshes(stage, str(component_path), Usd, UsdGeom)
    component.CreateAttribute("organiq:meshCount", Sdf.ValueTypeNames.Int).Set(len(meshes))
    changed = _note(changes, changed, "author extents and mesh count")

    for scope_name in ("Looks", "PhysicsMaterials"):
        source = Sdf.Path(f"/World/{scope_name}")
        target = Sdf.Path(f"{component_path}/{scope_name}")
        if stage.GetPrimAtPath(str(source)).IsValid() and not stage.GetPrimAtPath(str(target)).IsValid():
            Sdf.CopySpec(root_layer, source, root_layer, target)
            changed = _note(changes, changed, f"move {scope_name} into anatomy component")

    for prim in list(stage.Traverse()):
        for relationship in prim.GetRelationships():
            targets = relationship.GetTargets()
            rewritten = [_retarget_component_path(target, Sdf) for target in targets]
            if rewritten != list(targets):
                relationship.SetTargets(rewritten)
                changed = _note(changes, changed, f"retarget {relationship.GetPath()}")

    _bind_full_quality_materials(stage, str(component_path), Usd, UsdGeom, UsdShade)
    changed = _note(changes, changed, "bind full-quality textured materials")
    if _repair_surface_deformables(stage, str(component_path), Sdf, Usd, UsdGeom, UsdPhysics):
        changed = _note(changes, changed, "repair old volume deformables as surface deformables")

    for prim in stage.Traverse():
        for attr in prim.GetAttributes():
            value = attr.Get()
            new_value = _relative_asset(value, usd_path.parent, Sdf)
            if new_value is not None and new_value != value:
                attr.Set(new_value)
                changed = _note(changes, changed, f"normalise asset path {attr.GetPath()}")

    for old_scope in ("/World/Looks", "/World/PhysicsMaterials"):
        if stage.GetPrimAtPath(old_scope).IsValid():
            stage.RemovePrim(old_scope)
            changed = _note(changes, changed, f"remove {old_scope}")

    if changed:
        stage.GetRootLayer().Save()
    return {"repaired": changed, "changes": changes}


def _analyse_stage(stage, usd_path: Path, Kind, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    _record(checks, "USD file exists", usd_path.exists(), str(usd_path))
    _record(checks, "USD stage opens", stage is not None, str(usd_path))
    if stage is None:
        return _report(usd_path, checks)

    world = stage.GetPrimAtPath("/World")
    component = stage.GetPrimAtPath("/World/organiq")
    meshes = _label_meshes(stage, "/World/organiq", Usd, UsdGeom)

    _record(checks, "default prim is /World", bool(stage.GetDefaultPrim()) and str(stage.GetDefaultPrim().GetPath()) == "/World", str(stage.GetDefaultPrim().GetPath()) if stage.GetDefaultPrim() else "")
    _record(checks, "world is an assembly", world.GetMetadata("kind") == Kind.Tokens.assembly, str(world.GetMetadata("kind")))
    _record(checks, "anatomy component exists", component.IsValid(), str(component.GetPath()) if component else "missing")
    _record(checks, "anatomy is a component", component.GetMetadata("kind") == Kind.Tokens.component, str(component.GetMetadata("kind")))
    _record(checks, "anatomy role is component", _attr_value(component, "organiq:assetRole") == "anatomyComponent", str(_attr_value(component, "organiq:assetRole")))
    _record(checks, "mesh count is authored", int(_attr_value(component, "organiq:meshCount") or 0) == len(meshes) and len(meshes) > 0, f"authored={_attr_value(component, 'organiq:meshCount')} actual={len(meshes)}")
    _record(checks, "component-local Looks scope", _prim_is_a(stage, "/World/organiq/Looks", UsdGeom.Scope), "/World/organiq/Looks")
    _record(checks, "component-local PhysicsMaterials scope", _prim_is_a(stage, "/World/organiq/PhysicsMaterials", UsdGeom.Scope), "/World/organiq/PhysicsMaterials")
    _record(checks, "no top-level Looks scope", not stage.GetPrimAtPath("/World/Looks").IsValid(), "/World/Looks")
    _record(checks, "no top-level PhysicsMaterials scope", not stage.GetPrimAtPath("/World/PhysicsMaterials").IsValid(), "/World/PhysicsMaterials")
    _record(checks, "physics scene exists", _prim_is_a(stage, "/World/physicsScene", UsdPhysics.Scene), "/World/physicsScene")
    _record(checks, "anatomy extents hint exists", component.HasAPI(UsdGeom.ModelAPI), str(component.GetPath()))

    bad_relationships = _out_of_component_relationships(stage, "/World/organiq", Usd, Sdf)
    _record(checks, "component relationships stay in component scope", not bad_relationships, "; ".join(bad_relationships[:5]) if bad_relationships else "ok")

    material_failures = _material_failures(stage, meshes, UsdShade)
    _record(checks, "mesh materials are local and textured", not material_failures, "; ".join(material_failures[:5]) if material_failures else "ok")

    physics_failures = _physics_failures(stage, meshes, UsdShade)
    _record(checks, "mesh physics bindings are local", not physics_failures, "; ".join(physics_failures[:5]) if physics_failures else "ok")

    asset_failures = _asset_failures(stage, "/World/organiq", usd_path.parent, Usd, Sdf)
    _record(checks, "texture assets are relative and present", not asset_failures, "; ".join(asset_failures[:5]) if asset_failures else "ok")

    sim_failures = _sim_failures(stage, meshes, UsdPhysics)
    _record(checks, "simulation authoring avoids volume tet cooking", not sim_failures, "; ".join(sim_failures[:5]) if sim_failures else "ok")

    instance_report = _check_instanceable_reference(usd_path, len(meshes), Usd, UsdGeom, Sdf)
    _record(checks, "component composes as an instanceable reference", instance_report["ok"], instance_report["evidence"])

    return _report(usd_path, checks)


def _report(usd_path: Path, checks: list[dict[str, str]]) -> dict[str, Any]:
    ok = all(check["status"] == "ok" for check in checks)
    return {
        "status": "ok" if ok else "failed",
        "usd_path": str(usd_path),
        "checks": checks,
    }


def _label_meshes(stage, component_path: str, Usd, UsdGeom) -> list[Any]:
    root = stage.GetPrimAtPath(component_path)
    if not root:
        return []
    meshes = []
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
        if prim.IsA(UsdGeom.Mesh) and prim.GetAttribute("organiq:labelName").Get():
            meshes.append(prim)
    return meshes


def _label_mesh_bounds(stage, component_path: str, Usd, UsdGeom):
    meshes = _label_meshes(stage, component_path, Usd, UsdGeom)
    points: list[tuple[float, float, float]] = []
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=False,
    )
    for prim in meshes:
        aligned = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if aligned.IsEmpty():
            continue
        min_pt = aligned.GetMin()
        max_pt = aligned.GetMax()
        points.extend(((float(min_pt[0]), float(min_pt[1]), float(min_pt[2])), (float(max_pt[0]), float(max_pt[1]), float(max_pt[2]))))
    if not points:
        return ((-0.05, -0.05, -0.05), (0.05, 0.05, 0.05))
    return (
        (min(point[0] for point in points), min(point[1] for point in points), min(point[2] for point in points)),
        (max(point[0] for point in points), max(point[1] for point in points), max(point[2] for point in points)),
    )


def _author_model_metadata(prim, scene_name: str, role: str, kind_token, usd_path: Path, Sdf, Usd) -> None:
    model_api = Usd.ModelAPI(prim)
    model_api.SetKind(kind_token)
    model_api.SetAssetName(scene_name)
    model_api.SetAssetVersion("0.1.0")
    prim.CreateAttribute("organiq:assetRole", Sdf.ValueTypeNames.Token).Set(role)
    prim.CreateAttribute("organiq:assetFileName", Sdf.ValueTypeNames.String).Set(usd_path.name)


def _author_extents_hint(prim, bounds, Gf, UsdGeom) -> None:
    if not prim or not prim.IsValid():
        return
    from pxr import Gf as PxrGf

    model_api = UsdGeom.ModelAPI.Apply(prim)
    model_api.SetExtentsHint([PxrGf.Vec3f(*bounds[0]), PxrGf.Vec3f(*bounds[1])])


def _bind_full_quality_materials(stage, component_path: str, Usd, UsdGeom, UsdShade) -> None:
    for mesh in _label_meshes(stage, component_path, Usd, UsdGeom):
        binding_api = UsdShade.MaterialBindingAPI.Apply(mesh)
        targets = binding_api.GetDirectBindingRel().GetTargets()
        if not targets:
            continue
        material_prim = stage.GetPrimAtPath(str(targets[0]))
        if not material_prim:
            continue
        textured_targets = material_prim.GetRelationship("organiq:texturedMaterial").GetTargets()
        if not textured_targets:
            continue
        textured_material = UsdShade.Material(stage.GetPrimAtPath(str(textured_targets[0])))
        if textured_material and textured_material.GetPrim().IsValid():
            binding_api.Bind(textured_material, UsdShade.Tokens.strongerThanDescendants, "full")


def _repair_surface_deformables(stage, component_path: str, Sdf, Usd, UsdGeom, UsdPhysics) -> bool:
    changed = False
    for mesh in _label_meshes(stage, component_path, Usd, UsdGeom):
        if _attr_value(mesh, "organiq:simulationMode") != "deformable":
            continue
        parent = mesh.GetParent()
        if not parent:
            continue
        for prim in (parent, mesh):
            prim.CreateAttribute("organiq:deformableAuthoring", Sdf.ValueTypeNames.Token).Set("surface_deformable")
            prim.CreateAttribute("organiq:volumeTetCooking", Sdf.ValueTypeNames.Bool).Set(False)
            prim.CreateAttribute("physxDeformable:solverPositionIterationCount", Sdf.ValueTypeNames.Int).Set(32)
            if not prim.GetAttribute("physxDeformable:vertexVelocityDamping").HasAuthoredValue():
                prim.CreateAttribute("physxDeformable:vertexVelocityDamping", Sdf.ValueTypeNames.Float).Set(0.1)
            for property_name in (
                "physxDeformableBody:cookingSourceMesh",
                "physxDeformableBody:resolution",
                "physxDeformable:numberOfTetsPerHex",
                "physxDeformable:simulationHexahedralResolution",
            ):
                if prim.HasProperty(property_name):
                    prim.RemoveProperty(property_name)
        UsdPhysics.CollisionAPI.Apply(mesh)
        _author_surface_rest_shape(mesh, Sdf, UsdGeom)
        _author_visual_deformable_pose(mesh, Sdf, UsdGeom)
        physics_path = f"{parent.GetPath()}/physics"
        if stage.GetPrimAtPath(physics_path).IsValid():
            stage.RemovePrim(physics_path)
        changed = True
    return changed


def _author_visual_deformable_pose(mesh_prim, Sdf, UsdGeom) -> None:
    points = UsdGeom.PointBased(mesh_prim).GetPointsAttr().Get()
    if points:
        mesh_prim.CreateAttribute("deformablePose:default:omniphysics:points", Sdf.ValueTypeNames.Point3fArray).Set(points)
    mesh_prim.CreateAttribute("deformablePose:default:omniphysics:purposes", Sdf.ValueTypeNames.TokenArray).Set(
        ["bindPose"]
    )


def _author_surface_rest_shape(mesh_prim, Sdf, UsdGeom) -> None:
    from pxr import Gf

    mesh = UsdGeom.Mesh(mesh_prim)
    points = mesh.GetPointsAttr().Get()
    face_counts = mesh.GetFaceVertexCountsAttr().Get()
    face_indices = mesh.GetFaceVertexIndicesAttr().Get()
    if not points or not face_counts or not face_indices:
        return
    tri_indices = []
    cursor = 0
    for face_count in face_counts:
        count = int(face_count)
        if count != 3:
            return
        tri_indices.append(
            Gf.Vec3i(
                int(face_indices[cursor]),
                int(face_indices[cursor + 1]),
                int(face_indices[cursor + 2]),
            )
        )
        cursor += count
    if tri_indices:
        mesh_prim.CreateAttribute("omniphysics:restShapePoints", Sdf.ValueTypeNames.Point3fArray).Set(points)
        mesh_prim.CreateAttribute("omniphysics:restTriVtxIndices", Sdf.ValueTypeNames.Int3Array).Set(tri_indices)


def _retarget_component_path(path, Sdf):
    value = str(path)
    for old, new in (
        ("/World/Looks", "/World/organiq/Looks"),
        ("/World/PhysicsMaterials", "/World/organiq/PhysicsMaterials"),
    ):
        if value == old or value.startswith(f"{old}/"):
            return Sdf.Path(value.replace(old, new, 1))
    return path


def _relative_asset(value, anchor: Path, Sdf):
    if not isinstance(value, Sdf.AssetPath):
        return None
    path = value.path
    if not path or (":" not in path and "\\" not in path and not path.startswith("/")):
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(path.replace("\\", "/"))
    try:
        relative = os.path.relpath(candidate.resolve(), anchor.resolve())
    except Exception:
        return None
    return Sdf.AssetPath(Path(relative).as_posix())


def _out_of_component_relationships(stage, component_path: str, Usd, Sdf) -> list[str]:
    failures: list[str] = []
    root = stage.GetPrimAtPath(component_path)
    if not root:
        return [f"{component_path} missing"]
    for prim in Usd.PrimRange(root):
        for relationship in prim.GetRelationships():
            for target in relationship.GetTargets():
                text = str(target)
                if text.startswith("/World/Looks") or text.startswith("/World/PhysicsMaterials"):
                    failures.append(f"{relationship.GetPath()} -> {text}")
    return failures


def _material_failures(stage, meshes: list[Any], UsdShade) -> list[str]:
    failures: list[str] = []
    for mesh in meshes:
        binding_api = UsdShade.MaterialBindingAPI(mesh)
        direct_targets = [str(target) for target in binding_api.GetDirectBindingRel().GetTargets()]
        full_targets = [str(target) for target in mesh.GetRelationship("material:binding:full").GetTargets()]
        if not direct_targets or not direct_targets[0].startswith("/World/organiq/Looks/"):
            failures.append(f"{mesh.GetPath()} visual target {direct_targets}")
        elif not stage.GetPrimAtPath(direct_targets[0]).IsValid():
            failures.append(f"{mesh.GetPath()} visual target invalid")
        if not full_targets or not full_targets[0].startswith("/World/organiq/Looks/"):
            failures.append(f"{mesh.GetPath()} full target {full_targets}")
        elif not stage.GetPrimAtPath(full_targets[0]).IsValid():
            failures.append(f"{mesh.GetPath()} full target invalid")
    return failures


def _physics_failures(stage, meshes: list[Any], UsdShade) -> list[str]:
    failures: list[str] = []
    for mesh in meshes:
        physics_targets = [str(target) for target in mesh.GetRelationship("material:binding:physics").GetTargets()]
        if not physics_targets or not physics_targets[0].startswith("/World/organiq/PhysicsMaterials/"):
            failures.append(f"{mesh.GetPath()} physics target {physics_targets}")
        elif not stage.GetPrimAtPath(physics_targets[0]).IsValid():
            failures.append(f"{mesh.GetPath()} physics target invalid")
    return failures


def _asset_failures(stage, component_path: str, anchor: Path, Usd, Sdf) -> list[str]:
    failures: list[str] = []
    root = stage.GetPrimAtPath(component_path)
    if not root:
        return [f"{component_path} missing"]
    for prim in Usd.PrimRange(root):
        for attr in prim.GetAttributes():
            value = attr.Get()
            if isinstance(value, Sdf.AssetPath) and value.path:
                path = value.path
                if "\\" in path or ":" in path or path.startswith("/"):
                    failures.append(f"{attr.GetPath()} -> {path}")
                    continue
                if path.lower().endswith(".png") and not (anchor / path).exists():
                    failures.append(f"{attr.GetPath()} missing {path}")
    return failures


def _sim_failures(stage, meshes: list[Any], UsdPhysics) -> list[str]:
    failures: list[str] = []
    for mesh in meshes:
        parent = mesh.GetParent()
        volume_flags = [
            _attr_value(mesh, "organiq:volumeTetCooking"),
            _attr_value(parent, "organiq:volumeTetCooking") if parent else None,
        ]
        if any(flag is True for flag in volume_flags):
            failures.append(f"{mesh.GetPath()} has volume tet cooking enabled")
        if stage.GetPrimAtPath(f"{parent.GetPath()}/physics/cooking_mesh").IsValid() if parent else False:
            failures.append(f"{mesh.GetPath()} has a cooking mesh")
        if not mesh.HasAPI(UsdPhysics.CollisionAPI) and _attr_value(mesh, "organiq:deformableAuthoring") != "surface_deformable":
            failures.append(f"{mesh.GetPath()} lacks collision or surface deformable authoring")
    return failures


def _check_instanceable_reference(usd_path: Path, expected_meshes: int, Usd, UsdGeom, Sdf) -> dict[str, Any]:
    stage = Usd.Stage.CreateInMemory()
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    root = UsdGeom.Xform.Define(stage, "/World/Organiq_instance")
    component = UsdGeom.Xform.Define(stage, "/World/Organiq_instance/organiq")
    component.GetPrim().GetReferences().AddReference(str(usd_path), Sdf.Path("/World/organiq"))
    component.GetPrim().SetInstanceable(True)
    meshes = _label_meshes(stage, "/World/Organiq_instance", Usd, UsdGeom)
    ok = component.GetPrim().IsInstanceable() and component.GetPrim().IsInstance() and len(meshes) == expected_meshes
    return {
        "ok": bool(ok),
        "evidence": f"instanceable={component.GetPrim().IsInstanceable()} instance={component.GetPrim().IsInstance()} meshes={len(meshes)} expected={expected_meshes}",
    }


def _attr_value(prim, name: str):
    if not prim or not prim.IsValid():
        return None
    attr = prim.GetAttribute(name)
    return attr.Get() if attr else None


def _prim_is_a(stage, path: str, schema) -> bool:
    prim = stage.GetPrimAtPath(path)
    return bool(prim and prim.IsValid() and prim.IsA(schema))


def _record(checks: list[dict[str, str]], name: str, condition: bool, evidence: str) -> None:
    checks.append({"name": name, "status": "ok" if condition else "failed", "evidence": evidence})


def _note(changes: list[str], changed: bool, message: str) -> bool:
    if message not in changes:
        changes.append(message)
    return True or changed


def _backup_path(usd_path: Path) -> Path:
    candidate = usd_path.with_suffix(f".before-quality-repair{usd_path.suffix}")
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = usd_path.with_suffix(f".before-quality-repair-{index}{usd_path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


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


def _run_under_isaac_python(args: argparse.Namespace) -> int:
    if os.environ.get("ORGANIQ_USD_QUALITY_REEXEC") == "1":
        print("organiq_usd_asset_quality_check=skipped_no_pxr")
        return 1
    isaac_root = _find_isaac_root()
    if isaac_root is None:
        print("organiq_usd_asset_quality_check=skipped_no_isaac_root")
        return 1
    python_exe = isaac_root / "kit" / "python" / "python.exe"
    if not python_exe.exists():
        print("organiq_usd_asset_quality_check=skipped_no_isaac_python")
        return 1
    env = os.environ.copy()
    env["ORGANIQ_USD_QUALITY_REEXEC"] = "1"
    paths = [str(EXT_ROOT), *[str(path) for path in _find_isaac_extensions()]]
    env["PYTHONPATH"] = os.pathsep.join(paths + [env.get("PYTHONPATH", "")])
    for extension_path in _find_isaac_extensions():
        bin_path = extension_path / "bin"
        if bin_path.exists():
            env["PATH"] = f"{bin_path}{os.pathsep}{env.get('PATH', '')}"
    command = [str(python_exe), str(Path(__file__).resolve()), "--usd", str(args.usd), "--report", str(args.report)]
    if args.repair:
        command.append("--repair")
    run = subprocess.run(command, env=env, check=False)
    return int(run.returncode)


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


def _find_isaac_root() -> Path | None:
    global ISAAC_ROOT
    if ISAAC_ROOT is not None:
        return ISAAC_ROOT
    roots: list[Path] = []
    for variable in ("ISAACSIM_ROOT", "ISAAC_SIM_ROOT"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        package_root = Path(local_app_data) / "ov" / "pkg"
        if package_root.exists():
            roots.extend(sorted(package_root.glob("isaac-sim*"), reverse=True))
    for root in roots:
        if (root / "kit" / "python" / "python.exe").exists():
            ISAAC_ROOT = root
            return root
    return None


if __name__ == "__main__":
    raise SystemExit(main())
