from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .defaults import defaults_for_label, normalise_label_name
from .materials import create_preview_material, create_visual_material
from .models import MeshArtifact, TissueDefaults
from .physics import (
    apply_deformable_body,
    apply_rigid_body,
    apply_surface_collision_shell,
    bind_physics_material,
    create_deformable_physics_material,
    create_rigid_physics_material,
)


ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class UsdExportResult:
    path: Path
    prim_paths: tuple[str, ...]
    rigid_count: int
    deformable_count: int


@dataclass(frozen=True)
class StagePreviewResult:
    root_path: str
    mesh_paths: tuple[str, ...]
    camera_path: str


def export_meshes_to_usd(
    meshes: list[MeshArtifact] | tuple[MeshArtifact, ...],
    output_path: str | Path,
    scene_name: str = "organiq",
    progress: ProgressCallback | None = None,
) -> UsdExportResult:
    if not meshes:
        raise ValueError("No meshes to export")

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    texture_dir = output.parent / f"{output.stem}_textures"

    stage = Usd.Stage.CreateNew(str(output))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    stage.SetFramesPerSecond(60.0)
    stage.SetTimeCodesPerSecond(60.0)

    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    anatomy_root = UsdGeom.Xform.Define(stage, f"/World/{_safe_prim_name(scene_name)}")
    looks_scope = UsdGeom.Scope.Define(stage, "/World/Looks")
    physics_scope = UsdGeom.Scope.Define(stage, "/World/PhysicsMaterials")
    _ = looks_scope, physics_scope
    _author_physics_scene(stage)

    prim_paths: list[str] = []
    used_names: dict[str, int] = {}
    rigid_count = 0
    deformable_count = 0
    scene_bounds: list[tuple[float, float, float]] = []
    total_steps = max(len(meshes), 1) + 1
    _report_progress(progress, 0, total_steps, "authoring USD scene")

    for index, mesh in enumerate(meshes, start=1):
        _report_progress(progress, index - 1, total_steps, f"authoring {mesh.label_name}")
        defaults = defaults_for_label(mesh.label_name)
        name = _unique_name(_safe_prim_name(mesh.label_name), used_names)
        root_path = f"{anatomy_root.GetPath()}/{name}"
        mesh_path = f"{root_path}/mesh"
        material_path = f"/World/Looks/{name}_material"
        physics_material_path = f"/World/PhysicsMaterials/{name}_physics"

        root = UsdGeom.Xform.Define(stage, root_path)
        usd_mesh = _define_mesh(stage, mesh_path, mesh, defaults)
        material = create_visual_material(stage, material_path, defaults, texture_dir=texture_dir, material_key=name)
        UsdShade.MaterialBindingAPI.Apply(usd_mesh.GetPrim()).Bind(
            material, UsdShade.Tokens.strongerThanDescendants
        )
        _author_semantics(root.GetPrim(), usd_mesh.GetPrim(), mesh, defaults)

        if defaults.simulation_mode == "rigid":
            apply_rigid_body(stage, root.GetPrim(), usd_mesh.GetPrim(), defaults)
            create_rigid_physics_material(stage, physics_material_path, defaults)
            bind_physics_material(stage, usd_mesh.GetPrim(), physics_material_path)
            rigid_count += 1
        elif defaults.simulation_mode == "surface_shell":
            apply_surface_collision_shell(stage, usd_mesh.GetPrim(), defaults)
            create_rigid_physics_material(stage, physics_material_path, defaults)
            bind_physics_material(stage, usd_mesh.GetPrim(), physics_material_path)
        else:
            physics_root_path = apply_deformable_body(stage, str(root.GetPath()), str(usd_mesh.GetPath()), defaults)
            create_deformable_physics_material(stage, physics_material_path, defaults)
            if physics_root_path and stage.GetPrimAtPath(physics_root_path):
                bind_physics_material(stage, stage.GetPrimAtPath(physics_root_path), physics_material_path)
            bind_physics_material(stage, root.GetPrim(), physics_material_path)
            bind_physics_material(stage, usd_mesh.GetPrim(), physics_material_path)
            deformable_count += 1

        prim_paths.append(str(root.GetPath()))
        scene_bounds.extend(mesh.vertices_m)
        _report_progress(progress, index, total_steps, f"authored {index} of {len(meshes)} meshes")

    bounds = _bounds(scene_bounds)
    _author_scene_context(stage, bounds)
    _author_camera(stage, bounds)

    _report_progress(progress, len(meshes), total_steps, "saving USD")
    stage.GetRootLayer().Save()
    _report_progress(progress, total_steps, total_steps, f"exported {len(meshes)} meshes")
    return UsdExportResult(output, tuple(prim_paths), rigid_count, deformable_count)


def preview_meshes_on_stage(
    stage,
    meshes: list[MeshArtifact] | tuple[MeshArtifact, ...],
    root_path: str = "/World/Organiq_preview",
) -> StagePreviewResult:
    if not meshes:
        raise ValueError("No meshes to preview")

    from pxr import Sdf, UsdGeom, UsdShade

    if stage.GetPrimAtPath(root_path):
        stage.RemovePrim(root_path)

    root = UsdGeom.Xform.Define(stage, root_path)
    root.GetPrim().CreateAttribute("organiq:role", Sdf.ValueTypeNames.Token).Set("meshPreview")
    root.GetPrim().CreateAttribute("organiq:meshCount", Sdf.ValueTypeNames.Int).Set(len(meshes))
    UsdGeom.Imageable(root.GetPrim()).CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
    UsdGeom.Scope.Define(stage, "/World/Looks")

    mesh_paths: list[str] = []
    used_names: dict[str, int] = {}
    scene_bounds: list[tuple[float, float, float]] = []
    for mesh in meshes:
        defaults = defaults_for_label(mesh.label_name)
        name = _unique_name(_safe_prim_name(mesh.label_name), used_names)
        label_root_path = f"{root_path}/{name}"
        mesh_path = f"{label_root_path}/mesh"
        material_path = f"/World/Looks/{name}_preview_material"

        label_root = UsdGeom.Xform.Define(stage, label_root_path)
        UsdGeom.Imageable(label_root.GetPrim()).CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
        usd_mesh = _define_mesh(stage, mesh_path, mesh, defaults)
        imageable = UsdGeom.Imageable(usd_mesh.GetPrim())
        imageable.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
        imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.default_)

        material = create_preview_material(stage, material_path, defaults)
        UsdShade.MaterialBindingAPI.Apply(usd_mesh.GetPrim()).Bind(
            material, UsdShade.Tokens.strongerThanDescendants
        )
        _author_semantics(label_root.GetPrim(), usd_mesh.GetPrim(), mesh, defaults)
        mesh_paths.append(str(usd_mesh.GetPath()))
        scene_bounds.extend(mesh.vertices_m)

    bounds = _bounds(scene_bounds)
    _author_preview_lighting(stage, bounds, str(root.GetPath()))
    camera_path = f"{root.GetPath()}/camera"
    _author_camera(stage, bounds, camera_path=camera_path)

    return StagePreviewResult(str(root.GetPath()), tuple(mesh_paths), camera_path)


def instantiate_usd_on_stage(stage, usd_path: str | Path, prim_path: str = "/World/Organiq_instance") -> str:
    from pxr import Sdf, UsdGeom

    source_path = Path(usd_path).resolve()
    _remove_preview_prims(stage)
    if stage.GetPrimAtPath(prim_path):
        stage.RemovePrim(prim_path)
    root = UsdGeom.Xform.Define(stage, prim_path)
    UsdGeom.Imageable(root.GetPrim()).CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
    references = root.GetPrim().GetReferences()
    references.ClearReferences()
    references.AddReference(str(source_path))
    _make_instance_visuals_renderable(stage, str(root.GetPath()))
    bounds = _label_mesh_bounds(stage, str(root.GetPath()))
    camera_path = f"{root.GetPath()}/view_camera"
    _author_camera(stage, bounds, camera_path=camera_path)
    root.GetPrim().CreateAttribute("organiq:sourceUsd", Sdf.ValueTypeNames.Asset).Set(str(source_path))
    root.GetPrim().CreateAttribute("organiq:expectedMeshCount", Sdf.ValueTypeNames.Int).Set(
        _source_mesh_count(source_path)
    )
    root.GetPrim().CreateAttribute("organiq:viewCamera", Sdf.ValueTypeNames.String).Set(camera_path)
    return str(root.GetPath())


def _make_instance_visuals_renderable(stage, root_path: str) -> None:
    from pxr import Usd, UsdGeom

    root_prim = stage.GetPrimAtPath(root_path)
    if not root_prim:
        return
    looks_path = f"{root_path}/Looks"
    UsdGeom.Scope.Define(stage, looks_path)
    for prim in Usd.PrimRange(root_prim):
        imageable = UsdGeom.Imageable(prim)
        if not imageable:
            continue
        role = prim.GetAttribute("organiq:role").Get() if prim.GetAttribute("organiq:role") else None
        if role == "deformablePhysicsProxy" or "/physics/" in str(prim.GetPath()):
            imageable.CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
            imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
            continue
        label_attr = prim.GetAttribute("organiq:labelName")
        label_name = label_attr.Get() if label_attr else None
        if prim.IsA(UsdGeom.Mesh) and label_name:
            imageable.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
            imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.default_)
            _ensure_mesh_viewport_material(stage, looks_path, prim, str(label_name))


def _ensure_mesh_viewport_material(stage, looks_path: str, mesh_prim, label_name: str) -> None:
    from pxr import UsdShade

    if _mesh_has_universal_surface_material(mesh_prim):
        return
    parent = mesh_prim.GetParent()
    material_name = _safe_prim_name(str(parent.GetName()) if parent else label_name)
    material_path = f"{looks_path}/{material_name}_viewport_material"
    material = create_preview_material(stage, material_path, defaults_for_label(label_name))
    UsdShade.MaterialBindingAPI.Apply(mesh_prim).Bind(material, UsdShade.Tokens.strongerThanDescendants)


def _mesh_has_universal_surface_material(mesh_prim) -> bool:
    from pxr import UsdShade

    binding = UsdShade.MaterialBindingAPI(mesh_prim)
    try:
        bound_material = binding.ComputeBoundMaterial()[0]
    except Exception:
        bound_material = None
    if bound_material and bound_material.GetPrim().IsValid():
        return _material_has_universal_surface(bound_material)

    for target in binding.GetDirectBindingRel().GetTargets():
        material = UsdShade.Material(mesh_prim.GetStage().GetPrimAtPath(target))
        if material and material.GetPrim().IsValid() and _material_has_universal_surface(material):
            return True
    return False


def _material_has_universal_surface(material) -> bool:
    output = material.GetSurfaceOutput()
    try:
        attr = output.GetAttr()
    except Exception:
        return False
    if not attr or not attr.IsValid():
        return False
    try:
        return bool(output.HasConnectedSource())
    except Exception:
        pass
    try:
        return bool(output.GetConnectedSources())
    except Exception:
        return False


def _label_mesh_bounds(stage, root_path: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    from pxr import Usd, UsdGeom

    root_prim = stage.GetPrimAtPath(root_path)
    if not root_prim:
        return _bounds(())
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=False,
    )
    points: list[tuple[float, float, float]] = []
    for prim in Usd.PrimRange(root_prim):
        if not prim.IsA(UsdGeom.Mesh) or not prim.GetAttribute("organiq:labelName").Get():
            continue
        aligned = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if aligned.IsEmpty():
            continue
        min_pt = aligned.GetMin()
        max_pt = aligned.GetMax()
        points.append((float(min_pt[0]), float(min_pt[1]), float(min_pt[2])))
        points.append((float(max_pt[0]), float(max_pt[1]), float(max_pt[2])))
    return _bounds(tuple(points))


def _remove_preview_prims(stage) -> None:
    preview_paths: set[str] = set()
    default_preview = stage.GetPrimAtPath("/World/Organiq_preview")
    if default_preview:
        preview_paths.add(str(default_preview.GetPath()))

    for prim in stage.Traverse():
        role_attr = prim.GetAttribute("organiq:role")
        if role_attr and role_attr.Get() == "meshPreview":
            preview_paths.add(str(prim.GetPath()))
        path = str(prim.GetPath())
        if path.startswith("/World/Looks/") and path.endswith("_preview_material"):
            preview_paths.add(path)

    for path in sorted(preview_paths, key=lambda value: value.count("/"), reverse=True):
        if stage.GetPrimAtPath(path):
            stage.RemovePrim(path)


def _source_mesh_count(usd_path: Path) -> int:
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        return 0
    return sum(
        1
        for prim in stage.Traverse()
        if prim.IsA(UsdGeom.Mesh) and prim.GetAttribute("organiq:labelName").Get()
    )


def _define_mesh(stage, mesh_path: str, mesh: MeshArtifact, defaults: TissueDefaults):
    from pxr import Gf, Sdf, UsdGeom

    usd_mesh = UsdGeom.Mesh.Define(stage, mesh_path)
    points = [Gf.Vec3f(*point) for point in mesh.vertices_m]
    face_counts = [3 for _ in mesh.faces]
    face_indices = [int(index) for face in mesh.faces for index in face]
    usd_mesh.CreatePointsAttr(points)
    usd_mesh.CreateFaceVertexCountsAttr(face_counts)
    usd_mesh.CreateFaceVertexIndicesAttr(face_indices)
    if mesh.vertex_normals and len(mesh.vertex_normals) == len(mesh.vertices_m):
        usd_mesh.CreateNormalsAttr([Gf.Vec3f(*normal) for normal in mesh.vertex_normals])
        usd_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    usd_mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    usd_mesh.CreateDoubleSidedAttr().Set(True)
    usd_mesh.CreateDisplayColorAttr().Set([Gf.Vec3f(*defaults.colour)])
    usd_mesh.CreateDisplayOpacityAttr().Set([float(defaults.opacity)])
    extent = _bounds(mesh.vertices_m)
    usd_mesh.CreateExtentAttr().Set([Gf.Vec3f(*extent[0]), Gf.Vec3f(*extent[1])])
    usd_mesh.GetPrim().CreateAttribute("organiq:sourceVoxels", Sdf.ValueTypeNames.Int).Set(int(mesh.source_voxels))
    usd_mesh.GetPrim().CreateAttribute("organiq:meshingMethod", Sdf.ValueTypeNames.Token).Set(mesh.meshing_method)
    if mesh.mean_hounsfield is not None:
        usd_mesh.GetPrim().CreateAttribute("organiq:meanHounsfield", Sdf.ValueTypeNames.Float).Set(
            float(mesh.mean_hounsfield)
        )
    if mesh.distance_field is not None:
        _author_distance_field_metadata(usd_mesh.GetPrim(), mesh.distance_field)
    return usd_mesh


def _author_distance_field_metadata(mesh_prim, metadata):
    from pxr import Gf, Sdf

    mesh_prim.CreateAttribute("organiq:sdfShape", Sdf.ValueTypeNames.Int3).Set(
        Gf.Vec3i(*(int(v) for v in metadata.shape))
    )
    mesh_prim.CreateAttribute("organiq:sdfSpacingMm", Sdf.ValueTypeNames.Float3).Set(
        Gf.Vec3f(*(float(v) for v in metadata.spacing_mm))
    )
    mesh_prim.CreateAttribute("organiq:sdfNarrowBandMm", Sdf.ValueTypeNames.Float).Set(float(metadata.narrow_band_mm))
    mesh_prim.CreateAttribute("organiq:sdfMinDistanceMm", Sdf.ValueTypeNames.Float).Set(
        float(metadata.min_distance_mm)
    )
    mesh_prim.CreateAttribute("organiq:sdfMaxDistanceMm", Sdf.ValueTypeNames.Float).Set(
        float(metadata.max_distance_mm)
    )


def _author_semantics(root_prim, mesh_prim, mesh: MeshArtifact, defaults: TissueDefaults):
    from pxr import Sdf

    for prim in (root_prim, mesh_prim):
        prim.CreateAttribute("organiq:labelValue", Sdf.ValueTypeNames.Int).Set(int(mesh.label_value))
        prim.CreateAttribute("organiq:labelName", Sdf.ValueTypeNames.String).Set(mesh.label_name)
        prim.CreateAttribute("organiq:semanticClass", Sdf.ValueTypeNames.String).Set(defaults.semantic_class)
        prim.CreateAttribute("organiq:simulationMode", Sdf.ValueTypeNames.Token).Set(defaults.simulation_mode)
        prim.CreateAttribute("organiq:densityKgM3", Sdf.ValueTypeNames.Float).Set(float(defaults.density_kg_m3))
        prim.CreateAttribute("organiq:youngsModulusPa", Sdf.ValueTypeNames.Float).Set(
            float(defaults.youngs_modulus_pa or 0.0)
        )
        prim.CreateAttribute("organiq:poissonsRatio", Sdf.ValueTypeNames.Float).Set(
            float(defaults.poissons_ratio or 0.0)
        )
        prim.CreateAttribute("organiq:dampingScale", Sdf.ValueTypeNames.Float).Set(float(defaults.damping_scale))
        prim.CreateAttribute("organiq:deformableResolution", Sdf.ValueTypeNames.Int).Set(
            int(defaults.deformable_resolution)
        )
        prim.CreateAttribute("organiq:meshSmoothingMm", Sdf.ValueTypeNames.Float).Set(
            float(defaults.mesh_smoothing_mm)
        )
        prim.CreateAttribute("organiq:surfaceDetail", Sdf.ValueTypeNames.Token).Set(defaults.surface_detail)
        prim.CreateAttribute("organiq:meshingMethod", Sdf.ValueTypeNames.Token).Set(mesh.meshing_method)
        if mesh.mean_hounsfield is not None:
            prim.CreateAttribute("organiq:meanHounsfield", Sdf.ValueTypeNames.Float).Set(float(mesh.mean_hounsfield))


def _author_physics_scene(stage):
    from pxr import Gf, Sdf, UsdPhysics

    scene = UsdPhysics.Scene.Define(stage, "/World/physicsScene")
    scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0.0, 0.0, -1.0))
    scene.CreateGravityMagnitudeAttr().Set(9.80665)
    try:
        from pxr import PhysxSchema
    except Exception:
        PhysxSchema = None
    if PhysxSchema is not None:
        try:
            physx_scene = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
        except Exception:
            physx_scene = None
        if physx_scene:
            physx_scene.CreateEnableGPUDynamicsAttr().Set(True)
            physx_scene.CreateTimeStepsPerSecondAttr().Set(120)
            return
    scene.GetPrim().CreateAttribute("physxScene:enableGPUDynamics", Sdf.ValueTypeNames.Bool).Set(True)
    scene.GetPrim().CreateAttribute("physxScene:timeStepsPerSecond", Sdf.ValueTypeNames.Int).Set(120)


def _author_scene_context(stage, bounds: tuple[tuple[float, float, float], tuple[float, float, float]]):
    from pxr import Gf, Sdf, UsdGeom, UsdLux, UsdPhysics

    min_pt, max_pt = bounds
    width = max(max_pt[0] - min_pt[0], max_pt[1] - min_pt[1], 0.2)
    centre_x = (min_pt[0] + max_pt[0]) * 0.5
    centre_y = (min_pt[1] + max_pt[1]) * 0.5
    floor_z = min_pt[2] - max(width * 0.04, 0.02)

    floor = UsdGeom.Cube.Define(stage, "/World/contextTable")
    floor.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(floor).SetTranslate(Gf.Vec3d(centre_x, centre_y, floor_z - 0.01))
    UsdGeom.XformCommonAPI(floor).SetScale(Gf.Vec3f(width * 1.4, width * 1.4, 0.02))
    floor.CreateDisplayColorAttr().Set([Gf.Vec3f(0.12, 0.13, 0.14)])
    floor.GetPrim().CreateAttribute("organiq:role", Sdf.ValueTypeNames.Token).Set("context")
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim())

    dome = UsdLux.DomeLight.Define(stage, "/World/lighting/dome")
    dome.CreateIntensityAttr(450.0)
    dome.CreateColorAttr(Gf.Vec3f(0.76, 0.82, 0.90))

    key = UsdLux.RectLight.Define(stage, "/World/lighting/key")
    key.CreateIntensityAttr(6500.0)
    key.CreateWidthAttr(width * 1.1)
    key.CreateHeightAttr(width * 0.7)
    UsdGeom.XformCommonAPI(key).SetTranslate(Gf.Vec3d(centre_x - width * 0.5, centre_y - width, max_pt[2] + width))
    UsdGeom.XformCommonAPI(key).SetRotate(Gf.Vec3f(60.0, 0.0, -28.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)

    rim = UsdLux.DistantLight.Define(stage, "/World/lighting/rim")
    rim.CreateIntensityAttr(380.0)
    rim.CreateAngleAttr(4.0)
    UsdGeom.XformCommonAPI(rim).SetRotate(Gf.Vec3f(-42.0, 0.0, 135.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)


def _author_preview_lighting(
    stage,
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]],
    root_path: str,
):
    from pxr import Gf, UsdGeom, UsdLux

    min_pt, max_pt = bounds
    width = max(max_pt[0] - min_pt[0], max_pt[1] - min_pt[1], 0.2)
    centre_x = (min_pt[0] + max_pt[0]) * 0.5
    centre_y = (min_pt[1] + max_pt[1]) * 0.5

    dome = UsdLux.DomeLight.Define(stage, f"{root_path}/lighting/dome")
    dome.CreateIntensityAttr(360.0)
    dome.CreateColorAttr(Gf.Vec3f(0.78, 0.82, 0.88))

    key = UsdLux.RectLight.Define(stage, f"{root_path}/lighting/key")
    key.CreateIntensityAttr(5200.0)
    key.CreateWidthAttr(width * 1.15)
    key.CreateHeightAttr(width * 0.75)
    UsdGeom.XformCommonAPI(key).SetTranslate(Gf.Vec3d(centre_x - width * 0.55, centre_y - width, max_pt[2] + width))
    UsdGeom.XformCommonAPI(key).SetRotate(Gf.Vec3f(60.0, 0.0, -28.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)

    rim = UsdLux.DistantLight.Define(stage, f"{root_path}/lighting/rim")
    rim.CreateIntensityAttr(260.0)
    rim.CreateAngleAttr(4.0)
    UsdGeom.XformCommonAPI(rim).SetRotate(Gf.Vec3f(-42.0, 0.0, 135.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)


def _author_camera(
    stage,
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]],
    camera_path: str = "/World/camera",
):
    from pxr import Gf, UsdGeom

    min_pt, max_pt = bounds
    centre = tuple((lo + hi) * 0.5 for lo, hi in zip(min_pt, max_pt))
    radius = max(max(hi - lo for lo, hi in zip(min_pt, max_pt)), 0.18)
    camera = UsdGeom.Camera.Define(stage, camera_path)
    eye = Gf.Vec3d(centre[0] + radius * 1.35, centre[1] - radius * 2.15, centre[2] + radius * 0.95)
    target = Gf.Vec3d(*centre)
    view = Gf.Matrix4d(1.0)
    view.SetLookAt(eye, target, Gf.Vec3d(0.0, 0.0, 1.0))
    xformable = UsdGeom.Xformable(camera)
    try:
        xformable.ClearXformOpOrder()
    except Exception:
        pass
    xformable.MakeMatrixXform().Set(view.GetInverse())
    camera.CreateFocalLengthAttr(55.0)
    camera.CreateFocusDistanceAttr(radius * 2.4)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.001, max(radius * 20.0, 10.0)))
    stage.GetRootLayer().customLayerData["cameraPrim"] = str(camera.GetPath())


def _bounds(points: list[tuple[float, float, float]] | tuple[tuple[float, float, float], ...]):
    if not points:
        return ((-0.05, -0.05, -0.05), (0.05, 0.05, 0.05))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def _safe_prim_name(value: str) -> str:
    name = normalise_label_name(value)
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not name:
        name = "label"
    if name[0].isdigit():
        name = f"label_{name}"
    return name


def _unique_name(name: str, used: dict[str, int]) -> str:
    count = used.get(name, 0)
    used[name] = count + 1
    if count == 0:
        return name
    return f"{name}_{count + 1}"


def _report_progress(progress: ProgressCallback | None, completed: int, total: int, status: str) -> None:
    if progress is None:
        return
    progress(int(completed), max(int(total), 1), status)
