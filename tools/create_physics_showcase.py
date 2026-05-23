from __future__ import annotations

import argparse
import json
import math
import os
import re
import string
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"
BUILD_ROOT = REPO_ROOT / "build"
DEFAULT_OUTPUT = BUILD_ROOT / "organiq_physics_showcase.usd"
DEFAULT_REPORT = BUILD_ROOT / "organiq_physics_showcase.json"
DEFAULT_SHOTS = BUILD_ROOT / "organiq_physics_showcase_shots.json"
DEFAULT_CARD_DIR = BUILD_ROOT / "organiq_physics_showcase_cards"
DEFAULT_PROBE_OUTPUT = BUILD_ROOT / "organiq_physics_probe.usd"
DEFAULT_PROBE_REPORT = BUILD_ROOT / "organiq_physics_probe.json"
USD_LIB_PREFIXES = ("omni.usd.libs", "omni.usd.schema.physx")
ISAAC_ROOT: Path | None = None


@dataclass(frozen=True)
class MeshInfo:
    label: str
    safe_name: str
    mesh_path: str
    root_path: str
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]]
    centre: tuple[float, float, float]
    volume: float
    simulation_mode: str
    mean_hounsfield: float | None
    density_kg_m3: float
    youngs_modulus_pa: float | None
    poissons_ratio: float | None
    offset: tuple[float, float, float]
    softness: float


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not _ensure_pxr_available():
        return _run_under_isaac_python(args)
    return _create_showcase(args)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an Organiq physics showcase USD")
    parser.add_argument("--source-usd", default=str(_default_source_usd()), help="Organiq anatomy USD to showcase")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="showcase USD output path")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="JSON report path")
    parser.add_argument("--shots", default=str(DEFAULT_SHOTS), help="shot manifest JSON path")
    parser.add_argument("--card-dir", default=str(DEFAULT_CARD_DIR), help="directory for generated card textures")
    parser.add_argument("--max-panels", type=int, default=6, help="maximum metadata panels to place in the scene")
    parser.add_argument("--probe-output", default=str(DEFAULT_PROBE_OUTPUT), help="focused physics probe USD output path")
    parser.add_argument("--probe-report", default=str(DEFAULT_PROBE_REPORT), help="focused physics probe report path")
    parser.add_argument("--skip-probe", action="store_true", help="skip the focused physics probe USD")
    return parser.parse_args(argv)


def _default_source_usd() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        local_output = Path(local_app_data) / "Organiq" / "outputs" / "organiq_scene.usd"
        if local_output.exists():
            return local_output
    build_output = BUILD_ROOT / "organiq_scene_migrated.usd"
    if build_output.exists():
        return build_output
    return BUILD_ROOT / "organiq_dicom_workflow.usd"


def _create_showcase(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(EXT_ROOT))

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdRender, UsdShade

    from com.chrisvoncsefalvay.organiq.defaults import defaults_for_label

    source_usd = Path(args.source_usd).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    shots_path = Path(args.shots).expanduser().resolve()
    card_dir = Path(args.card_dir).expanduser().resolve()
    _require(source_usd.exists(), f"source USD does not exist: {source_usd}")

    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    shots_path.parent.mkdir(parents=True, exist_ok=True)
    card_dir.mkdir(parents=True, exist_ok=True)

    stage = Usd.Stage.CreateNew(str(output))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    stage.SetStartTimeCode(1.0)
    stage.SetEndTimeCode(360.0)
    stage.SetFramesPerSecond(60.0)
    stage.SetTimeCodesPerSecond(60.0)

    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    showcase = UsdGeom.Xform.Define(stage, "/World/Showcase")
    showcase.GetPrim().CreateAttribute("organiq:sourceUsd", Sdf.ValueTypeNames.Asset).Set(str(source_usd))
    showcase.GetPrim().CreateAttribute("organiq:purpose", Sdf.ValueTypeNames.String).Set(
        "physics visualisation"
    )

    anatomy_path = "/World/Showcase/anatomy"
    anatomy = UsdGeom.Xform.Define(stage, anatomy_path)
    anatomy.GetPrim().GetReferences().AddReference(str(source_usd), Sdf.Path("/World/organiq"))
    anatomy.GetPrim().CreateAttribute("organiq:visualSequence", Sdf.ValueTypeNames.String).Set(
        "wireframe to rendered anatomy, then metadata-driven physics motion"
    )

    mesh_infos = _collect_mesh_infos(stage, anatomy_path, defaults_for_label, Usd, UsdGeom)
    _require(mesh_infos, "source USD did not expose labelled Organiq meshes")
    bounds = _bounds([point for info in mesh_infos for point in info.bounds])

    materials = _author_materials(stage, UsdShade, Sdf, Gf)
    hidden_physics_visuals = _hide_source_physics_visuals(stage, anatomy_path, Sdf, Usd, UsdGeom)
    _author_anatomy_materials(stage, mesh_infos, UsdGeom, UsdShade, Sdf, Gf)
    _author_environment(stage, bounds, materials, UsdGeom, UsdLux, UsdShade, Gf)
    _author_animation(stage, anatomy_path, mesh_infos, UsdGeom, Gf)
    wireframe_segments = _author_wireframes(stage, mesh_infos, materials, Usd, UsdGeom, Gf)
    _author_elastic_response(stage, mesh_infos, materials, UsdGeom, Gf)
    panels = _author_metadata_panels(
        stage,
        mesh_infos,
        bounds,
        card_dir,
        output.parent,
        int(args.max_panels),
        materials,
        Sdf,
        UsdGeom,
        UsdShade,
        Gf,
    )
    camera_path = _author_cameras(stage, bounds, mesh_infos, UsdGeom, Gf)
    _author_render_settings(stage, camera_path, UsdRender, Sdf, Gf)
    shots = _write_shot_manifest(shots_path, output, camera_path)

    _set_layer_custom_data(
        stage,
        {
            "cameraPrim": camera_path,
            "showcasePrim": "/World/Showcase",
            "initialTimeCode": 96.0,
        },
    )
    stage.GetRootLayer().Save()

    probe = None
    if not args.skip_probe:
        probe = _create_physics_probe(
            source_usd,
            Path(args.probe_output).expanduser().resolve(),
            Path(args.probe_report).expanduser().resolve(),
            defaults_for_label,
            Usd,
            UsdGeom,
            UsdLux,
            UsdPhysics,
            UsdRender,
            UsdShade,
            Sdf,
            Gf,
        )

    report = {
        "status": "ok",
        "source_usd": str(source_usd),
        "showcase_usd": str(output),
        "camera_path": camera_path,
        "shots_path": str(shots_path),
        "mesh_count": len(mesh_infos),
        "wireframe_segments": wireframe_segments,
        "metadata_panels": panels,
        "hidden_physics_visuals": hidden_physics_visuals,
        "probe": probe,
        "shots": shots,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"showcase={output}")
    print(f"report={report_path}")
    print(f"shots={shots_path}")
    print("organiq_physics_showcase=ok")
    return 0


def _collect_mesh_infos(stage, anatomy_path: str, defaults_for_label, Usd, UsdGeom) -> list[MeshInfo]:
    root = stage.GetPrimAtPath(anatomy_path)
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=False,
    )
    raw_infos: list[dict[str, Any]] = []
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
        if prim.IsInstanceProxy() or not prim.IsA(UsdGeom.Mesh):
            continue
        label = _attr_value(prim, "organiq:labelName")
        if not label:
            continue
        role = _attr_value(prim, "organiq:role")
        path = str(prim.GetPath())
        if role == "deformablePhysicsProxy" or "/physics/" in path:
            continue

        aligned = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if aligned.IsEmpty():
            continue
        min_pt = _vec3_tuple(aligned.GetMin())
        max_pt = _vec3_tuple(aligned.GetMax())
        centre = tuple((lo + hi) * 0.5 for lo, hi in zip(min_pt, max_pt))
        size = tuple(max(hi - lo, 0.0001) for lo, hi in zip(min_pt, max_pt))
        volume = size[0] * size[1] * size[2]
        defaults = defaults_for_label(str(label))
        youngs_modulus = _float_attr(prim, "organiq:youngsModulusPa", defaults.youngs_modulus_pa)
        softness = _softness(youngs_modulus, defaults.simulation_mode)
        raw_infos.append(
            {
                "label": str(label),
                "mesh_path": path,
                "root_path": str(prim.GetParent().GetPath()),
                "bounds": (min_pt, max_pt),
                "centre": centre,
                "volume": volume,
                "simulation_mode": str(_attr_value(prim, "organiq:simulationMode") or defaults.simulation_mode),
                "mean_hounsfield": _optional_float_attr(prim, "organiq:meanHounsfield"),
                "density_kg_m3": float(_float_attr(prim, "organiq:densityKgM3", defaults.density_kg_m3) or 0.0),
                "youngs_modulus_pa": youngs_modulus,
                "poissons_ratio": _float_attr(prim, "organiq:poissonsRatio", defaults.poissons_ratio),
                "softness": softness,
            }
        )

    bounds = _bounds([point for info in raw_infos for point in info["bounds"]])
    scene_centre = tuple((lo + hi) * 0.5 for lo, hi in zip(bounds[0], bounds[1]))
    radius = max(max(hi - lo for lo, hi in zip(bounds[0], bounds[1])), 0.18)

    infos: list[MeshInfo] = []
    used_names: dict[str, int] = {}
    for index, info in enumerate(raw_infos):
        direction = (
            info["centre"][0] - scene_centre[0],
            info["centre"][1] - scene_centre[1],
            info["centre"][2] - scene_centre[2] * 0.25,
        )
        if _length(direction) < 1.0e-5:
            angle = 2.0 * math.pi * index / max(len(raw_infos), 1)
            direction = (math.cos(angle), math.sin(angle), 0.18)
        unit = _normalised(direction)
        mode = str(info["simulation_mode"])
        softness = float(info["softness"])
        base = radius * (0.12 + 0.22 * softness)
        if mode == "rigid":
            base *= 0.42
        elif mode == "surface_shell":
            base *= 0.26
        offset = (unit[0] * base, unit[1] * base, unit[2] * base * 0.55)
        safe_name = _unique(_safe_name(str(info["label"])), used_names)
        infos.append(
            MeshInfo(
                label=str(info["label"]),
                safe_name=safe_name,
                mesh_path=str(info["mesh_path"]),
                root_path=str(info["root_path"]),
                bounds=info["bounds"],
                centre=info["centre"],
                volume=float(info["volume"]),
                simulation_mode=mode,
                mean_hounsfield=info["mean_hounsfield"],
                density_kg_m3=float(info["density_kg_m3"]),
                youngs_modulus_pa=info["youngs_modulus_pa"],
                poissons_ratio=info["poissons_ratio"],
                offset=offset,
                softness=softness,
            )
        )
    return infos


def _author_materials(stage, UsdShade, Sdf, Gf) -> dict[str, Any]:
    return {
        "wire": _preview_material(
            stage,
            "/World/Showcase/Looks/wireframe_material",
            (0.14, 0.74, 1.0),
            0.88,
            UsdShade,
            Sdf,
            Gf,
            emissive=(0.05, 0.42, 0.80),
        ),
        "callout": _preview_material(
            stage,
            "/World/Showcase/Looks/callout_material",
            (0.70, 0.86, 1.0),
            0.68,
            UsdShade,
            Sdf,
            Gf,
            emissive=(0.10, 0.18, 0.25),
        ),
        "spring": _preview_material(
            stage,
            "/World/Showcase/Looks/elastic_response_material",
            (1.0, 0.42, 0.30),
            0.92,
            UsdShade,
            Sdf,
            Gf,
            emissive=(0.42, 0.08, 0.04),
        ),
        "hu": _preview_material(
            stage,
            "/World/Showcase/Looks/hu_marker_material",
            (0.96, 0.82, 0.38),
            0.88,
            UsdShade,
            Sdf,
            Gf,
            emissive=(0.28, 0.16, 0.02),
        ),
        "panel_back": _preview_material(
            stage,
            "/World/Showcase/Looks/panel_back_material",
            (0.025, 0.027, 0.030),
            0.82,
            UsdShade,
            Sdf,
            Gf,
        ),
        "floor": _preview_material(
            stage,
            "/World/Showcase/Looks/graphite_floor_material",
            (0.028, 0.030, 0.034),
            1.0,
            UsdShade,
            Sdf,
            Gf,
        ),
    }


def _hide_source_physics_visuals(stage, anatomy_path: str, Sdf, Usd, UsdGeom) -> int:
    root = stage.GetPrimAtPath(anatomy_path)
    hidden = 0
    proxy_names = {"cooking_mesh", "collision_tetmesh", "simulation_tetmesh"}
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
        path = str(prim.GetPath())
        name = prim.GetName()
        role = _attr_value(prim, "organiq:role")
        if "/physics" not in path and name not in proxy_names and role != "deformablePhysicsProxy":
            continue
        if not prim.IsA(UsdGeom.Imageable):
            continue
        UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
        prim.CreateAttribute("organiq:showcaseHiddenReason", Sdf.ValueTypeNames.String).Set(
            "visual physics proxy"
        )
        hidden += 1
    return hidden


def _author_anatomy_materials(stage, mesh_infos: list[MeshInfo], UsdGeom, UsdShade, Sdf, Gf) -> None:
    for info in mesh_infos:
        prim = stage.GetPrimAtPath(info.mesh_path)
        if not prim:
            continue
        colour, opacity = _anatomy_colour_and_opacity(info)
        material = _preview_material(
            stage,
            f"/World/Showcase/Looks/anatomy/{info.safe_name}_material",
            colour,
            opacity,
            UsdShade,
            Sdf,
            Gf,
            emissive=tuple(component * 0.025 for component in colour),
        )
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, UsdShade.Tokens.strongerThanDescendants)
        imageable = UsdGeom.Imageable(prim)
        imageable.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
        if prim.IsA(UsdGeom.Gprim):
            gprim = UsdGeom.Gprim(prim)
            gprim.CreateDisplayColorAttr().Set([Gf.Vec3f(*colour)])
            gprim.CreateDisplayOpacityAttr().Set([float(opacity)])


def _anatomy_colour_and_opacity(info: MeshInfo) -> tuple[tuple[float, float, float], float]:
    name = info.safe_name
    hu = info.mean_hounsfield
    if re.search(r"skin|body_surface|outer", name):
        return (0.95, 0.66, 0.54), 0.34
    if re.search(r"rib|vertebra|hip|sacrum|bone|sternum", name):
        return (0.96, 0.88, 0.70), 0.96
    if "lung" in name:
        return (0.33, 0.78, 0.92), 0.78
    if re.search(r"heart|atrium|ventricle|aorta|vein|artery", name):
        return (0.92, 0.18, 0.17), 0.92
    if re.search(r"liver|spleen", name):
        return (0.64, 0.24, 0.48), 0.91
    if "kidney" in name:
        return (0.84, 0.31, 0.44), 0.91
    if re.search(r"colon|bowel|stomach|duodenum|gallbladder|pancreas", name):
        return (0.94, 0.61, 0.24), 0.88
    if hu is None:
        return (0.78, 0.72, 0.66), 0.88
    if hu <= -500.0:
        return (0.30, 0.72, 0.88), 0.78
    if hu >= 250.0:
        return (0.96, 0.88, 0.70), 0.96
    t = max(0.0, min(1.0, (hu + 150.0) / 420.0))
    return (0.62 + 0.26 * t, 0.32 + 0.24 * (1.0 - t), 0.46 + 0.10 * (1.0 - t)), 0.88


def _preview_material(stage, path: str, colour, opacity: float, UsdShade, Sdf, Gf, emissive=None):
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, f"{path}/preview")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*colour))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.62)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))
    if emissive is not None:
        shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*emissive))
    material.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    return material


def _texture_material(stage, path: str, texture_path: Path, anchor: Path, UsdShade, Sdf, Gf):
    material = UsdShade.Material.Define(stage, path)
    primvar = UsdShade.Shader.Define(stage, f"{path}/st_reader")
    primvar.CreateIdAttr("UsdPrimvarReader_float2")
    primvar.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    tex = UsdShade.Shader.Define(stage, f"{path}/card_texture")
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(_relative_asset(texture_path, anchor)))
    tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
        primvar.CreateOutput("result", Sdf.ValueTypeNames.Float2)
    )
    tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
    tex.CreateOutput("a", Sdf.ValueTypeNames.Float)
    shader = UsdShade.Shader.Define(stage, f"{path}/preview")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        tex.GetOutput("rgb")
    )
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(tex.GetOutput("a"))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.58)
    material.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    return material


def _author_environment(stage, bounds, materials, UsdGeom, UsdLux, UsdShade, Gf) -> None:
    min_pt, max_pt = bounds
    centre = tuple((lo + hi) * 0.5 for lo, hi in zip(min_pt, max_pt))
    span = max(max(hi - lo for lo, hi in zip(min_pt, max_pt)), 0.25)
    floor_z = min_pt[2] - span * 0.08

    floor = UsdGeom.Cube.Define(stage, "/World/Showcase/context/floor")
    floor.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(floor).SetTranslate(Gf.Vec3d(centre[0], centre[1], floor_z))
    UsdGeom.XformCommonAPI(floor).SetScale(Gf.Vec3f(span * 1.9, span * 1.45, span * 0.018))
    floor.CreateDisplayColorAttr().Set([Gf.Vec3f(0.028, 0.030, 0.034)])
    UsdShade.MaterialBindingAPI.Apply(floor.GetPrim()).Bind(materials["floor"], UsdShade.Tokens.strongerThanDescendants)

    backdrop = UsdGeom.Cube.Define(stage, "/World/Showcase/context/backdrop")
    backdrop.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(backdrop).SetTranslate(
        Gf.Vec3d(centre[0], max_pt[1] + span * 0.42, centre[2] + span * 0.24)
    )
    UsdGeom.XformCommonAPI(backdrop).SetScale(Gf.Vec3f(span * 1.95, span * 0.018, span * 1.25))
    backdrop.CreateDisplayColorAttr().Set([Gf.Vec3f(0.018, 0.020, 0.024)])
    UsdShade.MaterialBindingAPI.Apply(backdrop.GetPrim()).Bind(materials["floor"], UsdShade.Tokens.strongerThanDescendants)

    dome = UsdLux.DomeLight.Define(stage, "/World/Showcase/lighting/dome")
    dome.CreateIntensityAttr(420.0)
    dome.CreateColorAttr(Gf.Vec3f(0.66, 0.72, 0.80))

    key = UsdLux.RectLight.Define(stage, "/World/Showcase/lighting/key")
    key.CreateIntensityAttr(7600.0)
    key.CreateWidthAttr(span * 1.2)
    key.CreateHeightAttr(span * 0.8)
    UsdGeom.XformCommonAPI(key).SetTranslate(
        Gf.Vec3d(centre[0] - span * 0.45, centre[1] - span * 0.95, max_pt[2] + span * 0.85)
    )
    UsdGeom.XformCommonAPI(key).SetRotate(Gf.Vec3f(58.0, 0.0, -24.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)

    rim = UsdLux.RectLight.Define(stage, "/World/Showcase/lighting/rim")
    rim.CreateIntensityAttr(4200.0)
    rim.CreateWidthAttr(span * 0.55)
    rim.CreateHeightAttr(span * 0.65)
    UsdGeom.XformCommonAPI(rim).SetTranslate(
        Gf.Vec3d(centre[0] + span * 0.72, centre[1] + span * 0.62, max_pt[2] + span * 0.42)
    )
    UsdGeom.XformCommonAPI(rim).SetRotate(Gf.Vec3f(118.0, 0.0, 132.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)


def _author_animation(stage, anatomy_path: str, mesh_infos: list[MeshInfo], UsdGeom, Gf) -> None:
    anatomy = stage.GetPrimAtPath(anatomy_path)
    visibility = UsdGeom.Imageable(anatomy).CreateVisibilityAttr()
    visibility.Set(UsdGeom.Tokens.inherited, 1)
    visibility.Set(UsdGeom.Tokens.inherited, 48)
    visibility.Set(UsdGeom.Tokens.inherited, 72)
    visibility.Set(UsdGeom.Tokens.inherited, 360)

    for info in mesh_infos:
        prim = stage.GetPrimAtPath(info.mesh_path)
        if not prim:
            continue
        xformable = UsdGeom.Xformable(prim)
        translate_op = _get_or_add_translate_op(xformable, "showcaseOffset")
        scale_op = _get_or_add_scale_op(xformable, "showcaseElasticScale")
        offset = info.offset
        bounce = 0.08 + info.softness * 0.12
        translate_samples = {
            1: (0.0, 0.0, 0.0),
            96: (0.0, 0.0, 0.0),
            154: (offset[0] * 0.32, offset[1] * 0.32, offset[2] * 0.32),
            218: offset,
            266: (offset[0] * (1.0 + bounce), offset[1] * (1.0 + bounce), offset[2] * (1.0 + bounce)),
            316: (offset[0] * 0.24, offset[1] * 0.24, offset[2] * 0.24),
            360: (0.0, 0.0, 0.0),
        }
        for frame, value in translate_samples.items():
            translate_op.Set(Gf.Vec3d(*value), frame)

        stretch = 0.01 + info.softness * 0.055
        if info.simulation_mode == "rigid":
            stretch *= 0.04
        elif info.simulation_mode == "surface_shell":
            stretch *= 0.28
        scale_samples = {
            1: (1.0, 1.0, 1.0),
            208: (1.0, 1.0, 1.0),
            252: (1.0 + stretch, 1.0 - stretch * 0.42, 1.0 + stretch * 0.18),
            300: (1.0 - stretch * 0.30, 1.0 + stretch * 0.24, 1.0),
            360: (1.0, 1.0, 1.0),
        }
        for frame, value in scale_samples.items():
            scale_op.Set(Gf.Vec3f(*value), frame)


def _get_or_add_translate_op(xformable, suffix: str):
    for op in xformable.GetOrderedXformOps():
        if op.GetOpName() == f"xformOp:translate:{suffix}":
            return op
    return xformable.AddTranslateOp(opSuffix=suffix)


def _get_or_add_scale_op(xformable, suffix: str):
    for op in xformable.GetOrderedXformOps():
        if op.GetOpName() == f"xformOp:scale:{suffix}":
            return op
    return xformable.AddScaleOp(opSuffix=suffix)


def _author_wireframes(stage, mesh_infos: list[MeshInfo], materials, Usd, UsdGeom, Gf) -> int:
    root = UsdGeom.Scope.Define(stage, "/World/Showcase/wireframe")
    visibility = UsdGeom.Imageable(root.GetPrim()).CreateVisibilityAttr()
    visibility.Set(UsdGeom.Tokens.inherited, 1)
    visibility.Set(UsdGeom.Tokens.inherited, 118)
    visibility.Set(UsdGeom.Tokens.invisible, 136)
    visibility.Set(UsdGeom.Tokens.invisible, 360)

    total_segments = 0
    for info in mesh_infos:
        prim = stage.GetPrimAtPath(info.mesh_path)
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get() or []
        face_counts = mesh.GetFaceVertexCountsAttr().Get() or []
        face_indices = mesh.GetFaceVertexIndicesAttr().Get() or []
        if not points or not face_counts or not face_indices:
            continue
        matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        edges = _sample_edges(face_counts, face_indices, max_edges=3200)
        curve_points = []
        for first, second in edges:
            curve_points.append(matrix.Transform(Gf.Vec3d(points[first])))
            curve_points.append(matrix.Transform(Gf.Vec3d(points[second])))
        if not curve_points:
            continue
        curve = UsdGeom.BasisCurves.Define(stage, f"/World/Showcase/wireframe/{info.safe_name}_wire")
        curve.CreateTypeAttr(UsdGeom.Tokens.linear)
        curve.CreateCurveVertexCountsAttr([2 for _ in edges])
        curve.CreatePointsAttr(curve_points)
        curve.CreateWidthsAttr([0.0012 + info.softness * 0.0008])
        curve.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        curve.CreateDisplayColorAttr().Set([Gf.Vec3f(*_hu_colour(info.mean_hounsfield))])
        from pxr import UsdShade

        UsdShade.MaterialBindingAPI.Apply(curve.GetPrim()).Bind(
            materials["wire"], UsdShade.Tokens.strongerThanDescendants
        )
        total_segments += len(edges)
    return total_segments


def _sample_edges(face_counts, face_indices, max_edges: int) -> list[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    cursor = 0
    for face_count in face_counts:
        count = int(face_count)
        face = [int(index) for index in face_indices[cursor : cursor + count]]
        cursor += count
        if len(face) < 2:
            continue
        for i, first in enumerate(face):
            second = face[(i + 1) % len(face)]
            if first == second:
                continue
            edges.add(tuple(sorted((first, second))))
    ordered = sorted(edges)
    if len(ordered) <= max_edges:
        return ordered
    step = max(1, int(math.ceil(len(ordered) / max_edges)))
    return ordered[::step][:max_edges]


def _author_elastic_response(stage, mesh_infos: list[MeshInfo], materials, UsdGeom, Gf) -> None:
    root = UsdGeom.Scope.Define(stage, "/World/Showcase/elastic_response")
    visibility = UsdGeom.Imageable(root.GetPrim()).CreateVisibilityAttr()
    visibility.Set(UsdGeom.Tokens.invisible, 1)
    visibility.Set(UsdGeom.Tokens.invisible, 160)
    visibility.Set(UsdGeom.Tokens.inherited, 184)
    visibility.Set(UsdGeom.Tokens.inherited, 360)
    for info in mesh_infos:
        if info.simulation_mode == "surface_shell":
            continue
        end = tuple(info.centre[i] + info.offset[i] for i in range(3))
        points = _coil_points(info.centre, end, 5 + int(info.softness * 6.0), 72, 0.006 + info.softness * 0.012)
        curve = UsdGeom.BasisCurves.Define(stage, f"/World/Showcase/elastic_response/{info.safe_name}_elastic_path")
        curve.CreateTypeAttr(UsdGeom.Tokens.linear)
        curve.CreateCurveVertexCountsAttr([len(points)])
        curve.CreatePointsAttr([Gf.Vec3f(*point) for point in points])
        curve.CreateWidthsAttr([0.0022 + info.softness * 0.0018])
        curve.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        from pxr import UsdShade

        material = materials["spring"] if info.simulation_mode != "rigid" else materials["hu"]
        UsdShade.MaterialBindingAPI.Apply(curve.GetPrim()).Bind(material, UsdShade.Tokens.strongerThanDescendants)
        curve.GetPrim().CreateAttribute("organiq:youngsModulusPa", _sdf_float_type()).Set(
            float(info.youngs_modulus_pa or 0.0)
        )


def _coil_points(start, end, coils: int, steps: int, amplitude: float) -> list[tuple[float, float, float]]:
    axis = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
    length = _length(axis)
    if length < 1.0e-6:
        return [start, end]
    forward = _normalised(axis)
    reference = (0.0, 0.0, 1.0) if abs(forward[2]) < 0.92 else (1.0, 0.0, 0.0)
    side = _normalised(_cross(forward, reference))
    up = _normalised(_cross(side, forward))
    values = []
    for index in range(steps):
        t = index / float(max(steps - 1, 1))
        phase = t * math.pi * 2.0 * max(coils, 1)
        envelope = math.sin(math.pi * t)
        radial = (
            math.cos(phase) * side[0] + math.sin(phase) * up[0],
            math.cos(phase) * side[1] + math.sin(phase) * up[1],
            math.cos(phase) * side[2] + math.sin(phase) * up[2],
        )
        values.append(
            (
                start[0] + axis[0] * t + radial[0] * amplitude * envelope,
                start[1] + axis[1] * t + radial[1] * amplitude * envelope,
                start[2] + axis[2] * t + radial[2] * amplitude * envelope,
            )
        )
    return values


def _author_metadata_panels(
    stage,
    mesh_infos: list[MeshInfo],
    bounds,
    card_dir: Path,
    anchor: Path,
    max_panels: int,
    materials,
    Sdf,
    UsdGeom,
    UsdShade,
    Gf,
) -> list[dict[str, Any]]:
    selected = _select_panel_infos(mesh_infos, max_panels)
    if not selected:
        return []
    min_pt, max_pt = bounds
    centre = tuple((lo + hi) * 0.5 for lo, hi in zip(min_pt, max_pt))
    span = max(max(hi - lo for lo, hi in zip(min_pt, max_pt)), 0.25)
    root = UsdGeom.Scope.Define(stage, "/World/Showcase/metadata")
    visibility = UsdGeom.Imageable(root.GetPrim()).CreateVisibilityAttr()
    visibility.Set(UsdGeom.Tokens.invisible, 1)
    visibility.Set(UsdGeom.Tokens.invisible, 92)
    visibility.Set(UsdGeom.Tokens.inherited, 126)
    visibility.Set(UsdGeom.Tokens.inherited, 360)

    card_width = span * 0.42
    card_height = card_width * 0.45
    gap = card_height * 0.17
    base_x = max_pt[0] + span * 0.48
    base_y = centre[1] - span * 0.64
    start_z = centre[2] + (len(selected) - 1) * (card_height + gap) * 0.5
    panels: list[dict[str, Any]] = []

    for index, info in enumerate(selected):
        texture = _write_card_texture(card_dir, info, index)
        material = _texture_material(
            stage,
            f"/World/Showcase/Looks/{info.safe_name}_metadata_card_material",
            texture,
            anchor,
            UsdShade,
            Sdf,
            Gf,
        )
        z = start_z - index * (card_height + gap)
        card_centre = (base_x, base_y, z)
        _author_card_mesh(
            stage,
            f"/World/Showcase/metadata/{info.safe_name}_card",
            card_centre,
            card_width,
            card_height,
            material,
            UsdGeom,
            UsdShade,
            Gf,
            Sdf,
        )
        _author_metadata_bars(
            stage,
            info,
            (base_x - card_width * 0.42, base_y - 0.001, z - card_height * 0.31),
            card_width,
            card_height,
            materials,
            UsdGeom,
            UsdShade,
            Gf,
        )
        _author_callout(stage, info.centre, (base_x - card_width * 0.52, base_y, z), materials, UsdGeom, UsdShade, Gf)
        panels.append(
            {
                "label": info.label,
                "mean_hounsfield": info.mean_hounsfield,
                "density_kg_m3": info.density_kg_m3,
                "youngs_modulus_pa": info.youngs_modulus_pa,
                "texture": str(texture),
            }
        )
    return panels


def _select_panel_infos(mesh_infos: list[MeshInfo], max_panels: int) -> list[MeshInfo]:
    limit = max(1, max_panels)
    ordered = sorted(mesh_infos, key=lambda item: item.volume, reverse=True)
    selected: list[MeshInfo] = []

    def add_first(pattern: str) -> None:
        if len(selected) >= limit:
            return
        match = next((item for item in ordered if re.search(pattern, item.safe_name) and item not in selected), None)
        if match is not None:
            selected.append(match)

    for pattern in (
        r"skin_shell|outer_skin|body_surface",
        r"heart",
        r"lung",
        r"hip|rib|vertebra|bone|sacrum",
        r"kidney",
        r"liver|spleen|colon|small_bowel|stomach",
    ):
        add_first(pattern)

    for item in ordered:
        if len(selected) >= limit:
            break
        if item not in selected:
            selected.append(item)
    return selected


def _write_card_texture(card_dir: Path, info: MeshInfo, index: int) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    path = card_dir / f"{index + 1:02d}_{info.safe_name}.png"
    width, height = 1024, 420
    image = Image.new("RGBA", (width, height), (8, 9, 11, 232))
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("arial.ttf", 52)
        body_font = ImageFont.truetype("arial.ttf", 34)
        small_font = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        title_font = body_font = small_font = ImageFont.load_default()

    hu_colour = tuple(int(c * 255.0) for c in _hu_colour(info.mean_hounsfield))
    elastic_colour = tuple(int(c * 255.0) for c in _elastic_colour(info.youngs_modulus_pa, info.simulation_mode))
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=28, outline=(120, 160, 190, 180), width=3)
    draw.rectangle((0, 0, 22, height), fill=hu_colour + (255,))
    draw.text((52, 38), _display_label(info.label), fill=(244, 246, 248, 255), font=title_font)
    draw.text((56, 118), f"{info.simulation_mode.replace('_', ' ')} physics", fill=(176, 190, 204, 255), font=small_font)

    hu_text = "HU n/a" if info.mean_hounsfield is None else f"HU {info.mean_hounsfield:.0f}"
    density_text = f"density {info.density_kg_m3:.0f} kg/m3"
    youngs_text = _youngs_text(info.youngs_modulus_pa)
    poisson_text = "Poisson n/a" if info.poissons_ratio is None else f"Poisson {info.poissons_ratio:.2f}"
    draw.text((56, 188), hu_text, fill=(252, 236, 194, 255), font=body_font)
    draw.text((56, 242), density_text, fill=(228, 231, 235, 255), font=body_font)
    draw.text((530, 188), youngs_text, fill=(255, 178, 154, 255), font=body_font)
    draw.text((530, 242), poisson_text, fill=(228, 231, 235, 255), font=body_font)

    _draw_bar(draw, (56, 330, 458, 356), _hu_normalised(info.mean_hounsfield), hu_colour)
    _draw_bar(draw, (530, 330, 932, 356), _elastic_normalised(info.youngs_modulus_pa), elastic_colour)
    draw.text((56, 362), "radiodensity", fill=(148, 162, 176, 255), font=small_font)
    draw.text((530, 362), "elasticity", fill=(148, 162, 176, 255), font=small_font)
    image.save(path)
    return path


def _draw_bar(draw, box, value: float, colour: tuple[int, int, int]) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=7, outline=(86, 96, 108, 255), width=2, fill=(24, 28, 32, 255))
    fill_right = left + int((right - left) * max(0.02, min(value, 1.0)))
    draw.rounded_rectangle((left, top, fill_right, bottom), radius=7, fill=colour + (255,))


def _author_card_mesh(stage, path, centre, width, height, material, UsdGeom, UsdShade, Gf, Sdf) -> None:
    x, y, z = centre
    half_w = width * 0.5
    half_h = height * 0.5
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(
        [
            Gf.Vec3f(x - half_w, y, z - half_h),
            Gf.Vec3f(x + half_w, y, z - half_h),
            Gf.Vec3f(x + half_w, y, z + half_h),
            Gf.Vec3f(x - half_w, y, z + half_h),
        ]
    )
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateDoubleSidedAttr().Set(True)
    st = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
    )
    st.Set([Gf.Vec2f(0, 0), Gf.Vec2f(1, 0), Gf.Vec2f(1, 1), Gf.Vec2f(0, 1)])
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material, UsdShade.Tokens.strongerThanDescendants)


def _author_metadata_bars(stage, info, origin, card_width, card_height, materials, UsdGeom, UsdShade, Gf) -> None:
    hu_value = _hu_normalised(info.mean_hounsfield)
    elasticity_value = _elastic_normalised(info.youngs_modulus_pa)
    bar_width = card_width * 0.32
    bar_height = card_height * 0.028
    for name, value, z_offset, material_key in (
        ("hu", hu_value, 0.0, "hu"),
        ("elasticity", elasticity_value, -card_height * 0.075, "spring"),
    ):
        bar = UsdGeom.Cube.Define(stage, f"/World/Showcase/metadata/{info.safe_name}_{name}_bar")
        bar.CreateSizeAttr(1.0)
        scale_x = max(bar_width * value, bar_width * 0.025)
        UsdGeom.XformCommonAPI(bar).SetTranslate(
            Gf.Vec3d(origin[0] + scale_x * 0.5, origin[1], origin[2] + z_offset)
        )
        UsdGeom.XformCommonAPI(bar).SetScale(Gf.Vec3f(scale_x, 0.0015, bar_height))
        UsdShade.MaterialBindingAPI.Apply(bar.GetPrim()).Bind(
            materials[material_key], UsdShade.Tokens.strongerThanDescendants
        )


def _author_callout(stage, start, end, materials, UsdGeom, UsdShade, Gf) -> None:
    curve = UsdGeom.BasisCurves.Define(stage, f"/World/Showcase/metadata/callout_{_safe_name(str(end))}")
    bend = ((start[0] + end[0]) * 0.5, min(start[1], end[1]) - 0.03, (start[2] + end[2]) * 0.5)
    curve.CreateTypeAttr(UsdGeom.Tokens.linear)
    curve.CreateCurveVertexCountsAttr([3])
    curve.CreatePointsAttr([Gf.Vec3f(*start), Gf.Vec3f(*bend), Gf.Vec3f(*end)])
    curve.CreateWidthsAttr([0.002])
    curve.SetWidthsInterpolation(UsdGeom.Tokens.constant)
    UsdShade.MaterialBindingAPI.Apply(curve.GetPrim()).Bind(materials["callout"], UsdShade.Tokens.strongerThanDescendants)


def _author_cameras(stage, bounds, mesh_infos: list[MeshInfo], UsdGeom, Gf) -> str:
    min_pt, max_pt = bounds
    centre = tuple((lo + hi) * 0.5 for lo, hi in zip(min_pt, max_pt))
    radius = max(max(hi - lo for lo, hi in zip(min_pt, max_pt)), 0.18)
    camera_path = "/World/Showcase/Cameras/main_camera"
    camera = UsdGeom.Camera.Define(stage, camera_path)
    camera.CreateFocalLengthAttr(70.0)
    camera.CreateFocusDistanceAttr(radius * 2.2)
    camera.CreateFStopAttr(4.0)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.001, max(radius * 30.0, 10.0)))
    xform = UsdGeom.Xformable(camera)
    try:
        xform.ClearXformOpOrder()
    except Exception:
        pass
    op = xform.MakeMatrixXform()
    for frame, eye, target in (
        (
            1,
            (centre[0] + radius * 1.25, centre[1] - radius * 2.25, centre[2] + radius * 0.82),
            (centre[0], centre[1], centre[2] + radius * 0.03),
        ),
        (
            120,
            (centre[0] + radius * 1.05, centre[1] - radius * 2.05, centre[2] + radius * 0.62),
            (centre[0], centre[1], centre[2] + radius * 0.04),
        ),
        (
            220,
            (centre[0] + radius * 1.85, centre[1] - radius * 1.65, centre[2] + radius * 0.70),
            (centre[0] + radius * 0.24, centre[1] - radius * 0.06, centre[2]),
        ),
        (
            360,
            (centre[0] - radius * 0.85, centre[1] - radius * 2.15, centre[2] + radius * 0.82),
            (centre[0] + radius * 0.08, centre[1], centre[2]),
        ),
    ):
        matrix = Gf.Matrix4d(1.0)
        matrix.SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0.0, 0.0, 1.0))
        op.Set(matrix.GetInverse(), frame)

    for name, eye, target in (
        ("wireframe_camera", (centre[0] + radius * 1.15, centre[1] - radius * 2.10, centre[2] + radius * 0.72), centre),
        ("exploded_camera", (centre[0] + radius * 1.75, centre[1] - radius * 1.55, centre[2] + radius * 0.68), centre),
        ("metadata_camera", (centre[0] + radius * 1.35, centre[1] - radius * 2.45, centre[2] + radius * 0.72), (centre[0] + radius * 0.36, centre[1] - radius * 0.28, centre[2])),
    ):
        _static_camera(stage, f"/World/Showcase/Cameras/{name}", eye, target, radius, UsdGeom, Gf)
    return camera_path


def _static_camera(stage, path, eye, target, radius, UsdGeom, Gf) -> None:
    camera = UsdGeom.Camera.Define(stage, path)
    view = Gf.Matrix4d(1.0)
    view.SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0.0, 0.0, 1.0))
    xform = UsdGeom.Xformable(camera)
    try:
        xform.ClearXformOpOrder()
    except Exception:
        pass
    xform.MakeMatrixXform().Set(view.GetInverse())
    camera.CreateFocalLengthAttr(72.0)
    camera.CreateFocusDistanceAttr(radius * 2.1)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.001, max(radius * 30.0, 10.0)))


def _author_render_settings(stage, camera_path: str, UsdRender, Sdf, Gf) -> None:
    settings = UsdRender.Settings.Define(stage, "/Render/Settings")
    product = UsdRender.Product.Define(stage, "/Render/Products/hero")
    product.CreateCameraRel().SetTargets([Sdf.Path(camera_path)])
    product.CreateResolutionAttr().Set(Gf.Vec2i(1920, 1080))
    settings.CreateProductsRel().SetTargets([product.GetPath()])
    prim = product.GetPrim()
    prim.CreateAttribute("omni:rtx:rendermode", Sdf.ValueTypeNames.String).Set("PathTracing")
    prim.CreateAttribute("omni:rtx:pt:samplesPerPixel", Sdf.ValueTypeNames.Int).Set(512)
    prim.CreateAttribute("omni:rtx:pt:limits:maxBounces", Sdf.ValueTypeNames.Int).Set(6)
    prim.CreateAttribute("omni:rtx:pt:maxVolumeBounces", Sdf.ValueTypeNames.Int).Set(16)
    prim.CreateAttribute("omni:rtx:pt:fractionalCutoutOpacity", Sdf.ValueTypeNames.Bool).Set(True)
    prim.CreateAttribute("omni:rtx:pt:denoising:enabled", Sdf.ValueTypeNames.Bool).Set(True)


def _write_shot_manifest(shots_path: Path, output: Path, camera_path: str) -> list[dict[str, Any]]:
    shots = [
        {
            "name": "wireframe to rendered",
            "start_frame": 1,
            "end_frame": 136,
            "camera": camera_path,
            "purpose": "show the CT-derived surface becoming rendered anatomy",
        },
        {
            "name": "radiodensity and material response",
            "start_frame": 96,
            "end_frame": 190,
            "camera": "/World/Showcase/Cameras/metadata_camera",
            "purpose": "show HU values beside the organs they describe",
        },
        {
            "name": "organ separation",
            "start_frame": 154,
            "end_frame": 246,
            "camera": "/World/Showcase/Cameras/exploded_camera",
            "purpose": "show reusable anatomy components moving apart without losing material or physics bindings",
        },
        {
            "name": "elastic response",
            "start_frame": 230,
            "end_frame": 360,
            "camera": camera_path,
            "purpose": "show stiffness-scaled motion paths from the authored elasticity values",
        },
    ]
    shots_path.write_text(
        json.dumps({"showcase_usd": str(output), "time_codes_per_second": 60, "shots": shots}, indent=2),
        encoding="utf-8",
    )
    return shots


def _create_physics_probe(
    source_usd: Path,
    output: Path,
    report_path: Path,
    defaults_for_label,
    Usd,
    UsdGeom,
    UsdLux,
    UsdPhysics,
    UsdRender,
    UsdShade,
    Sdf,
    Gf,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    source_stage = Usd.Stage.Open(str(source_usd))
    _require(source_stage is not None, f"could not open source USD: {source_usd}")
    source_meshes = _collect_source_probe_meshes(source_stage, defaults_for_label, Usd, UsdGeom, Gf)
    soft_info, stiff_info = _select_probe_meshes(source_meshes)

    stage = Usd.Stage.CreateNew(str(output))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    stage.SetStartTimeCode(1.0)
    stage.SetEndTimeCode(240.0)
    stage.SetFramesPerSecond(60.0)
    stage.SetTimeCodesPerSecond(60.0)

    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    root = UsdGeom.Xform.Define(stage, "/World/PhysicsProbe")
    root.GetPrim().CreateAttribute("organiq:sourceUsd", Sdf.ValueTypeNames.Asset).Set(str(source_usd))
    root.GetPrim().CreateAttribute("organiq:purpose", Sdf.ValueTypeNames.String).Set(
        "focused tissue physics demonstration"
    )

    materials = _author_probe_materials(stage, soft_info, stiff_info, UsdShade, Sdf, Gf)
    _author_probe_environment(stage, materials, UsdGeom, UsdLux, UsdPhysics, UsdShade, Gf)
    soft_result = _author_probe_mesh(
        stage,
        "/World/PhysicsProbe/Tissue/soft_organ",
        soft_info,
        (-0.18, 0.02),
        0.36,
        0.052,
        materials["soft"],
        UsdGeom,
        UsdPhysics,
        UsdShade,
        Sdf,
        Gf,
    )
    stiff_result = _author_probe_mesh(
        stage,
        "/World/PhysicsProbe/Tissue/stiff_reference",
        stiff_info,
        (0.24, 0.02),
        0.30,
        0.010,
        materials["stiff"],
        UsdGeom,
        UsdPhysics,
        UsdShade,
        Sdf,
        Gf,
    )
    contact = _author_probe_manipulator(stage, soft_result, materials, UsdGeom, UsdPhysics, UsdShade, Sdf, Gf)
    camera_path = _author_probe_camera(stage, Gf, UsdGeom)
    _author_render_settings(stage, camera_path, UsdRender, Sdf, Gf)

    _set_layer_custom_data(
        stage,
        {
            "cameraPrim": camera_path,
            "showcasePrim": "/World/PhysicsProbe",
            "initialTimeCode": 118.0,
        },
    )
    stage.GetRootLayer().Save()

    report = {
        "status": "ok",
        "source_usd": str(source_usd),
        "probe_usd": str(output),
        "camera_path": camera_path,
        "soft_mesh": soft_result,
        "stiff_reference_mesh": stiff_result,
        "contact": contact,
        "time_codes_per_second": 60,
        "frames": {
            "approach": 60,
            "contact": 96,
            "max_compression": 118,
            "rebound": 190,
            "reset": 240,
        },
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _collect_source_probe_meshes(source_stage, defaults_for_label, Usd, UsdGeom, Gf) -> list[dict[str, Any]]:
    root = source_stage.GetPrimAtPath("/World/organiq")
    _require(root.IsValid(), "source USD does not contain /World/organiq")
    meshes: list[dict[str, Any]] = []
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
        if prim.IsInstanceProxy() or not prim.IsA(UsdGeom.Mesh):
            continue
        label = _attr_value(prim, "organiq:labelName")
        if not label:
            continue
        role = _attr_value(prim, "organiq:role")
        path = str(prim.GetPath())
        if role == "deformablePhysicsProxy" or "/physics/" in path:
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get() or []
        face_counts = mesh.GetFaceVertexCountsAttr().Get() or []
        face_indices = mesh.GetFaceVertexIndicesAttr().Get() or []
        if not points or not face_counts or not face_indices:
            continue
        matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world_points = [matrix.Transform(Gf.Vec3d(point)) for point in points]
        bounds = _bounds([_vec3_tuple(point) for point in world_points])
        min_pt, max_pt = bounds
        size = tuple(max(hi - lo, 0.0001) for lo, hi in zip(min_pt, max_pt))
        defaults = defaults_for_label(str(label))
        meshes.append(
            {
                "label": str(label),
                "safe_name": _safe_name(str(label)),
                "points": world_points,
                "face_counts": [int(count) for count in face_counts],
                "face_indices": [int(index) for index in face_indices],
                "bounds": bounds,
                "size": size,
                "centre": tuple((lo + hi) * 0.5 for lo, hi in zip(min_pt, max_pt)),
                "volume": size[0] * size[1] * size[2],
                "mean_hounsfield": _optional_float_attr(prim, "organiq:meanHounsfield"),
                "density_kg_m3": float(_float_attr(prim, "organiq:densityKgM3", defaults.density_kg_m3) or 0.0),
                "youngs_modulus_pa": _float_attr(prim, "organiq:youngsModulusPa", defaults.youngs_modulus_pa),
                "poissons_ratio": _float_attr(prim, "organiq:poissonsRatio", defaults.poissons_ratio),
                "simulation_mode": str(_attr_value(prim, "organiq:simulationMode") or defaults.simulation_mode),
            }
        )
    _require(meshes, "source USD did not expose suitable render meshes")
    return meshes


def _select_probe_meshes(meshes: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    def pick(patterns: tuple[str, ...], exclude: dict[str, Any] | None = None) -> dict[str, Any] | None:
        candidates = sorted(meshes, key=lambda item: float(item["volume"]), reverse=True)
        for pattern in patterns:
            match = next(
                (
                    item
                    for item in candidates
                    if item is not exclude and re.search(pattern, str(item["safe_name"]))
                ),
                None,
            )
            if match is not None:
                return match
        return None

    soft = pick(("kidney", "liver", "spleen", "heart", "lung", "colon", "stomach"))
    if soft is None:
        soft = min(meshes, key=lambda item: float(item.get("youngs_modulus_pa") or 1.0e9))
    stiff = pick(("hip", "vertebra", "rib", "sacrum", "bone", "sternum"), exclude=soft)
    if stiff is None:
        stiff = max((item for item in meshes if item is not soft), key=lambda item: float(item.get("youngs_modulus_pa") or 0.0))
    return soft, stiff


def _author_probe_materials(stage, soft_info, stiff_info, UsdShade, Sdf, Gf) -> dict[str, Any]:
    soft_colour, soft_opacity = _anatomy_colour_and_opacity(
        MeshInfo(
            label=str(soft_info["label"]),
            safe_name=str(soft_info["safe_name"]),
            mesh_path="",
            root_path="",
            bounds=soft_info["bounds"],
            centre=soft_info["centre"],
            volume=float(soft_info["volume"]),
            simulation_mode=str(soft_info["simulation_mode"]),
            mean_hounsfield=soft_info["mean_hounsfield"],
            density_kg_m3=float(soft_info["density_kg_m3"]),
            youngs_modulus_pa=soft_info["youngs_modulus_pa"],
            poissons_ratio=soft_info["poissons_ratio"],
            offset=(0.0, 0.0, 0.0),
            softness=1.0,
        )
    )
    stiff_colour, stiff_opacity = _anatomy_colour_and_opacity(
        MeshInfo(
            label=str(stiff_info["label"]),
            safe_name=str(stiff_info["safe_name"]),
            mesh_path="",
            root_path="",
            bounds=stiff_info["bounds"],
            centre=stiff_info["centre"],
            volume=float(stiff_info["volume"]),
            simulation_mode=str(stiff_info["simulation_mode"]),
            mean_hounsfield=stiff_info["mean_hounsfield"],
            density_kg_m3=float(stiff_info["density_kg_m3"]),
            youngs_modulus_pa=stiff_info["youngs_modulus_pa"],
            poissons_ratio=stiff_info["poissons_ratio"],
            offset=(0.0, 0.0, 0.0),
            softness=0.1,
        )
    )
    materials = {
        "soft": _preview_material(stage, "/World/PhysicsProbe/Looks/soft_tissue", soft_colour, max(soft_opacity, 0.86), UsdShade, Sdf, Gf),
        "stiff": _preview_material(stage, "/World/PhysicsProbe/Looks/stiff_reference", stiff_colour, stiff_opacity, UsdShade, Sdf, Gf),
        "tray": _preview_material(stage, "/World/PhysicsProbe/Looks/brushed_graphite_tray", (0.035, 0.038, 0.042), 1.0, UsdShade, Sdf, Gf),
        "tool": _preview_material(stage, "/World/PhysicsProbe/Looks/ceramic_press_tool", (0.86, 0.90, 0.92), 1.0, UsdShade, Sdf, Gf),
        "pressure": _preview_material(stage, "/World/PhysicsProbe/Looks/pressure_field", (1.0, 0.36, 0.20), 0.72, UsdShade, Sdf, Gf, emissive=(0.42, 0.04, 0.02)),
        "spring": _preview_material(stage, "/World/PhysicsProbe/Looks/elastic_path", (1.0, 0.76, 0.22), 0.88, UsdShade, Sdf, Gf, emissive=(0.32, 0.18, 0.02)),
    }
    _apply_deformable_material_metadata(materials["soft"], soft_info)
    _apply_deformable_material_metadata(materials["stiff"], stiff_info)
    return materials


def _apply_deformable_material_metadata(material, info) -> None:
    try:
        from pxr import PhysxSchema

        api = PhysxSchema.PhysxDeformableBodyMaterialAPI.Apply(material.GetPrim())
        api.CreateYoungsModulusAttr().Set(float(info.get("youngs_modulus_pa") or 0.0))
        api.CreatePoissonsRatioAttr().Set(float(info.get("poissons_ratio") or 0.4))
        api.CreateDynamicFrictionAttr().Set(0.58)
        api.CreateElasticityDampingAttr().Set(0.08)
    except Exception:
        prim = material.GetPrim()
        prim.CreateAttribute("physxDeformableBodyMaterial:youngsModulus", _sdf_float_type()).Set(
            float(info.get("youngs_modulus_pa") or 0.0)
        )
        prim.CreateAttribute("physxDeformableBodyMaterial:poissonsRatio", _sdf_float_type()).Set(
            float(info.get("poissons_ratio") or 0.4)
        )


def _author_probe_environment(stage, materials, UsdGeom, UsdLux, UsdPhysics, UsdShade, Gf) -> None:
    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsProbe/physics_scene")
    scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0.0, 0.0, -1.0))
    scene.CreateGravityMagnitudeAttr().Set(9.81)

    for path, translate, scale in (
        ("/World/PhysicsProbe/Tray/base", (0.02, 0.03, -0.026), (0.74, 0.46, 0.018)),
        ("/World/PhysicsProbe/Tray/back_rail", (0.02, 0.255, 0.025), (0.74, 0.018, 0.050)),
        ("/World/PhysicsProbe/Tray/front_rail", (0.02, -0.195, 0.025), (0.74, 0.018, 0.050)),
        ("/World/PhysicsProbe/Tray/left_rail", (-0.365, 0.03, 0.025), (0.018, 0.46, 0.050)),
        ("/World/PhysicsProbe/Tray/right_rail", (0.405, 0.03, 0.025), (0.018, 0.46, 0.050)),
    ):
        cube = UsdGeom.Cube.Define(stage, path)
        cube.CreateSizeAttr(1.0)
        UsdGeom.XformCommonAPI(cube).SetTranslate(Gf.Vec3d(*translate))
        UsdGeom.XformCommonAPI(cube).SetScale(Gf.Vec3f(*scale))
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(materials["tray"], UsdShade.Tokens.strongerThanDescendants)
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())

    dome = UsdLux.DomeLight.Define(stage, "/World/PhysicsProbe/Lighting/dome")
    dome.CreateIntensityAttr(360.0)
    dome.CreateColorAttr(Gf.Vec3f(0.68, 0.72, 0.78))
    key = UsdLux.RectLight.Define(stage, "/World/PhysicsProbe/Lighting/key")
    key.CreateIntensityAttr(8400.0)
    key.CreateWidthAttr(0.75)
    key.CreateHeightAttr(0.42)
    UsdGeom.XformCommonAPI(key).SetTranslate(Gf.Vec3d(-0.35, -0.72, 0.86))
    UsdGeom.XformCommonAPI(key).SetRotate(Gf.Vec3f(58.0, 0.0, -18.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)
    rim = UsdLux.RectLight.Define(stage, "/World/PhysicsProbe/Lighting/rim")
    rim.CreateIntensityAttr(4200.0)
    rim.CreateWidthAttr(0.35)
    rim.CreateHeightAttr(0.46)
    UsdGeom.XformCommonAPI(rim).SetTranslate(Gf.Vec3d(0.55, 0.40, 0.62))
    UsdGeom.XformCommonAPI(rim).SetRotate(Gf.Vec3f(110.0, 0.0, 142.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)


def _author_probe_mesh(
    stage,
    path: str,
    info: dict[str, Any],
    tray_xy: tuple[float, float],
    target_size: float,
    max_compression: float,
    material,
    UsdGeom,
    UsdPhysics,
    UsdShade,
    Sdf,
    Gf,
) -> dict[str, Any]:
    mesh = UsdGeom.Mesh.Define(stage, path)
    base_points, local = _probe_base_points(info, tray_xy, target_size, Gf)
    min_z = min(point[2] for point in base_points)
    max_z = max(point[2] for point in base_points)
    height = max(max_z - min_z, 0.001)
    press_xy = tray_xy
    radius = max(target_size * 0.27, 0.035)
    frame_values = (
        (1, 0.0),
        (44, 0.0),
        (84, 0.30),
        (118, 1.0),
        (150, 0.82),
        (190, 0.28),
        (240, 0.0),
    )
    for frame, press in frame_values:
        deformed = [
            _deformed_probe_point(point, press_xy, min_z, height, radius, max_compression * press)
            for point in base_points
        ]
        mesh.CreatePointsAttr().Set([Gf.Vec3f(*point) for point in deformed], frame)
    mesh.CreateFaceVertexCountsAttr(info["face_counts"])
    mesh.CreateFaceVertexIndicesAttr(info["face_indices"])
    mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    mesh.CreateDisplayColorAttr().Set([Gf.Vec3f(*_hu_colour(info["mean_hounsfield"]))])
    mesh.CreateDisplayOpacityAttr().Set([0.96])
    mesh.GetPrim().CreateAttribute("organiq:labelName", Sdf.ValueTypeNames.String).Set(str(info["label"]))
    mesh.GetPrim().CreateAttribute("organiq:simulationMode", Sdf.ValueTypeNames.String).Set(
        str(info["simulation_mode"])
    )
    mesh.GetPrim().CreateAttribute("organiq:meanHounsfield", _sdf_float_type()).Set(
        float(info["mean_hounsfield"] or 0.0)
    )
    mesh.GetPrim().CreateAttribute("organiq:densityKgM3", _sdf_float_type()).Set(float(info["density_kg_m3"]))
    mesh.GetPrim().CreateAttribute("organiq:youngsModulusPa", _sdf_float_type()).Set(
        float(info["youngs_modulus_pa"] or 0.0)
    )
    mesh.GetPrim().CreateAttribute("organiq:maxVisualCompressionM", _sdf_float_type()).Set(float(max_compression))
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material, UsdShade.Tokens.strongerThanDescendants)
    _apply_probe_physics(mesh.GetPrim(), info, UsdPhysics)

    return {
        "label": str(info["label"]),
        "prim_path": path,
        "mean_hounsfield": info["mean_hounsfield"],
        "density_kg_m3": float(info["density_kg_m3"]),
        "youngs_modulus_pa": float(info["youngs_modulus_pa"] or 0.0),
        "poissons_ratio": float(info["poissons_ratio"] or 0.0),
        "max_visual_compression_m": float(max_compression),
        "point_count": len(base_points),
        "bounds": {
            "min": [float(min(point[index] for point in base_points)) for index in range(3)],
            "max": [float(max(point[index] for point in base_points)) for index in range(3)],
        },
        "local_span": [float(max(value[index] for value in local) - min(value[index] for value in local)) for index in range(3)],
    }


def _probe_base_points(info: dict[str, Any], tray_xy: tuple[float, float], target_size: float, Gf) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    min_pt, max_pt = info["bounds"]
    centre = info["centre"]
    span = max(float(size) for size in info["size"])
    scale = target_size / max(span, 0.001)
    local: list[tuple[float, float, float]] = []
    for point in info["points"]:
        local.append(
            (
                (float(point[0]) - float(centre[0])) * scale,
                (float(point[1]) - float(centre[1])) * scale,
                (float(point[2]) - float(centre[2])) * scale,
            )
        )
    local_min_z = min(point[2] for point in local)
    base_z = 0.012 - local_min_z
    points = [(point[0] + tray_xy[0], point[1] + tray_xy[1], point[2] + base_z) for point in local]
    return points, local


def _deformed_probe_point(
    point: tuple[float, float, float],
    press_xy: tuple[float, float],
    min_z: float,
    height: float,
    radius: float,
    compression: float,
) -> tuple[float, float, float]:
    if compression <= 0.0:
        return point
    dx = point[0] - press_xy[0]
    dy = point[1] - press_xy[1]
    radial = math.sqrt(dx * dx + dy * dy)
    gaussian = math.exp(-(radial * radial) / max(2.0 * radius * radius, 1.0e-6))
    top_weight = _smoothstep(0.38, 0.98, (point[2] - min_z) / height)
    press = gaussian * top_weight
    if press <= 1.0e-6:
        return point
    outward = (dx / radial, dy / radial) if radial > 1.0e-6 else (0.0, 0.0)
    lateral = compression * 0.34 * press * (1.0 - _smoothstep(0.0, radius, radial))
    return (
        point[0] + outward[0] * lateral,
        point[1] + outward[1] * lateral,
        point[2] - compression * press,
    )


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge1 <= edge0:
        return 0.0
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _apply_probe_physics(prim, info: dict[str, Any], UsdPhysics) -> None:
    UsdPhysics.CollisionAPI.Apply(prim)
    try:
        UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set("none")
    except Exception:
        pass
    mass = UsdPhysics.MassAPI.Apply(prim)
    mass.CreateDensityAttr().Set(float(info["density_kg_m3"]))
    try:
        from pxr import PhysxSchema

        PhysxSchema.PhysxCollisionAPI.Apply(prim)
        PhysxSchema.PhysxDeformableBodyAPI.Apply(prim)
    except Exception:
        prim.CreateAttribute("organiq:physxDeformableBodyAuthored", _sdf_float_type()).Set(1.0)


def _author_probe_manipulator(stage, soft_result, materials, UsdGeom, UsdPhysics, UsdShade, Sdf, Gf) -> dict[str, Any]:
    bounds = soft_result["bounds"]
    centre_x = (bounds["min"][0] + bounds["max"][0]) * 0.5
    centre_y = (bounds["min"][1] + bounds["max"][1]) * 0.5
    top_z = bounds["max"][2]
    root = UsdGeom.Xform.Define(stage, "/World/PhysicsProbe/Manipulator")
    translate = UsdGeom.Xformable(root).AddTranslateOp(opSuffix="pressPath")
    for frame, z in (
        (1, top_z + 0.24),
        (54, top_z + 0.24),
        (84, top_z + 0.085),
        (118, top_z + 0.012),
        (150, top_z + 0.030),
        (190, top_z + 0.14),
        (240, top_z + 0.24),
    ):
        translate.Set(Gf.Vec3d(centre_x, centre_y - 0.018, z), frame)

    shaft = UsdGeom.Cylinder.Define(stage, "/World/PhysicsProbe/Manipulator/shaft")
    shaft.CreateRadiusAttr(0.018)
    shaft.CreateHeightAttr(0.34)
    UsdGeom.XformCommonAPI(shaft).SetTranslate(Gf.Vec3d(0.0, 0.0, 0.17))
    UsdShade.MaterialBindingAPI.Apply(shaft.GetPrim()).Bind(materials["tool"], UsdShade.Tokens.strongerThanDescendants)

    pad = UsdGeom.Sphere.Define(stage, "/World/PhysicsProbe/Manipulator/press_pad")
    pad.CreateRadiusAttr(0.060)
    UsdGeom.XformCommonAPI(pad).SetScale(Gf.Vec3f(1.0, 1.0, 0.38))
    UsdShade.MaterialBindingAPI.Apply(pad.GetPrim()).Bind(materials["tool"], UsdShade.Tokens.strongerThanDescendants)
    UsdPhysics.CollisionAPI.Apply(pad.GetPrim())
    rigid = UsdPhysics.RigidBodyAPI.Apply(pad.GetPrim())
    try:
        rigid.CreateKinematicEnabledAttr().Set(True)
    except Exception:
        pad.GetPrim().CreateAttribute("physics:kinematicEnabled", Sdf.ValueTypeNames.Bool).Set(True)

    disk = UsdGeom.Cylinder.Define(stage, "/World/PhysicsProbe/Pressure/contact_patch")
    disk.CreateRadiusAttr(0.074)
    disk.CreateHeightAttr(0.003)
    UsdGeom.XformCommonAPI(disk).SetTranslate(Gf.Vec3d(centre_x, centre_y - 0.018, top_z + 0.002))
    UsdShade.MaterialBindingAPI.Apply(disk.GetPrim()).Bind(materials["pressure"], UsdShade.Tokens.strongerThanDescendants)
    visibility = UsdGeom.Imageable(disk.GetPrim()).CreateVisibilityAttr()
    visibility.Set(UsdGeom.Tokens.invisible, 1)
    visibility.Set(UsdGeom.Tokens.inherited, 84)
    visibility.Set(UsdGeom.Tokens.inherited, 170)
    visibility.Set(UsdGeom.Tokens.invisible, 210)

    curve = UsdGeom.BasisCurves.Define(stage, "/World/PhysicsProbe/Pressure/force_trace")
    curve.CreateTypeAttr(UsdGeom.Tokens.linear)
    curve.CreateCurveVertexCountsAttr([2])
    curve.CreatePointsAttr([Gf.Vec3f(centre_x, centre_y - 0.018, top_z + 0.20), Gf.Vec3f(centre_x, centre_y - 0.018, top_z + 0.02)])
    curve.CreateWidthsAttr([0.009])
    curve.SetWidthsInterpolation(UsdGeom.Tokens.constant)
    UsdShade.MaterialBindingAPI.Apply(curve.GetPrim()).Bind(materials["spring"], UsdShade.Tokens.strongerThanDescendants)
    return {
        "tool_path": "/World/PhysicsProbe/Manipulator",
        "press_pad_path": "/World/PhysicsProbe/Manipulator/press_pad",
        "contact_patch_path": "/World/PhysicsProbe/Pressure/contact_patch",
        "force_trace_path": "/World/PhysicsProbe/Pressure/force_trace",
    }


def _author_probe_camera(stage, Gf, UsdGeom) -> str:
    camera_path = "/World/PhysicsProbe/Cameras/main_camera"
    camera = UsdGeom.Camera.Define(stage, camera_path)
    camera.CreateFocalLengthAttr(34.0)
    camera.CreateFocusDistanceAttr(1.35)
    camera.CreateFStopAttr(7.1)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.001, 12.0))
    view = Gf.Matrix4d(1.0)
    view.SetLookAt(Gf.Vec3d(0.82, -1.18, 0.68), Gf.Vec3d(0.02, 0.02, 0.16), Gf.Vec3d(0.0, 0.0, 1.0))
    UsdGeom.Xformable(camera).MakeMatrixXform().Set(view.GetInverse())
    return camera_path


def _sdf_float_type():
    from pxr import Sdf

    return Sdf.ValueTypeNames.Float


def _set_layer_custom_data(stage, values: dict[str, Any]) -> None:
    root_layer = stage.GetRootLayer()
    data = dict(root_layer.customLayerData or {})
    data.update(values)
    root_layer.customLayerData = data


def _bounds(points: list[tuple[float, float, float]]):
    if not points:
        return ((-0.05, -0.05, -0.05), (0.05, 0.05, 0.05))
    return (
        (min(point[0] for point in points), min(point[1] for point in points), min(point[2] for point in points)),
        (max(point[0] for point in points), max(point[1] for point in points), max(point[2] for point in points)),
    )


def _attr_value(prim, name: str):
    attr = prim.GetAttribute(name)
    return attr.Get() if attr else None


def _float_attr(prim, name: str, fallback):
    value = _optional_float_attr(prim, name)
    if value is not None:
        return value
    return fallback


def _optional_float_attr(prim, name: str) -> float | None:
    attr = prim.GetAttribute(name)
    if not attr:
        return None
    value = attr.Get()
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _vec3_tuple(value) -> tuple[float, float, float]:
    return (float(value[0]), float(value[1]), float(value[2]))


def _softness(youngs_modulus_pa: float | None, simulation_mode: str) -> float:
    if simulation_mode == "rigid":
        return 0.02
    if youngs_modulus_pa is None or youngs_modulus_pa <= 0:
        return 0.55
    log_e = math.log10(max(youngs_modulus_pa, 1.0))
    return max(0.02, min(1.0, (10.5 - log_e) / 7.8))


def _hu_colour(value: float | None) -> tuple[float, float, float]:
    if value is None:
        return (0.55, 0.58, 0.62)
    t = _hu_normalised(value)
    if t < 0.5:
        u = t / 0.5
        return (0.10 + u * 0.45, 0.42 + u * 0.18, 0.95 - u * 0.48)
    u = (t - 0.5) / 0.5
    return (0.55 + u * 0.40, 0.60 + u * 0.18, 0.47 - u * 0.18)


def _hu_normalised(value: float | None) -> float:
    if value is None:
        return 0.5
    return max(0.0, min(1.0, (float(value) + 1000.0) / 2200.0))


def _elastic_colour(value: float | None, simulation_mode: str) -> tuple[float, float, float]:
    if simulation_mode == "rigid":
        return (0.94, 0.76, 0.38)
    t = _elastic_normalised(value)
    return (1.0, 0.26 + t * 0.54, 0.22 + t * 0.26)


def _elastic_normalised(value: float | None) -> float:
    if value is None or value <= 0:
        return 0.25
    return max(0.0, min(1.0, (math.log10(value) - 3.0) / 8.0))


def _youngs_text(value: float | None) -> str:
    if value is None or value <= 0:
        return "elasticity n/a"
    if value >= 1.0e9:
        return f"E {value / 1.0e9:.2f} GPa"
    if value >= 1.0e6:
        return f"E {value / 1.0e6:.2f} MPa"
    return f"E {value / 1.0e3:.1f} kPa"


def _display_label(value: str) -> str:
    words = re.sub(r"[_-]+", " ", value).strip()
    return words[:1].upper() + words[1:]


def _relative_asset(path: Path, anchor: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), anchor.resolve())).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _normalised(value) -> tuple[float, float, float]:
    length = _length(value)
    if length <= 1.0e-9:
        return (0.0, 0.0, 1.0)
    return (value[0] / length, value[1] / length, value[2] / length)


def _length(value) -> float:
    return math.sqrt(sum(float(component) * float(component) for component in value))


def _cross(first, second) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip().lower()).strip("_")
    return name or "item"


def _unique(name: str, used: dict[str, int]) -> str:
    count = used.get(name, 0)
    used[name] = count + 1
    if count == 0:
        return name
    return f"{name}_{count + 1}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _ensure_pxr_available() -> bool:
    _configure_isaac_usd_paths()
    try:
        from pxr import Usd  # noqa: F401
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
    plugin_paths = _find_isaac_usd_plugin_paths()
    if plugin_paths:
        existing = os.environ.get("PXR_PLUGINPATH_NAME")
        values = [str(path) for path in plugin_paths]
        if existing:
            values.append(existing)
        os.environ["PXR_PLUGINPATH_NAME"] = os.pathsep.join(values)


def _run_under_isaac_python(args: argparse.Namespace) -> int:
    if os.environ.get("ORGANIQ_PHYSICS_SHOWCASE_REEXEC") == "1":
        print("organiq_physics_showcase=skipped_no_pxr")
        return 1
    isaac_root = _find_isaac_root()
    if isaac_root is None:
        print("organiq_physics_showcase=skipped_no_isaac_root")
        return 1
    python_exe = isaac_root / "kit" / "python" / "python.exe"
    if not python_exe.exists():
        print("organiq_physics_showcase=skipped_no_isaac_python")
        return 1
    env = os.environ.copy()
    env["ORGANIQ_PHYSICS_SHOWCASE_REEXEC"] = "1"
    paths = [str(EXT_ROOT), *[str(path) for path in _find_isaac_extensions()]]
    env["PYTHONPATH"] = os.pathsep.join(paths + [env.get("PYTHONPATH", "")])
    for extension_path in _find_isaac_extensions():
        bin_path = extension_path / "bin"
        if bin_path.exists():
            env["PATH"] = f"{bin_path}{os.pathsep}{env.get('PATH', '')}"
    plugin_paths = _find_isaac_usd_plugin_paths()
    if plugin_paths:
        values = [str(path) for path in plugin_paths]
        existing = env.get("PXR_PLUGINPATH_NAME")
        if existing:
            values.append(existing)
        env["PXR_PLUGINPATH_NAME"] = os.pathsep.join(values)
    command = [
        str(python_exe),
        str(Path(__file__).resolve()),
        "--source-usd",
        str(args.source_usd),
        "--output",
        str(args.output),
        "--report",
        str(args.report),
        "--shots",
        str(args.shots),
        "--card-dir",
        str(args.card_dir),
        "--max-panels",
        str(args.max_panels),
        "--probe-output",
        str(args.probe_output),
        "--probe-report",
        str(args.probe_report),
    ]
    if args.skip_probe:
        command.append("--skip-probe")
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


def _find_isaac_usd_plugin_paths() -> list[Path]:
    paths: list[Path] = []
    for extension_path in _find_isaac_extensions():
        for plug_info in extension_path.rglob("plugInfo.json"):
            parent = plug_info.parent
            if parent not in paths:
                paths.append(parent)
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
    package_root_value = os.environ.get("OMNI_USER_PACKAGE_ROOT")
    package_roots = [Path(package_root_value)] if package_root_value else []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        package_roots.append(Path(local_app_data) / "ov" / "pkg")
    for package_root in package_roots:
        if package_root.exists():
            roots.extend(sorted(package_root.glob("isaac-sim*"), reverse=True))
    roots.extend(Path(f"{drive}:/isaacsim") for drive in string.ascii_uppercase)

    for root in roots:
        if (root / "kit" / "python" / "python.exe").exists():
            ISAAC_ROOT = root
            return root
    return None


if __name__ == "__main__":
    raise SystemExit(main())
