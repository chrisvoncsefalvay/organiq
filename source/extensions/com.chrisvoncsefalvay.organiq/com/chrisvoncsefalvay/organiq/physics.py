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
            deformableUtils.add_deformable_material(
                stage,
                material_path,
                density=float(defaults.density_kg_m3),
                static_friction=float(defaults.static_friction),
                dynamic_friction=float(defaults.dynamic_friction),
                youngs_modulus=float(defaults.youngs_modulus_pa or 5.0e5),
                poissons_ratio=float(defaults.poissons_ratio or 0.45),
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
    from pxr import Sdf, UsdGeom, UsdPhysics

    root_prim = stage.GetPrimAtPath(root_path)
    mesh_prim = stage.GetPrimAtPath(mesh_path)
    if not root_prim or not mesh_prim:
        return False
    _author_deformable_resolution(root_prim, defaults)
    _make_visual_mesh_renderable(mesh_prim)

    physics_root_path = f"{root_path}/physics"
    cooking_mesh_path = f"{physics_root_path}/cooking_mesh"
    simulation_tet_path = f"{physics_root_path}/simulation_tet"
    collision_tet_path = f"{physics_root_path}/collision_tet"
    physics_root = UsdGeom.Xform.Define(stage, physics_root_path)
    physics_root.GetPrim().CreateAttribute("organiq:role", Sdf.ValueTypeNames.Token).Set("deformablePhysicsProxy")
    _hide_physics_proxy(physics_root.GetPrim())
    cooking_mesh = _copy_mesh_for_cooking(stage, mesh_prim, cooking_mesh_path)
    if cooking_mesh is not None:
        _hide_physics_proxy(cooking_mesh.GetPrim())

    try:
        from omni.physx.scripts import deformableUtils
    except Exception:
        deformableUtils = None

    if deformableUtils is not None and cooking_mesh is not None:
        try:
            ok = deformableUtils.create_auto_volume_deformable_hierarchy(
                stage,
                physics_root_path,
                simulation_tet_path,
                collision_tet_path,
                cooking_mesh_path,
                simulation_hex_mesh_enabled=True,
                cooking_src_simplification_enabled=True,
                set_visibility_with_guide_purpose=True,
            )
            if ok:
                _author_deformable_resolution(physics_root.GetPrim(), defaults)
                _hide_physics_proxy(stage.GetPrimAtPath(simulation_tet_path))
                _hide_physics_proxy(stage.GetPrimAtPath(collision_tet_path))
                _author_visual_deformable_pose(mesh_prim)
                _apply_visual_collision(mesh_prim)
                _make_visual_mesh_renderable(mesh_prim)
                return str(physics_root.GetPath())
        except Exception:
            pass

    if cooking_mesh is not None:
        _apply_api_if_available(physics_root.GetPrim(), "PhysxAutoDeformableBodyAPI")
        _apply_api_if_available(physics_root.GetPrim(), "PhysxAutoDeformableHexahedralMeshAPI")
        _apply_api_if_available(physics_root.GetPrim(), "OmniPhysicsDeformableBodyAPI")
        physics_root.GetPrim().CreateRelationship("physxDeformableBody:cookingSourceMesh").SetTargets(
            [cooking_mesh.GetPath()]
        )
        _author_deformable_resolution(physics_root.GetPrim(), defaults)

    _author_visual_deformable_pose(mesh_prim)
    _apply_visual_collision(mesh_prim)
    _make_visual_mesh_renderable(mesh_prim)
    return str(physics_root.GetPath())


def _author_deformable_resolution(root_prim, defaults: TissueDefaults) -> None:
    from pxr import Sdf

    resolution = max(4, min(int(defaults.deformable_resolution), 64))
    root_prim.CreateAttribute("physxDeformable:simulationHexahedralResolution", Sdf.ValueTypeNames.UInt).Set(resolution)
    root_prim.CreateAttribute("physxDeformableBody:resolution", Sdf.ValueTypeNames.UInt).Set(resolution)
    root_prim.CreateAttribute("physxDeformable:numberOfTetsPerHex", Sdf.ValueTypeNames.UInt).Set(5)
    root_prim.CreateAttribute("physxDeformable:vertexVelocityDamping", Sdf.ValueTypeNames.Float).Set(
        float(defaults.damping_scale)
    )
    root_prim.CreateAttribute("physxDeformable:solverPositionIterationCount", Sdf.ValueTypeNames.Int).Set(32)


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


def _apply_visual_collision(mesh_prim) -> None:
    from pxr import UsdPhysics

    UsdPhysics.CollisionAPI.Apply(mesh_prim)
    mesh_collision = UsdPhysics.MeshCollisionAPI.Apply(mesh_prim)
    mesh_collision.CreateApproximationAttr().Set("meshSimplification")


def _hide_physics_proxy(prim) -> None:
    if not prim:
        return
    from pxr import UsdGeom

    imageable = UsdGeom.Imageable(prim)
    if not imageable:
        return
    imageable.CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)


def _copy_mesh_for_cooking(stage, source_prim, target_path: str):
    from pxr import UsdGeom

    source = UsdGeom.Mesh(source_prim)
    if not source:
        return None
    target = UsdGeom.Mesh.Define(stage, target_path)
    points = source.GetPointsAttr().Get()
    face_counts = source.GetFaceVertexCountsAttr().Get()
    face_indices = source.GetFaceVertexIndicesAttr().Get()
    if not points or not face_counts or not face_indices:
        return None
    target.CreatePointsAttr(points)
    target.CreateFaceVertexCountsAttr(face_counts)
    target.CreateFaceVertexIndicesAttr(face_indices)
    normals = source.GetNormalsAttr().Get()
    if normals:
        target.CreateNormalsAttr(normals)
        target.SetNormalsInterpolation(source.GetNormalsInterpolation())
    extent = source.GetExtentAttr().Get()
    if extent:
        target.CreateExtentAttr().Set(extent)
    target.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    target.CreateDoubleSidedAttr().Set(True)
    return target


def _apply_api_if_available(prim, schema_name: str, instance_name: str | None = None) -> bool:
    try:
        if instance_name is None:
            return bool(prim.ApplyAPI(schema_name))
        return bool(prim.ApplyAPI(schema_name, instance_name))
    except Exception:
        return False
