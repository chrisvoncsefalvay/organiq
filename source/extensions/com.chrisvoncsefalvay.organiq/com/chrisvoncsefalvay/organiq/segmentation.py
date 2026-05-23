from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .dependencies import isaac_python_candidates, resolve_python_executable
from .dicom import write_nifti
from .models import SegmentLabel, SegmentationResult, Volume
from .paths import DEFAULT_MODEL_ROOT, DEFAULT_OUTPUT_ROOT, ensure_work_roots


DEFAULT_MONAI_BUNDLE = "wholeBody_ct_segmentation"
SUPPORTED_MONAI_BUNDLES: tuple[str, ...] = (DEFAULT_MONAI_BUNDLE,)
MONAI_BUNDLE_PRESETS: tuple[str, ...] = SUPPORTED_MONAI_BUNDLES
SYNTHETIC_SKIN_LABEL_VALUE = 32760
SYNTHETIC_SKIN_LABEL_NAME = "skin_shell"
MONAI_OUTPUT_LIMIT_CHARS = 16000
DEFAULT_MONAI_TIMEOUT_SECONDS = 4 * 60 * 60
WHOLE_BODY_CT_LABELS: tuple[str, ...] = (
    "background",
    "spleen",
    "kidney_right",
    "kidney_left",
    "gallbladder",
    "liver",
    "stomach",
    "aorta",
    "inferior_vena_cava",
    "portal_vein_and_splenic_vein",
    "pancreas",
    "adrenal_gland_right",
    "adrenal_gland_left",
    "lung_upper_lobe_left",
    "lung_lower_lobe_left",
    "lung_upper_lobe_right",
    "lung_middle_lobe_right",
    "lung_lower_lobe_right",
    "vertebrae_L5",
    "vertebrae_L4",
    "vertebrae_L3",
    "vertebrae_L2",
    "vertebrae_L1",
    "vertebrae_T12",
    "vertebrae_T11",
    "vertebrae_T10",
    "vertebrae_T9",
    "vertebrae_T8",
    "vertebrae_T7",
    "vertebrae_T6",
    "vertebrae_T5",
    "vertebrae_T4",
    "vertebrae_T3",
    "vertebrae_T2",
    "vertebrae_T1",
    "vertebrae_C7",
    "vertebrae_C6",
    "vertebrae_C5",
    "vertebrae_C4",
    "vertebrae_C3",
    "vertebrae_C2",
    "vertebrae_C1",
    "esophagus",
    "trachea",
    "heart_myocardium",
    "heart_atrium_left",
    "heart_ventricle_left",
    "heart_atrium_right",
    "heart_ventricle_right",
    "pulmonary_artery",
    "brain",
    "iliac_artery_left",
    "iliac_artery_right",
    "iliac_vena_left",
    "iliac_vena_right",
    "small_bowel",
    "duodenum",
    "colon",
    "rib_left_1",
    "rib_left_2",
    "rib_left_3",
    "rib_left_4",
    "rib_left_5",
    "rib_left_6",
    "rib_left_7",
    "rib_left_8",
    "rib_left_9",
    "rib_left_10",
    "rib_left_11",
    "rib_left_12",
    "rib_right_1",
    "rib_right_2",
    "rib_right_3",
    "rib_right_4",
    "rib_right_5",
    "rib_right_6",
    "rib_right_7",
    "rib_right_8",
    "rib_right_9",
    "rib_right_10",
    "rib_right_11",
    "rib_right_12",
    "humerus_left",
    "humerus_right",
    "scapula_left",
    "scapula_right",
    "clavicula_left",
    "clavicula_right",
    "femur_left",
    "femur_right",
    "hip_left",
    "hip_right",
    "sacrum",
    "face",
    "gluteus_maximus_left",
    "gluteus_maximus_right",
    "gluteus_medius_left",
    "gluteus_medius_right",
    "gluteus_minimus_left",
    "gluteus_minimus_right",
    "autochthon_left",
    "autochthon_right",
    "iliopsoas_left",
    "iliopsoas_right",
    "urinary_bladder",
)
ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class MonaiRun:
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    output_path: Path | None


def segmentation_from_array(
    label_volume,
    spacing_mm: tuple[float, float, float],
    label_names: dict[int, str] | None = None,
    source: str = "custom",
    output_path: Path | None = None,
    source_volume=None,
) -> SegmentationResult:
    import numpy as np

    label_names = label_names or {}
    labels_array = np.asarray(label_volume)
    source_array = _source_volume_array(source_volume)
    values = [int(v) for v in np.unique(labels_array) if int(v) != 0]
    labels = []
    for value in values:
        mask = labels_array == value
        voxel_count = int(np.count_nonzero(mask))
        labels.append(
            SegmentLabel(
                value=value,
                name=label_names.get(value, f"label_{value}"),
                voxel_count=voxel_count,
                mean_hounsfield=_mean_hounsfield_for_mask(mask, source_array),
            )
        )
    return SegmentationResult(
        label_volume=label_volume,
        spacing_mm=spacing_mm,
        labels=tuple(labels),
        source=source,
        output_path=output_path,
    )


def add_skin_shell_label(
    segmentation: SegmentationResult,
    volume: Volume,
    threshold_hu: float = -550.0,
    shell_thickness_mm: float = 2.0,
) -> SegmentationResult:
    import numpy as np

    data = np.asarray(volume.data)
    if data.ndim != 3:
        return segmentation
    body_mask = np.isfinite(data) & (data > float(threshold_hu))
    body_mask = _clean_body_mask(body_mask)
    if not bool(body_mask.any()):
        return segmentation

    labels = tuple(
        label
        for label in segmentation.labels
        if label.value != SYNTHETIC_SKIN_LABEL_VALUE and label.name != SYNTHETIC_SKIN_LABEL_NAME
    )
    shell_mask = _skin_shell_mask(body_mask, volume.spacing_mm, shell_thickness_mm)
    shell_voxels = int(np.count_nonzero(shell_mask))
    labels = labels + (
        SegmentLabel(
            value=SYNTHETIC_SKIN_LABEL_VALUE,
            name=SYNTHETIC_SKIN_LABEL_NAME,
            voxel_count=shell_voxels,
            mean_hounsfield=_mean_hounsfield_for_mask(shell_mask, data),
        ),
    )
    auxiliary = dict(segmentation.auxiliary_label_volumes)
    auxiliary[SYNTHETIC_SKIN_LABEL_VALUE] = body_mask
    return replace(segmentation, labels=labels, auxiliary_label_volumes=auxiliary)


def ensure_monai_bundle(
    bundle_name: str = DEFAULT_MONAI_BUNDLE,
    bundle_dir: str | Path = DEFAULT_MODEL_ROOT,
    python_executable: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    root = Path(bundle_dir)
    root.mkdir(parents=True, exist_ok=True)
    bundle_root = root / bundle_name
    if bundle_root.exists():
        return None
    executable = resolve_monai_python_executable(python_executable)
    code = (
        "from monai.bundle.scripts import download;"
        f"download(name={bundle_name!r}, bundle_dir={str(root)!r}, progress=False)"
    )
    command = [executable, "-c", code]
    try:
        return subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            f"MONAI bundle download could not start with {executable}: {exc}",
        )


def run_monai_bundle(
    volume: Volume,
    bundle_name: str = DEFAULT_MONAI_BUNDLE,
    bundle_dir: str | Path = DEFAULT_MODEL_ROOT,
    output_dir: str | Path = DEFAULT_OUTPUT_ROOT,
    python_executable: str | None = None,
    highres: bool = False,
    extra_args: tuple[str, ...] = (),
    progress: ProgressCallback | None = None,
    timeout_seconds: float | None = None,
) -> tuple[SegmentationResult | None, MonaiRun]:
    progress_total = 9
    _report_progress(progress, 0, progress_total, "preparing MONAI workspace")
    if bundle_name not in SUPPORTED_MONAI_BUNDLES:
        return None, _unsupported_bundle_run(bundle_name)
    ensure_work_roots()
    executable = resolve_monai_python_executable(python_executable)
    root = Path(bundle_dir)
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    dataset_dir = out_root / "monai_inputs" / _safe_path_name(bundle_name)
    input_dir = dataset_dir / "imagesTs"
    input_dir.mkdir(parents=True, exist_ok=True)
    input_path = input_dir / "organiq_input_ct.nii.gz"
    _report_progress(progress, 1, progress_total, "writing CT NIfTI")
    write_nifti(volume, input_path)

    _report_progress(progress, 2, progress_total, "checking MONAI bundle")
    download = ensure_monai_bundle(bundle_name, root, executable)
    bundle_root = root / bundle_name
    if download is not None and download.returncode != 0:
        return None, _download_failed_run(bundle_name, bundle_root, download)
    if not bundle_root.exists():
        return None, _missing_bundle_directory_run(bundle_name, bundle_root, download)
    inference_config = bundle_root / "configs" / "inference.json"
    if not inference_config.exists():
        return None, _missing_inference_config_run(bundle_name, inference_config, download)
    _report_progress(progress, 3, progress_total, "running MONAI inference")
    run = _run_monai_inference(
        executable,
        bundle_root,
        dataset_dir,
        out_root,
        bool(highres),
        extra_args,
        input_path,
        progress,
        timeout_seconds,
    )
    if run.return_code != 0 or run.output_path is None:
        return None, run
    _report_progress(progress, 4, progress_total, "decoding MONAI labels")
    segmentation = load_label_nifti(run.output_path, volume.spacing_mm, _label_names_from_bundle(bundle_root), volume)
    _report_progress(progress, 5, progress_total, "adding outer skin shell")
    segmentation = add_skin_shell_label(segmentation, volume)
    if _has_anatomy_labels(segmentation):
        _report_progress(progress, 6, progress_total, "validated anatomy labels")
        _report_progress(progress, 7, progress_total, "high-resolution retry not needed")
        _report_progress(progress, 8, progress_total, "finalising segmentation")
        _report_progress(progress, progress_total, progress_total, f"segmented {len(segmentation.labels)} labels")
        return segmentation, run
    if not highres:
        _report_progress(progress, 6, progress_total, "retrying high-resolution inference")
        retry = _run_monai_inference(
            executable,
            bundle_root,
            dataset_dir,
            out_root,
            True,
            extra_args,
            input_path,
            progress,
            timeout_seconds,
        )
        if retry.return_code == 0 and retry.output_path is not None:
            _report_progress(progress, 7, progress_total, "decoding high-resolution labels")
            retry_segmentation = load_label_nifti(
                retry.output_path,
                volume.spacing_mm,
                _label_names_from_bundle(bundle_root),
                volume,
            )
            _report_progress(progress, 8, progress_total, "adding outer skin shell")
            retry_segmentation = add_skin_shell_label(retry_segmentation, volume)
            if _has_anatomy_labels(retry_segmentation):
                _report_progress(
                    progress,
                    progress_total,
                    progress_total,
                    f"segmented {len(retry_segmentation.labels)} labels",
                )
                return retry_segmentation, retry
        return None, _with_run_error(retry, "MONAI output contained no anatomy labels")
    return None, _with_run_error(run, "MONAI output contained no anatomy labels")


def load_label_nifti(
    label_path: str | Path,
    spacing_mm: tuple[float, float, float],
    label_names: dict[int, str] | None = None,
    source_volume=None,
) -> SegmentationResult:
    try:
        import nibabel as nib
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("nibabel and numpy are required for label NIfTI loading") from exc

    path = Path(label_path)
    data = nib.load(str(path)).get_fdata()
    labels = np.asarray(data, dtype=np.int16)
    if labels.ndim == 3:
        labels = np.transpose(labels, (2, 1, 0))
    return segmentation_from_array(
        labels,
        spacing_mm,
        label_names,
        source="monai_bundle",
        output_path=path,
        source_volume=source_volume,
    )


def _source_volume_array(source_volume):
    if source_volume is None:
        return None
    try:
        import numpy as np
    except ImportError:
        return None

    data = getattr(source_volume, "data", source_volume)
    try:
        return np.asarray(data, dtype=np.float32)
    except Exception:
        return None


def _mean_hounsfield_for_mask(mask, source_array) -> float | None:
    if source_array is None:
        return None
    try:
        import numpy as np
    except ImportError:
        return None

    mask_array = np.asarray(mask, dtype=bool)
    if source_array.shape != mask_array.shape:
        return None
    values = np.asarray(source_array, dtype=np.float32)[mask_array]
    if values.size == 0:
        return None
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    return float(np.mean(finite))


def _clean_body_mask(mask):
    try:
        from scipy import ndimage as ndi
    except Exception:
        return mask

    structure = ndi.generate_binary_structure(3, 2)
    clean = ndi.binary_closing(mask, structure=structure, iterations=2)
    clean = ndi.binary_fill_holes(clean)
    labels, count = ndi.label(clean)
    if count > 1:
        sizes = ndi.sum(clean, labels, index=range(1, count + 1))
        keep = int(sizes.argmax()) + 1
        clean = labels == keep
    return clean


def _skin_shell_mask(mask, spacing_mm: tuple[float, float, float], thickness_mm: float):
    import numpy as np

    min_spacing = max(min(float(value) for value in spacing_mm), 1.0e-6)
    iterations = max(1, int(round(float(thickness_mm) / min_spacing)))
    eroded = mask
    try:
        from scipy import ndimage as ndi

        eroded = ndi.binary_erosion(mask, iterations=iterations)
    except Exception:
        for _ in range(iterations):
            eroded = _erode_one_voxel(eroded)
    return mask & ~eroded


def _erode_one_voxel(mask):
    import numpy as np

    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    eroded = mask.copy()
    for dz, dy, dx in (
        (-1, 0, 0),
        (1, 0, 0),
        (0, -1, 0),
        (0, 1, 0),
        (0, 0, -1),
        (0, 0, 1),
    ):
        z0 = 1 + dz
        y0 = 1 + dy
        x0 = 1 + dx
        eroded &= padded[z0 : z0 + mask.shape[0], y0 : y0 + mask.shape[1], x0 : x0 + mask.shape[2]]
    return eroded


def _nifti_mtimes(root: Path) -> dict[Path, int]:
    mtimes: dict[Path, int] = {}
    if not root.exists():
        return mtimes
    for path in root.rglob("*"):
        if path.is_file() and (path.name.endswith(".nii") or path.name.endswith(".nii.gz")):
            mtimes[_resolve_path(path)] = path.stat().st_mtime_ns
    return mtimes


def _latest_nifti(
    root: Path,
    exclude: set[Path],
    newer_than: float = 0.0,
    baseline_mtimes: dict[Path, int] | None = None,
) -> Path | None:
    candidates = []
    baseline_mtimes = baseline_mtimes or {}
    for path in root.rglob("*"):
        if path.is_file() and (path.name.endswith(".nii") or path.name.endswith(".nii.gz")):
            resolved = _resolve_path(path)
            if resolved in exclude:
                continue
            stat = path.stat()
            baseline_mtime = baseline_mtimes.get(resolved)
            if baseline_mtime is not None:
                if stat.st_mtime_ns <= baseline_mtime:
                    continue
            elif stat.st_mtime < newer_than:
                continue
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.stat().st_mtime_ns, str(item)))


def _run_monai_inference(
    executable: str,
    bundle_root: Path,
    dataset_dir: Path,
    output_dir: Path,
    highres: bool,
    extra_args: tuple[str, ...],
    input_path: Path,
    progress: ProgressCallback | None = None,
    timeout_seconds: float | None = None,
) -> MonaiRun:
    code = _monai_inference_code(
        bundle_root=bundle_root,
        dataset_dir=dataset_dir,
        output_dir=output_dir,
        highres=highres,
    )
    command = [executable, "-c", code, *extra_args]
    baseline_mtimes = _nifti_mtimes(output_dir)
    run_started = time.time()
    result = _run_bounded_subprocess(
        command,
        cwd=bundle_root,
        timeout_seconds=_normalise_timeout(timeout_seconds),
        progress=progress,
        status="running high-resolution MONAI inference" if highres else "running MONAI inference",
    )
    exclude = {_resolve_path(input_path)}
    output_path = _latest_nifti(
        output_dir,
        exclude=exclude,
        newer_than=run_started,
        baseline_mtimes=baseline_mtimes,
    )
    if output_path is None:
        output_path = _latest_nifti(output_dir, exclude=exclude, newer_than=run_started - 2.0)
    return MonaiRun(tuple(command), result.returncode, result.stdout, result.stderr, output_path)


class _BoundedTextBuffer:
    def __init__(self, limit: int = MONAI_OUTPUT_LIMIT_CHARS):
        self._limit = max(1, int(limit))
        self._parts: list[str] = []
        self._length = 0
        self._truncated = 0
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._parts.append(text)
            self._length += len(text)
            while self._length > self._limit and self._parts:
                overflow = self._length - self._limit
                first = self._parts[0]
                if len(first) <= overflow:
                    self._parts.pop(0)
                    self._length -= len(first)
                    self._truncated += len(first)
                else:
                    self._parts[0] = first[overflow:]
                    self._length -= overflow
                    self._truncated += overflow
                    break

    def text(self) -> str:
        with self._lock:
            body = "".join(self._parts)
            if self._truncated:
                return f"[truncated {self._truncated} chars]\n{body}"
            return body


def _run_bounded_subprocess(
    command: list[str],
    cwd: Path,
    timeout_seconds: float,
    progress: ProgressCallback | None,
    status: str,
) -> subprocess.CompletedProcess[str]:
    stdout = _BoundedTextBuffer()
    stderr = _BoundedTextBuffer()
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, "", f"{status} could not start: {exc}")

    threads = (
        threading.Thread(target=_read_stream, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_read_stream, args=(process.stderr, stderr), daemon=True),
    )
    for thread in threads:
        thread.start()

    return_code: int | None = None
    while return_code is None:
        elapsed = time.monotonic() - started
        if _progress_cancel_requested(progress):
            _terminate_process(process)
            return_code = process.wait(timeout=10.0)
            stderr.append("\nMONAI inference cancelled by the user.")
            break
        if timeout_seconds > 0.0 and elapsed > timeout_seconds:
            _terminate_process(process)
            return_code = process.wait(timeout=10.0)
            stderr.append(f"\nMONAI inference timed out after {timeout_seconds:.0f} seconds.")
            break
        try:
            _report_progress(progress, 3, 9, _runtime_status(status, elapsed))
        except RuntimeError:
            _terminate_process(process)
            return_code = process.wait(timeout=10.0)
            stderr.append("\nMONAI inference cancelled by the user.")
            break
        return_code = process.poll()
        if return_code is None:
            time.sleep(1.0)

    for thread in threads:
        thread.join(timeout=2.0)
    return subprocess.CompletedProcess(command, int(return_code), stdout.text(), stderr.text())


def _read_stream(stream, buffer: _BoundedTextBuffer) -> None:
    if stream is None:
        return
    try:
        for line in iter(stream.readline, ""):
            buffer.append(line)
    finally:
        stream.close()


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()


def _runtime_status(status: str, elapsed: float) -> str:
    telemetry = _process_memory_telemetry()
    if telemetry:
        return f"{status} ({elapsed:.0f}s, {telemetry})"
    return f"{status} ({elapsed:.0f}s)"


def _process_memory_telemetry() -> str:
    try:
        import psutil
    except Exception:
        return ""
    try:
        process = psutil.Process(os.getpid())
        rss_gb = process.memory_info().rss / (1024.0**3)
        memory = psutil.virtual_memory()
        return f"rss {rss_gb:.1f} GB, host memory {memory.percent:.0f}%"
    except Exception:
        return ""


def _progress_cancel_requested(progress: ProgressCallback | None) -> bool:
    event = getattr(progress, "cancel_event", None)
    if event is not None and event.is_set():
        return True
    return bool(getattr(progress, "cancel_requested", False))


def _normalise_timeout(timeout_seconds: float | None) -> float:
    if timeout_seconds is not None:
        return max(0.0, float(timeout_seconds))
    value = os.environ.get("ORGANIQ_MONAI_TIMEOUT_SECONDS")
    if not value:
        return float(DEFAULT_MONAI_TIMEOUT_SECONDS)
    try:
        return max(0.0, float(value))
    except ValueError:
        return float(DEFAULT_MONAI_TIMEOUT_SECONDS)


def _monai_inference_code(bundle_root: Path, dataset_dir: Path, output_dir: Path, highres: bool) -> str:
    overrides = {
        "bundle_root": str(bundle_root),
        "dataset_dir": str(dataset_dir),
        "output_dir": str(output_dir),
        "displayable_configs#highres": bool(highres),
    }
    return (
        "from monai.bundle.scripts import run;"
        f"run(config_file={str(bundle_root / 'configs' / 'inference.json')!r}, **{overrides!r})"
    )


def _safe_path_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._") or "bundle"


def _resolve_path(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _has_anatomy_labels(segmentation: SegmentationResult) -> bool:
    return any(label.value != SYNTHETIC_SKIN_LABEL_VALUE for label in segmentation.labels)


def _with_run_error(run: MonaiRun, message: str) -> MonaiRun:
    stderr = run.stderr.rstrip()
    stderr = f"{stderr}\n{message}" if stderr else message
    return MonaiRun(run.command, run.return_code or 1, run.stdout, stderr, run.output_path)


def _unsupported_bundle_run(bundle_name: str) -> MonaiRun:
    supported = ", ".join(SUPPORTED_MONAI_BUNDLES)
    stderr = (
        f"Unsupported MONAI bundle: {bundle_name}. "
        f"Organiq currently supports {supported}. "
        "The supported runner expects configs/inference.json and whole-body CT label metadata."
    )
    return MonaiRun((), 1, "", stderr, None)


def _download_failed_run(
    bundle_name: str,
    bundle_root: Path,
    result: subprocess.CompletedProcess[str],
) -> MonaiRun:
    stderr = _join_error_lines(
        result.stderr,
        f"MONAI bundle download failed for {bundle_name}.",
        f"Expected bundle directory: {bundle_root}",
        "Check the bundle name, network access and MONAI bundle availability.",
    )
    return MonaiRun(_completed_command(result), result.returncode or 1, result.stdout, stderr, None)


def _missing_bundle_directory_run(
    bundle_name: str,
    bundle_root: Path,
    result: subprocess.CompletedProcess[str] | None,
) -> MonaiRun:
    stderr = _join_error_lines(
        result.stderr if result is not None else "",
        f"MONAI bundle download for {bundle_name} completed but did not create the bundle directory.",
        f"Expected bundle directory: {bundle_root}",
    )
    return MonaiRun(
        _completed_command(result),
        result.returncode if result is not None and result.returncode else 1,
        result.stdout if result is not None else "",
        stderr,
        None,
    )


def _missing_inference_config_run(
    bundle_name: str,
    inference_config: Path,
    result: subprocess.CompletedProcess[str] | None,
) -> MonaiRun:
    stderr = _join_error_lines(
        result.stderr if result is not None else "",
        f"MONAI bundle {bundle_name} is missing the supported inference config.",
        f"Expected config: {inference_config}",
    )
    return MonaiRun(
        _completed_command(result),
        1,
        result.stdout if result is not None else "",
        stderr,
        None,
    )


def _join_error_lines(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _completed_command(result: subprocess.CompletedProcess[str] | None) -> tuple[str, ...]:
    if result is None:
        return ()
    args = result.args
    if isinstance(args, (list, tuple)):
        return tuple(str(part) for part in args)
    return (str(args),)


def _report_progress(progress: ProgressCallback | None, completed: int, total: int, status: str) -> None:
    if progress is None:
        return
    progress(int(completed), max(int(total), 1), status)


def resolve_monai_python_executable(python_executable: str | None = None) -> str:
    if python_executable:
        return python_executable

    for direct_override in isaac_python_candidates("kit/python/python.exe"):
        if direct_override.exists():
            return str(direct_override)

    executable = Path(resolve_python_executable())
    if executable.suffix.lower() == ".bat":
        for parent in (executable.parent, *executable.parents):
            candidate = parent / "python" / "python.exe"
            if candidate.exists():
                return str(candidate)
    return str(executable)


def _label_names_from_bundle(bundle_root: Path) -> dict[int, str]:
    metadata = bundle_root / "configs" / "metadata.json"
    if not metadata.exists():
        return _whole_body_ct_label_names() if bundle_root.name == DEFAULT_MONAI_BUNDLE else {}
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except Exception:
        return _whole_body_ct_label_names() if bundle_root.name == DEFAULT_MONAI_BUNDLE else {}

    names = _label_names_from_metadata(data)
    if names:
        return names
    if _is_whole_body_ct_bundle(bundle_root, data):
        return _whole_body_ct_label_names()
    return {}


def _label_names_from_metadata(data: dict) -> dict[int, str]:
    for candidate in (
        data.get("labels"),
        data.get("label_names"),
        data.get("network_data_format", {}).get("labels"),
    ):
        names = _coerce_label_name_map(candidate)
        if names:
            return names

    outputs = data.get("network_data_format", {}).get("outputs", {})
    if not isinstance(outputs, dict):
        return {}
    output_items = []
    if isinstance(outputs.get("pred"), dict):
        output_items.append(outputs["pred"])
    output_items.extend(value for key, value in outputs.items() if key != "pred" and isinstance(value, dict))
    for output in output_items:
        names = _coerce_label_name_map(output.get("channel_def"))
        if names:
            return names
    return {}


def _coerce_label_name_map(candidate) -> dict[int, str]:
    if isinstance(candidate, dict):
        names = {}
        for key, value in candidate.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if index != 0:
                names[index] = str(value)
        return names
    if isinstance(candidate, list):
        return {index: str(value) for index, value in enumerate(candidate) if index != 0}
    return {}


def _is_whole_body_ct_bundle(bundle_root: Path, metadata: dict) -> bool:
    return (
        bundle_root.name == DEFAULT_MONAI_BUNDLE
        or metadata.get("name") == "Whole Body CT Segmentation"
        or (
            metadata.get("data_source") == "TotalSegmentator"
            and "104" in str(metadata.get("pred_classes", ""))
        )
    )


def _whole_body_ct_label_names() -> dict[int, str]:
    return {index: name for index, name in enumerate(WHOLE_BODY_CT_LABELS) if index != 0}
