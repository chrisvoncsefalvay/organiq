from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DicomSeries:
    series_uid: str
    patient_id: str
    study_description: str
    series_description: str
    modality: str
    file_count: int
    rows: int | None = None
    columns: int | None = None
    spacing_mm: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class Volume:
    data: Any
    spacing_mm: tuple[float, float, float]
    origin_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    direction: tuple[float, ...] | None = None
    series: DicomSeries | None = None


@dataclass(frozen=True)
class SegmentLabel:
    value: int
    name: str
    voxel_count: int
    selected: bool = True
    mean_hounsfield: float | None = None


@dataclass(frozen=True)
class SegmentationResult:
    label_volume: Any
    spacing_mm: tuple[float, float, float]
    labels: tuple[SegmentLabel, ...]
    source: str
    output_path: Path | None = None
    auxiliary_label_volumes: dict[int, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DistanceFieldMetadata:
    shape: tuple[int, int, int]
    spacing_mm: tuple[float, float, float]
    narrow_band_mm: float
    min_distance_mm: float
    max_distance_mm: float


@dataclass(frozen=True)
class MeshArtifact:
    label_value: int
    label_name: str
    vertices_m: tuple[tuple[float, float, float], ...]
    faces: tuple[tuple[int, int, int], ...]
    source_voxels: int
    mean_hounsfield: float | None = None
    meshing_method: str = "sdf"
    distance_field: DistanceFieldMetadata | None = None
    vertex_normals: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True)
class TissueDefaults:
    name: str
    simulation_mode: str
    density_kg_m3: float
    youngs_modulus_pa: float | None
    poissons_ratio: float | None
    static_friction: float
    dynamic_friction: float
    damping_scale: float
    colour: tuple[float, float, float]
    opacity: float
    roughness: float
    metallic: float = 0.0
    mdl_name: str = "OmniPBR.mdl"
    mdl_subidentifier: str = "OmniPBR"
    semantic_class: str = "tissue"
    mesh_smoothing_mm: float = 0.8
    mesh_smoothing_iterations: int = 4
    mesh_relax_lambda: float = 0.45
    mesh_relax_mu: float = -0.47
    mesh_closing_iterations: int = 1
    mesh_keep_largest_component: bool = False
    deformable_resolution: int = 14
    texture_scale: tuple[float, float] = (3.0, 3.0)
    surface_detail: str = "organ"
    normal_strength: float = 0.28
    detail_normal_strength: float = 0.1


@dataclass
class WorkflowState:
    dicom_folder: Path | None = None
    series: DicomSeries | None = None
    volume: Volume | None = None
    segmentation: SegmentationResult | None = None
    meshes: list[MeshArtifact] = field(default_factory=list)
    usd_path: Path | None = None
    status: str = "Idle"
