from __future__ import annotations

import hashlib
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from .models import TissueDefaults


@dataclass(frozen=True)
class TissueTextureSet:
    diffuse: Path
    normal: Path


def create_visual_material(
    stage,
    material_path: str,
    defaults: TissueDefaults,
    texture_dir: str | Path | None = None,
    material_key: str | None = None,
):
    from pxr import Gf, Sdf, UsdShade

    material = UsdShade.Material.Define(stage, material_path)
    _author_preview_surface(stage, material, material_path, defaults)
    shader = UsdShade.Shader.Define(stage, f"{material_path}/shader")
    shader_out = shader.CreateOutput("out", Sdf.ValueTypeNames.Token)

    shader.CreateIdAttr(defaults.mdl_subidentifier)
    shader.GetImplementationSourceAttr().Set(UsdShade.Tokens.sourceAsset)
    shader.SetSourceAsset(Sdf.AssetPath(defaults.mdl_name), "mdl")
    shader.SetSourceAssetSubIdentifier(defaults.mdl_subidentifier, "mdl")

    shader.CreateInput("diffuse_color_constant", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*defaults.colour))
    shader.CreateInput("reflection_roughness_constant", Sdf.ValueTypeNames.Float).Set(float(defaults.roughness))
    shader.CreateInput("metallic_constant", Sdf.ValueTypeNames.Float).Set(float(defaults.metallic))
    shader.CreateInput("project_uvw", Sdf.ValueTypeNames.Bool).Set(True)
    shader.CreateInput("texture_scale", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(*defaults.texture_scale))
    shader.CreateInput("texture_translate", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(0.0, 0.0))

    texture_set = ensure_tissue_textures(defaults, texture_dir, material_key)
    if texture_set is not None:
        shader.CreateInput("diffuse_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(_asset(texture_set.diffuse)))
        shader.CreateInput("normalmap_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(_asset(texture_set.normal)))
        shader.CreateInput("bump_factor", Sdf.ValueTypeNames.Float).Set(float(defaults.normal_strength))
        shader.CreateInput("detail_normalmap_texture", Sdf.ValueTypeNames.Asset).Set(
            Sdf.AssetPath(_asset(texture_set.normal))
        )
        shader.CreateInput("detail_bump_factor", Sdf.ValueTypeNames.Float).Set(float(defaults.detail_normal_strength))

    if defaults.opacity < 0.999:
        shader.CreateInput("enable_opacity", Sdf.ValueTypeNames.Bool).Set(True)
        shader.CreateInput("opacity_constant", Sdf.ValueTypeNames.Float).Set(float(defaults.opacity))
        shader.CreateInput("opacity_threshold", Sdf.ValueTypeNames.Float).Set(0.0)

    material.CreateSurfaceOutput("mdl").ConnectToSource(shader_out)
    material.GetPrim().CreateAttribute("organiq:semanticClass", Sdf.ValueTypeNames.String).Set(defaults.semantic_class)
    material.GetPrim().CreateAttribute("organiq:opacity", Sdf.ValueTypeNames.Float).Set(float(defaults.opacity))
    if texture_set is not None:
        material.GetPrim().CreateAttribute("organiq:diffuseTexture", Sdf.ValueTypeNames.Asset).Set(
            Sdf.AssetPath(_asset(texture_set.diffuse))
        )
        material.GetPrim().CreateAttribute("organiq:normalTexture", Sdf.ValueTypeNames.Asset).Set(
            Sdf.AssetPath(_asset(texture_set.normal))
        )
    return material


def create_preview_material(stage, material_path: str, defaults: TissueDefaults):
    from pxr import UsdShade

    material = UsdShade.Material.Define(stage, material_path)
    _author_preview_surface(stage, material, material_path, defaults)
    return material


def _author_preview_surface(stage, material, material_path: str, defaults: TissueDefaults) -> None:
    from pxr import Gf, Sdf, UsdShade

    shader = UsdShade.Shader.Define(stage, f"{material_path}/preview")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*defaults.colour))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(defaults.roughness))
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(defaults.metallic))
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(defaults.opacity))
    material.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput("surface", Sdf.ValueTypeNames.Token))


def ensure_tissue_textures(
    defaults: TissueDefaults,
    texture_dir: str | Path | None,
    material_key: str | None = None,
) -> TissueTextureSet | None:
    if texture_dir is None:
        return None
    try:
        import numpy as np
    except Exception:
        return None

    output_dir = Path(texture_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_key = _safe_texture_name(material_key or defaults.name)
    diffuse = output_dir / f"{safe_key}_{defaults.surface_detail}_albedo.png"
    normal = output_dir / f"{safe_key}_{defaults.surface_detail}_normal.png"
    if diffuse.exists() and normal.exists():
        return TissueTextureSet(diffuse=diffuse, normal=normal)

    albedo, height = _build_texture_arrays(np, defaults, safe_key)
    normal_rgb = _normal_map_from_height(np, height, defaults.normal_strength)
    _write_png(diffuse, (np.clip(albedo, 0.0, 1.0) * 255.0).astype(np.uint8))
    _write_png(normal, (np.clip(normal_rgb, 0.0, 1.0) * 255.0).astype(np.uint8))
    return TissueTextureSet(diffuse=diffuse, normal=normal)


def _build_texture_arrays(np, defaults: TissueDefaults, key: str):
    size = 256
    seed = int(hashlib.sha1(f"{key}:{defaults.surface_detail}".encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) / float(size)
    noise = _smooth_noise(np, rng, size, 5)
    fine = _smooth_noise(np, rng, size, 2)
    base = np.array(defaults.colour, dtype=np.float32).reshape(1, 1, 3)
    kind = defaults.surface_detail

    if kind == "bone":
        pores = np.maximum(0.0, fine - 0.68)
        albedo = base * (0.86 + 0.24 * noise[..., None]) - pores[..., None] * 0.26
        height = 0.65 * noise - 0.35 * pores
    elif kind == "lung":
        cells = 0.5 + 0.5 * np.sin((xx * 34.0 + noise * 4.0) * np.pi)
        albedo = base * (0.82 + 0.24 * noise[..., None]) + cells[..., None] * np.array([0.08, 0.02, 0.03])
        height = 0.5 * noise + 0.2 * cells
    elif kind == "muscle":
        fibres = 0.5 + 0.5 * np.sin((xx * 32.0 + yy * 4.0 + fine * 2.0) * np.pi)
        albedo = base * (0.72 + 0.30 * noise[..., None]) + fibres[..., None] * np.array([0.12, 0.02, 0.015])
        height = 0.65 * fibres + 0.25 * noise
    elif kind == "skin":
        pores = np.maximum(0.0, fine - 0.58)
        freckles = np.maximum(0.0, _smooth_noise(np, rng, size, 1) - 0.84)
        albedo = base * (0.86 + 0.20 * noise[..., None]) - pores[..., None] * 0.08
        albedo += freckles[..., None] * np.array([0.06, 0.025, 0.01])
        height = 0.35 * noise - 0.2 * pores
    elif kind == "blood":
        flow = 0.5 + 0.5 * np.sin((xx * 5.5 + yy * 2.0 + noise) * np.pi)
        albedo = base * (0.78 + 0.20 * noise[..., None]) + flow[..., None] * np.array([0.08, 0.0, 0.0])
        height = 0.28 * noise + 0.16 * flow
    elif kind == "brain":
        folds = 0.5 + 0.5 * np.sin((xx * 13.0 + np.sin(yy * 7.0) + noise * 2.0) * np.pi)
        albedo = base * (0.80 + 0.24 * noise[..., None]) + folds[..., None] * np.array([0.04, 0.025, 0.02])
        height = 0.5 * folds + 0.2 * noise
    elif kind == "fat":
        lobules = 0.5 + 0.5 * np.sin((xx * 11.0 + yy * 8.0 + noise * 2.5) * np.pi)
        albedo = base * (0.84 + 0.24 * noise[..., None]) + lobules[..., None] * np.array([0.04, 0.035, 0.0])
        height = 0.45 * lobules + 0.2 * noise
    elif kind == "heart":
        grain = 0.5 + 0.5 * np.sin((xx * 18.0 + yy * 6.0 + fine * 2.2) * np.pi)
        albedo = base * (0.78 + 0.25 * noise[..., None]) + grain[..., None] * np.array([0.11, 0.015, 0.015])
        height = 0.55 * grain + 0.22 * noise
    else:
        vessels = np.exp(-np.abs(np.sin((xx * 4.5 + yy * 2.6 + noise * 0.8) * np.pi)) * 9.0)
        albedo = base * (0.78 + 0.28 * noise[..., None]) + vessels[..., None] * np.array([0.10, 0.0, 0.0])
        height = 0.48 * noise + 0.24 * vessels

    return np.clip(albedo, 0.0, 1.0), np.clip(height, 0.0, 1.0)


def _smooth_noise(np, rng, size: int, passes: int):
    noise = rng.random((size, size), dtype=np.float32)
    for _ in range(passes):
        noise = (
            noise
            + np.roll(noise, 1, axis=0)
            + np.roll(noise, -1, axis=0)
            + np.roll(noise, 1, axis=1)
            + np.roll(noise, -1, axis=1)
        ) / 5.0
    low = float(noise.min())
    high = float(noise.max())
    if high <= low:
        return noise * 0.0
    return (noise - low) / (high - low)


def _normal_map_from_height(np, height, strength: float):
    dy, dx = np.gradient(height.astype(np.float32))
    nx = -dx * float(strength) * 4.0
    ny = -dy * float(strength) * 4.0
    nz = np.ones_like(height)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    normal = np.stack((nx / length, ny / length, nz / length), axis=-1)
    return normal * 0.5 + 0.5


def _write_png(path: Path, image_rgb) -> None:
    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        raise ValueError("PNG writer expects an RGB image")
    height, width, _ = image_rgb.shape
    raw = b"".join(b"\x00" + image_rgb[row].tobytes() for row in range(height))
    data = b"\x89PNG\r\n\x1a\n"
    data += _png_chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
    data += _png_chunk(b"IDAT", zlib.compress(raw, 9))
    data += _png_chunk(b"IEND", b"")
    path.write_bytes(data)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack("!I", len(payload)) + kind + payload + struct.pack("!I", checksum)


def _safe_texture_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip().lower()).strip("_")
    return name or "tissue"


def _asset(path: Path) -> str:
    return path.resolve().as_posix()
