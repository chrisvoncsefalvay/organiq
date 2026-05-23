from __future__ import annotations

from .models import TissueDefaults


def create_rigid_physics_material(stage, material_path: str, defaults: TissueDefaults):
    from pxr import UsdPhysics, UsdShade

    material = UsdShade.Material.Define(stage, material_path)
    material_api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    material_api.CreateStaticFrictionAttr().Set(float(defaults.static_friction))
    material_api.CreateDynamicFrictionAttr().Set(float(defaults.dynamic_friction))
    material_api.CreateRestitutionAttr().Set(0.02)
    material_api.CreateDensityAttr().Set(float(defaults.density_kg_m3))
    return material


def create_deformable_physics_material(stage, material_path: str, defaults: TissueDefaults):
    from pxr import Sdf, UsdShade

    try:
        from omni.physx.scripts import deformableUtils
    except Exception:
        deformableUtils = None

    if deformableUtils is not None:
        try:
            deformableUtils.add_surface_deformable_material(
                stage,
                material_path,
                density=float(defaults.density_kg_m3),
                static_friction=float(defaults.static_friction),
                dynamic_friction=float(defaults.dynamic_friction),
                youngs_modulus=float(defaults.youngs_modulus_pa or 5.0e5),
                poissons_ratio=float(defaults.poissons_ratio or 0.45),
                surface_thickness=_surface_thickness_m(defaults),
                surface_stretch_stiffness=_surface_stretch_stiffness(defaults),
                surface_shear_stiffness=_surface_stretch_stiffness(defaults) * 0.35,
                surface_bend_stiffness=_surface_bend_stiffness(defaults),
            )
            return UsdShade.Material(stage.GetPrimAtPath(material_path))
        except Exception:
            pass

    material = UsdShade.Material.Define(stage, material_path)
    prim = material.GetPrim()
    _apply_api_if_available(prim, "OmniPhysicsDeformableMaterialAPI")
    prim.CreateAttribute("omniphysics:density", Sdf.ValueTypeNames.Float).Set(float(defaults.density_kg_m3))
    prim.CreateAttribute("omniphysics:staticFriction", Sdf.ValueTypeNames.Float).Set(float(defaults.static_friction))
    prim.CreateAttribute("omniphysics:dynamicFriction", Sdf.ValueTypeNames.Float).Set(float(defaults.dynamic_friction))
    prim.CreateAttribute("omniphysics:youngsModulus", Sdf.ValueTypeNames.Float).Set(
        float(defaults.youngs_modulus_pa or 5.0e5)
    )
    prim.CreateAttribute("omniphysics:poissonsRatio", Sdf.ValueTypeNames.Float).Set(
        float(defaults.poissons_ratio or 0.45)
    )
    _apply_api_if_available(prim, "OmniPhysicsSurfaceDeformableMaterialAPI")
    prim.CreateAttribute("omniphysics:surfaceThickness", Sdf.ValueTypeNames.Float).Set(_surface_thickness_m(defaults))
    prim.CreateAttribute("omniphysics:surfaceStretchStiffness", Sdf.ValueTypeNames.Float).Set(
        _surface_stretch_stiffness(defaults)
    )
    prim.CreateAttribute("omniphysics:surfaceShearStiffness", Sdf.ValueTypeNames.Float).Set(
        _surface_stretch_stiffness(defaults) * 0.35
    )
    prim.CreateAttribute("omniphysics:surfaceBendStiffness", Sdf.ValueTypeNames.Float).Set(
        _surface_bend_stiffness(defaults)
    )
    return material


def bind_physics_material(stage, prim, material_path: str):
    from pxr import Sdf, UsdShade

    try:
        from omni.physx.scripts.physicsUtils import add_physics_material_to_prim
    except Exception:
        add_physics_material_to_prim = None

    if add_physics_material_to_prim is not None:
        add_physics_material_to_prim(stage, prim, Sdf.Path(material_path))
        return

    material = UsdShade.Material(stage.GetPrimAtPath(material_path))
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, UsdShade.Tokens.weakerThanDescendants, "physics")


def apply_rigid_body(stage, body_prim, mesh_prim, defaults: TissueDefaults):
    from pxr import Sdf, UsdPhysics

    try:
        from pxr import PhysxSchema
    except Exception:
        PhysxSchema = None

    UsdPhysics.CollisionAPI.Apply(mesh_prim)
    mesh_collision = UsdPhysics.MeshCollisionAPI.Apply(mesh_prim)
    mesh_collision.CreateApproximationAttr().Set("convexDecomposition")
    _make_visual_mesh_renderable(mesh_prim)

    UsdPhysics.RigidBodyAPI.Apply(body_prim)
    mass_api = UsdPhysics.MassAPI.Apply(body_prim)
    mass_api.CreateDensityAttr().Set(float(defaults.density_kg_m3))
    physx_body = None
    if PhysxSchema is not None:
        try:
            physx_body = PhysxSchema.PhysxRigidBodyAPI.Apply(body_prim)
        except Exception:
            physx_body = None
    if physx_body:
        physx_body.CreateSolverPositionIterationCountAttr().Set(32)
        physx_body.CreateSolverVelocityIterationCountAttr().Set(8)
    else:
        body_prim.CreateAttribute("physxRigidBody:solverPositionIterationCount", Sdf.ValueTypeNames.Int).Set(32)
        body_prim.CreateAttribute("physxRigidBody:solverVelocityIterationCount", Sdf.ValueTypeNames.Int).Set(8)


def apply_surface_collision_shell(stage, mesh_prim, defaults: TissueDefaults):
    from pxr import Sdf, UsdPhysics

    UsdPhysics.CollisionAPI.Apply(mesh_prim)
    mesh_collision = UsdPhysics.MeshCollisionAPI.Apply(mesh_prim)
    mesh_collision.CreateApproximationAttr().Set("none")
    _make_visual_mesh_renderable(mesh_prim)
    mesh_prim.CreateAttribute("organiq:collisionShell", Sdf.ValueTypeNames.Bool).Set(True)
    mesh_prim.CreateAttribute("organiq:collisionShellDensityKgM3", Sdf.ValueTypeNames.Float).Set(
        float(defaults.density_kg_m3)
    )


def apply_deformable_body(stage, root_path: str, mesh_path: str, defaults: TissueDefaults):
    from pxr import UsdPhysics

    root_prim = stage.GetPrimAtPath(root_path)
    mesh_prim = stage.GetPrimAtPath(mesh_path)
    if not root_prim or not mesh_prim:
        return False
    _author_surface_deformable_metadata(root_prim, mesh_prim, defaults)
    _make_visual_mesh_renderable(mesh_prim)

    try:
        from omni.physx.scripts import deformableUtils
    except Exception:
        deformableUtils = None

    applied = False
    if deformableUtils is not None:
        try:
            applied = bool(deformableUtils.set_physics_surface_deformable_body(stage, mesh_path))
        except Exception:
            applied = False

    if not applied:
        _apply_api_if_available(mesh_prim, "OmniPhysicsDeformableBodyAPI")
        _apply_api_if_available(mesh_prim, "OmniPhysicsSurfaceDeformableSimAPI")
        UsdPhysics.CollisionAPI.Apply(mesh_prim)

    _author_surface_rest_shape(mesh_prim)
    _author_visual_deformable_pose(mesh_prim)
    _make_visual_mesh_renderable(mesh_prim)
    return str(root_prim.GetPath())


def _author_surface_deformable_metadata(root_prim, mesh_prim, defaults: TissueDefaults) -> None:
    from pxr import Sdf

    for prim in (root_prim, mesh_prim):
        prim.CreateAttribute("organiq:deformableAuthoring", Sdf.ValueTypeNames.Token).Set("surface_deformable")
        prim.CreateAttribute("organiq:volumeTetCooking", Sdf.ValueTypeNames.Bool).Set(False)
        prim.CreateAttribute("physxDeformable:vertexVelocityDamping", Sdf.ValueTypeNames.Float).Set(
            float(defaults.damping_scale)
        )
        prim.CreateAttribute("physxDeformable:solverPositionIterationCount", Sdf.ValueTypeNames.Int).Set(32)


def _make_visual_mesh_renderable(mesh_prim) -> None:
    from pxr import UsdGeom

    imageable = UsdGeom.Imageable(mesh_prim)
    if not imageable:
        return
    imageable.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
    imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.default_)


def _author_visual_deformable_pose(mesh_prim) -> None:
    from pxr import Sdf, UsdGeom

    _apply_api_if_available(mesh_prim, "OmniPhysicsDeformablePoseAPI", "default")
    mesh_prim.CreateAttribute("deformablePose:default:omniphysics:purposes", Sdf.ValueTypeNames.TokenArray).Set(
        ["bindPose"]
    )
    points = UsdGeom.PointBased(mesh_prim).GetPointsAttr().Get()
    mesh_prim.CreateAttribute("deformablePose:default:omniphysics:points", Sdf.ValueTypeNames.Point3fArray).Set(points)


def _author_surface_rest_shape(mesh_prim) -> None:
    from pxr import Gf, Sdf, UsdGeom

    mesh = UsdGeom.Mesh(mesh_prim)
    if not mesh:
        return
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
    if not tri_indices:
        return

    rest_points_attr = mesh_prim.GetAttribute("omniphysics:restShapePoints")
    if rest_points_attr:
        rest_points_attr.Set(points)
    else:
        mesh_prim.CreateAttribute("omniphysics:restShapePoints", Sdf.ValueTypeNames.Point3fArray).Set(points)

    tri_indices_attr = mesh_prim.GetAttribute("omniphysics:restTriVtxIndices")
    if tri_indices_attr:
        tri_indices_attr.Set(tri_indices)
    else:
        mesh_prim.CreateAttribute("omniphysics:restTriVtxIndices", Sdf.ValueTypeNames.Int3Array).Set(tri_indices)


def _surface_thickness_m(defaults: TissueDefaults) -> float:
    return max(0.001, min(float(defaults.mesh_smoothing_mm) * 0.001, 0.006))


def _surface_stretch_stiffness(defaults: TissueDefaults) -> float:
    return max(1.0, float(defaults.youngs_modulus_pa or 5.0e5))


def _surface_bend_stiffness(defaults: TissueDefaults) -> float:
    thickness = _surface_thickness_m(defaults)
    return max(0.001, _surface_stretch_stiffness(defaults) * thickness * thickness)


def _apply_api_if_available(prim, schema_name: str, instance_name: str | None = None) -> bool:
    try:
        if instance_name is None:
            return bool(prim.ApplyAPI(schema_name))
        return bool(prim.ApplyAPI(schema_name, instance_name))
    except Exception:
        return False
