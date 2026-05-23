from __future__ import annotations

from dataclasses import dataclass

import carb
import omni.kit.app
import omni.kit.commands
import omni.usd


@dataclass(frozen=True)
class ViewportFrameResult:
    selected_paths: tuple[str, ...]
    camera_set: bool
    framed: bool


async def frame_paths_next_update(
    paths,
    expand_to_meshes: bool = False,
    camera_path: str | None = None,
    update_count: int = 2,
) -> ViewportFrameResult:
    for _ in range(update_count):
        await omni.kit.app.get_app().next_update_async()
    stage = omni.usd.get_context().get_stage()
    frame_paths = [str(path) for path in paths if path]
    if stage is not None and expand_to_meshes:
        expanded: list[str] = []
        for path in frame_paths:
            expanded.extend(renderable_mesh_paths(stage, path))
        if expanded:
            frame_paths = expanded
    camera_set = False
    if stage is not None:
        make_renderable(stage, frame_paths)
        if camera_path:
            frame_camera_to_paths(stage, camera_path, frame_paths)
        camera_set = set_viewport_camera(stage, camera_path)
    framed = select_and_frame_paths(frame_paths)
    return ViewportFrameResult(tuple(frame_paths), camera_set, framed)


def renderable_mesh_paths(stage, root_path: str) -> list[str]:
    from pxr import Usd, UsdGeom

    root_prim = stage.GetPrimAtPath(root_path)
    if not root_prim:
        return []
    paths: list[str] = []
    for prim in Usd.PrimRange(root_prim):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        is_organiq_mesh = bool(prim.GetAttribute("organiq:labelName"))
        imageable = UsdGeom.Imageable(prim)
        if imageable.ComputeVisibility() == UsdGeom.Tokens.invisible and not is_organiq_mesh:
            continue
        purpose = imageable.ComputePurpose()
        if is_organiq_mesh or purpose in (UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy):
            paths.append(str(prim.GetPath()))
    return paths


def make_renderable(stage, paths: list[str]) -> None:
    from pxr import UsdGeom

    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if not prim:
            continue
        imageable = UsdGeom.Imageable(prim)
        if not imageable:
            continue
        imageable.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
        if prim.IsA(UsdGeom.Gprim):
            imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.default_)


def frame_camera_to_paths(stage, camera_path: str, paths: list[str]) -> bool:
    from pxr import Gf, Usd, UsdGeom

    camera_prim = stage.GetPrimAtPath(camera_path)
    if not camera_prim or not camera_prim.IsA(UsdGeom.Camera):
        return False

    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=False,
    )
    points: list[tuple[float, float, float]] = []
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if not prim:
            continue
        aligned = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if aligned.IsEmpty():
            continue
        min_pt = aligned.GetMin()
        max_pt = aligned.GetMax()
        points.append((float(min_pt[0]), float(min_pt[1]), float(min_pt[2])))
        points.append((float(max_pt[0]), float(max_pt[1]), float(max_pt[2])))
    if not points:
        return False

    min_pt = tuple(min(point[index] for point in points) for index in range(3))
    max_pt = tuple(max(point[index] for point in points) for index in range(3))
    centre = tuple((lo + hi) * 0.5 for lo, hi in zip(min_pt, max_pt))
    radius = max(max(hi - lo for lo, hi in zip(min_pt, max_pt)), 0.18)
    eye = Gf.Vec3d(centre[0] + radius * 1.35, centre[1] - radius * 2.15, centre[2] + radius * 0.95)
    target = Gf.Vec3d(*centre)
    view = Gf.Matrix4d(1.0)
    view.SetLookAt(eye, target, Gf.Vec3d(0.0, 0.0, 1.0))

    xformable = UsdGeom.Xformable(camera_prim)
    try:
        xformable.ClearXformOpOrder()
    except Exception:
        pass
    xformable.MakeMatrixXform().Set(view.GetInverse())
    camera = UsdGeom.Camera(camera_prim)
    camera.CreateFocusDistanceAttr(radius * 2.4)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.001, max(radius * 20.0, 10.0)))
    return True


def set_viewport_camera(stage, camera_path: str | None) -> bool:
    if not camera_path or not stage.GetPrimAtPath(camera_path):
        return False
    try:
        from omni.kit.viewport.utility import get_active_viewport
        from pxr import Sdf

        viewport = get_active_viewport()
        if viewport is None:
            return False
        viewport.camera_path = Sdf.Path(camera_path)
        return True
    except Exception as exc:
        carb.log_warn(f"Organiq could not switch viewport camera: {exc}")
        return False


def select_and_frame_paths(paths: list[str]) -> bool:
    path_list = [str(path) for path in paths if path]
    if not path_list:
        return False
    try:
        omni.kit.commands.execute(
            "SelectPrims",
            old_selected_paths=[],
            new_selected_paths=path_list,
            expand_in_stage=True,
        )
    except Exception as exc:
        carb.log_warn(f"Organiq could not select preview prims: {exc}")
    try:
        from omni.kit.viewport.utility import frame_viewport_prims, get_active_viewport

        viewport = get_active_viewport()
        if viewport is not None:
            return bool(frame_viewport_prims(viewport_api=viewport, prims=path_list))
        return bool(frame_viewport_prims(prims=path_list))
    except Exception as exc:
        carb.log_warn(f"Organiq could not frame viewport selection: {exc}")
        return False
