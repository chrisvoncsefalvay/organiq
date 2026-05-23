from __future__ import annotations

from pathlib import Path
from typing import Callable

from .dicom import load_ct_series, scan_dicom_folder
from .meshing import mesh_selected_labels
from .models import DicomSeries, MeshArtifact, SegmentationResult, Volume, WorkflowState
from .paths import DEFAULT_OUTPUT_ROOT, ensure_work_roots
from .segmentation import run_monai_bundle
from .usd_writer import UsdExportResult, export_meshes_to_usd, instantiate_usd_on_stage


ProgressCallback = Callable[[int, int, str], None]


class OrganiqWorkflow:
    def __init__(self) -> None:
        ensure_work_roots()
        self.state = WorkflowState()
        self.available_series: tuple[DicomSeries, ...] = ()
        self.selected_label_values: set[int] = set()

    def scan(self, dicom_folder: str | Path, progress: ProgressCallback | None = None) -> tuple[DicomSeries, ...]:
        folder = Path(dicom_folder)
        self._set_status(f"Scanning {folder}")
        self.available_series = scan_dicom_folder(folder, progress=progress)
        self.state.dicom_folder = folder
        self._set_status(f"Found {len(self.available_series)} CT series")
        return self.available_series

    def load_series(self, series_uid: str | None = None, progress: ProgressCallback | None = None) -> Volume:
        if self.state.dicom_folder is None:
            raise RuntimeError("Load a DICOM folder before loading a series")
        self._set_status("Loading CT series")
        self.state.volume = load_ct_series(self.state.dicom_folder, series_uid=series_uid, progress=progress)
        self.state.series = self.state.volume.series
        if self.state.series is not None:
            self._set_status(f"Loaded {self.state.series.file_count} CT slices")
        return self.state.volume

    def segment_monai(
        self,
        bundle_name: str,
        bundle_dir: str | Path,
        highres: bool = False,
        extra_args: tuple[str, ...] = (),
        progress: ProgressCallback | None = None,
    ) -> SegmentationResult:
        volume = self._require_volume()
        self._set_status(f"Running MONAI bundle {bundle_name}")
        segmentation, run = run_monai_bundle(
            volume,
            bundle_name=bundle_name,
            bundle_dir=bundle_dir,
            highres=highres,
            extra_args=extra_args,
            progress=progress,
        )
        if segmentation is None:
            detail = run.stderr.strip() or run.stdout.strip() or f"return code {run.return_code}"
            raise RuntimeError(f"MONAI segmentation failed: {detail}")
        self.state.segmentation = segmentation
        self.selected_label_values = {label.value for label in segmentation.labels}
        self._set_status(f"MONAI segmented {len(segmentation.labels)} labels")
        return segmentation

    def set_label_selected(self, label_value: int, selected: bool) -> None:
        if selected:
            self.selected_label_values.add(label_value)
        else:
            self.selected_label_values.discard(label_value)

    def set_labels_selected(self, label_values, selected: bool) -> None:
        values = {int(value) for value in label_values}
        if selected:
            self.selected_label_values.update(values)
        else:
            self.selected_label_values.difference_update(values)

    def select_all_labels(self) -> None:
        segmentation = self._require_segmentation()
        self.selected_label_values = {label.value for label in segmentation.labels}

    def select_no_labels(self) -> None:
        self._require_segmentation()
        self.selected_label_values = set()

    def mesh_selected(
        self,
        smooth: bool = True,
        method: str = "sdf",
        progress: ProgressCallback | None = None,
    ) -> list[MeshArtifact]:
        segmentation = self._require_segmentation()
        self._set_status(f"Meshing selected labels with {method}")
        self.state.meshes = mesh_selected_labels(
            segmentation,
            self.selected_label_values,
            smooth=smooth,
            method=method,
            progress=progress,
        )
        self._set_status(f"Meshed {len(self.state.meshes)} labels with {method}")
        return self.state.meshes

    def export_usd(
        self,
        output_path: str | Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> UsdExportResult:
        if not self.state.meshes:
            raise RuntimeError("Mesh at least one label before USD export")
        output = Path(output_path) if output_path else DEFAULT_OUTPUT_ROOT / "organiq_scene.usd"
        self._set_status(f"Exporting USD {output}")
        result = export_meshes_to_usd(self.state.meshes, output, progress=progress)
        self.state.usd_path = result.path
        self._set_status(f"Exported USD with {len(result.prim_paths)} anatomy prims")
        return result

    def instantiate(self, stage, prim_path: str = "/World/Organiq_instance") -> str:
        if self.state.usd_path is None:
            raise RuntimeError("Export a USD before instancing it")
        self._set_status("Instancing Organiq USD")
        path = instantiate_usd_on_stage(stage, self.state.usd_path, prim_path=prim_path)
        self._set_status(f"Instanced Organiq at {path}")
        return path

    def _require_volume(self) -> Volume:
        if self.state.volume is None:
            raise RuntimeError("Load a CT series before segmentation")
        return self.state.volume

    def _require_segmentation(self) -> SegmentationResult:
        if self.state.segmentation is None:
            raise RuntimeError("Segment a volume before meshing")
        return self.state.segmentation

    def _set_status(self, status: str) -> None:
        self.state.status = status
