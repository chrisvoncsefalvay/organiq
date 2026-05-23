from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

from .models import DicomSeries, Volume


ProgressCallback = Callable[[int, int, str], None]


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("numpy is required for CT volume loading") from exc
    return np


def _require_pydicom():
    try:
        import pydicom
    except ImportError as exc:
        raise RuntimeError("pydicom is required for DICOM loading") from exc
    return pydicom


def iter_candidate_files(folder: str | Path) -> Iterable[Path]:
    root = Path(folder)
    if not root.exists():
        raise FileNotFoundError(root)
    for path in root.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            yield path


def scan_dicom_folder(folder: str | Path, progress: ProgressCallback | None = None) -> tuple[DicomSeries, ...]:
    pydicom = _require_pydicom()
    grouped: dict[str, list[tuple[Path, object]]] = defaultdict(list)
    candidates = tuple(iter_candidate_files(folder))
    total = max(len(candidates), 1)
    _report_progress(progress, 0, total, "scanning DICOM files")

    for index, path in enumerate(candidates, start=1):
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
        except Exception:
            _report_progress(progress, index, total, f"scanned {index} of {total} files")
            continue
        uid = str(getattr(ds, "SeriesInstanceUID", ""))
        modality = str(getattr(ds, "Modality", ""))
        if not uid or modality.upper() != "CT":
            _report_progress(progress, index, total, f"scanned {index} of {total} files")
            continue
        grouped[uid].append((path, ds))
        _report_progress(progress, index, total, f"scanned {index} of {total} files")

    series: list[DicomSeries] = []
    for uid, items in sorted(grouped.items(), key=lambda entry: entry[0]):
        first = items[0][1]
        spacing = _spacing_from_header(first)
        series.append(
            DicomSeries(
                series_uid=uid,
                patient_id=str(getattr(first, "PatientID", "")),
                study_description=str(getattr(first, "StudyDescription", "")),
                series_description=str(getattr(first, "SeriesDescription", "")),
                modality=str(getattr(first, "Modality", "")),
                file_count=len(items),
                rows=_safe_int(getattr(first, "Rows", None)),
                columns=_safe_int(getattr(first, "Columns", None)),
                spacing_mm=spacing,
            )
        )
    _report_progress(progress, total, total, f"found {len(series)} CT series")
    return tuple(series)


def load_ct_series(
    folder: str | Path,
    series_uid: str | None = None,
    progress: ProgressCallback | None = None,
) -> Volume:
    np = _require_numpy()
    pydicom = _require_pydicom()
    slices: list[tuple[float, Path, object]] = []
    candidates = tuple(iter_candidate_files(folder))
    scan_total = max(len(candidates), 1)
    _report_progress(progress, 0, scan_total, "reading CT headers")

    for index, path in enumerate(candidates, start=1):
        try:
            ds = pydicom.dcmread(str(path), force=True)
        except Exception:
            _report_progress(progress, index, scan_total, f"read {index} of {scan_total} files")
            continue
        if str(getattr(ds, "Modality", "")).upper() != "CT":
            _report_progress(progress, index, scan_total, f"read {index} of {scan_total} files")
            continue
        uid = str(getattr(ds, "SeriesInstanceUID", ""))
        if series_uid and uid != series_uid:
            _report_progress(progress, index, scan_total, f"read {index} of {scan_total} files")
            continue
        sort_key = _slice_sort_key(ds)
        slices.append((sort_key, path, ds))
        _report_progress(progress, index, scan_total, f"read {index} of {scan_total} files")

    if not slices:
        raise RuntimeError("No CT DICOM slices found")

    slices.sort(key=lambda item: item[0])
    arrays = []
    slice_positions = []
    total = scan_total + max(len(slices), 1)
    for index, (slice_position, _, ds) in enumerate(slices, start=1):
        slope = float(getattr(ds, "RescaleSlope", 1.0))
        intercept = float(getattr(ds, "RescaleIntercept", 0.0))
        arrays.append(ds.pixel_array.astype(np.float32) * slope + intercept)
        slice_positions.append(slice_position)
        _report_progress(progress, scan_total + index, total, f"loaded {index} of {len(slices)} slices")

    data = np.stack(arrays, axis=0)
    first = slices[0][2]
    spacing = _spacing_from_header(first, slice_positions)
    direction = _direction_from_header(first)
    series = DicomSeries(
        series_uid=str(getattr(first, "SeriesInstanceUID", "")),
        patient_id=str(getattr(first, "PatientID", "")),
        study_description=str(getattr(first, "StudyDescription", "")),
        series_description=str(getattr(first, "SeriesDescription", "")),
        modality=str(getattr(first, "Modality", "")),
        file_count=len(slices),
        rows=int(data.shape[1]),
        columns=int(data.shape[2]),
        spacing_mm=spacing,
    )
    origin = tuple(float(x) for x in getattr(first, "ImagePositionPatient", [0.0, 0.0, 0.0])[:3])
    _report_progress(progress, total, total, f"loaded {len(slices)} CT slices")
    return Volume(data=data, spacing_mm=spacing, origin_mm=origin, direction=direction, series=series)


def write_nifti(volume: Volume, output_path: str | Path) -> Path:
    try:
        import nibabel as nib
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("nibabel and numpy are required for NIfTI export") from exc

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    affine = _nifti_affine(volume, np)
    image = nib.Nifti1Image(np.transpose(volume.data, (2, 1, 0)), affine)
    nib.save(image, str(output))
    return output


def _spacing_from_header(ds: object, z_positions: list[float] | None = None) -> tuple[float, float, float]:
    pixel_spacing = getattr(ds, "PixelSpacing", [1.0, 1.0])
    row_spacing = float(pixel_spacing[0])
    col_spacing = float(pixel_spacing[1])
    if z_positions and len(z_positions) > 1:
        diffs = [abs(b - a) for a, b in zip(z_positions, z_positions[1:]) if abs(b - a) > 0]
        slice_spacing = sum(diffs) / len(diffs) if diffs else float(getattr(ds, "SliceThickness", 1.0))
    else:
        slice_spacing = float(getattr(ds, "SpacingBetweenSlices", getattr(ds, "SliceThickness", 1.0)))
    return (slice_spacing, row_spacing, col_spacing)


def _slice_sort_key(ds: object) -> float:
    ipp = getattr(ds, "ImagePositionPatient", None)
    if ipp and len(ipp) >= 3:
        normal = _slice_normal_from_header(ds)
        return sum(float(ipp[index]) * normal[index] for index in range(3))
    return float(getattr(ds, "InstanceNumber", 0))


def _direction_from_header(ds: object) -> tuple[float, ...]:
    row_direction, column_direction = _in_plane_directions_from_header(ds)
    return tuple(row_direction + column_direction)


def _slice_normal_from_header(ds: object) -> tuple[float, float, float]:
    row_direction, column_direction = _in_plane_directions_from_header(ds)
    normal = (
        row_direction[1] * column_direction[2] - row_direction[2] * column_direction[1],
        row_direction[2] * column_direction[0] - row_direction[0] * column_direction[2],
        row_direction[0] * column_direction[1] - row_direction[1] * column_direction[0],
    )
    return _normalise_vector(normal, (0.0, 0.0, 1.0))


def _in_plane_directions_from_header(ds: object) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    orientation = getattr(ds, "ImageOrientationPatient", None)
    if orientation and len(orientation) >= 6:
        row = _normalise_vector(tuple(float(value) for value in orientation[:3]), (1.0, 0.0, 0.0))
        column = _normalise_vector(tuple(float(value) for value in orientation[3:6]), (0.0, 1.0, 0.0))
        return row, column
    return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)


def _normalise_vector(vector: tuple[float, float, float], fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    length = sum(value * value for value in vector) ** 0.5
    if length <= 1.0e-12:
        return fallback
    return tuple(float(value) / length for value in vector)


def _nifti_affine(volume: Volume, np):
    slice_spacing, row_spacing, col_spacing = volume.spacing_mm
    direction = volume.direction or (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    row_direction = tuple(float(value) for value in direction[:3])
    column_direction = tuple(float(value) for value in direction[3:6])
    normal = (
        row_direction[1] * column_direction[2] - row_direction[2] * column_direction[1],
        row_direction[2] * column_direction[0] - row_direction[0] * column_direction[2],
        row_direction[0] * column_direction[1] - row_direction[1] * column_direction[0],
    )
    normal = _normalise_vector(normal, (0.0, 0.0, 1.0))
    affine = np.eye(4, dtype=np.float64)
    affine[:3, 0] = np.asarray(row_direction, dtype=np.float64) * float(col_spacing)
    affine[:3, 1] = np.asarray(column_direction, dtype=np.float64) * float(row_spacing)
    affine[:3, 2] = np.asarray(normal, dtype=np.float64) * float(slice_spacing)
    affine[:3, 3] = np.asarray(volume.origin_mm, dtype=np.float64)
    return affine


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _report_progress(progress: ProgressCallback | None, completed: int, total: int, status: str) -> None:
    if progress is None:
        return
    progress(int(completed), max(int(total), 1), status)
