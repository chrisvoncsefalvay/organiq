import numpy as np
import os
import subprocess
import sys
import time

from com.chrisvoncsefalvay.organiq.models import Volume
from com.chrisvoncsefalvay.organiq import segmentation as segmentation_module
from com.chrisvoncsefalvay.organiq.segmentation import (
    SYNTHETIC_SKIN_LABEL_VALUE,
    add_skin_shell_label,
    ensure_monai_bundle,
    segmentation_from_array,
)


def test_segmentation_from_array_skips_background():
    labels = np.array([[[0, 1], [2, 2]]], dtype=np.uint8)
    segmentation = segmentation_from_array(labels, (1.0, 1.0, 1.0), {1: "bone", 2: "lung"})
    assert [label.name for label in segmentation.labels] == ["bone", "lung"]
    assert [label.voxel_count for label in segmentation.labels] == [1, 2]


def test_segmentation_records_mean_hounsfield_per_label():
    labels = np.array([[[0, 1], [2, 2]]], dtype=np.uint8)
    data = np.array([[[-1000.0, 700.0], [-820.0, -780.0]]], dtype=np.float32)
    volume = Volume(data=data, spacing_mm=(1.0, 1.0, 1.0))

    segmentation = segmentation_from_array(labels, volume.spacing_mm, {1: "bone", 2: "lung"}, source_volume=volume)

    by_name = {label.name: label for label in segmentation.labels}
    assert by_name["bone"].mean_hounsfield == 700.0
    assert by_name["lung"].mean_hounsfield == -800.0


def test_skin_shell_label_uses_ct_body_envelope():
    data = np.full((8, 8, 8), -1000.0, dtype=np.float32)
    data[1:7, 1:7, 1:7] = 45.0
    labels = np.zeros(data.shape, dtype=np.uint8)
    labels[2:6, 2:6, 2:6] = 3
    volume = Volume(data=data, spacing_mm=(1.0, 1.0, 1.0))
    segmentation = segmentation_from_array(labels, volume.spacing_mm, {3: "soft_tissue"})

    with_shell = add_skin_shell_label(segmentation, volume)

    shell = [label for label in with_shell.labels if label.value == SYNTHETIC_SKIN_LABEL_VALUE][0]
    assert shell.name == "skin_shell"
    assert shell.voxel_count > 0
    assert shell.mean_hounsfield == 45.0
    assert int(with_shell.auxiliary_label_volumes[SYNTHETIC_SKIN_LABEL_VALUE].sum()) >= shell.voxel_count


def test_ensure_monai_bundle_uses_requested_python(tmp_path, monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(segmentation_module.subprocess, "run", fake_run)

    result = ensure_monai_bundle("test_bundle", tmp_path, python_executable="C:/isaac/python.bat")

    assert result is not None
    assert seen["command"][:2] == ["C:/isaac/python.bat", "-c"]
    assert "monai.bundle.scripts" in seen["command"][2]
    assert "test_bundle" in seen["command"][2]
    assert seen["kwargs"]["capture_output"] is True


def test_ensure_monai_bundle_reports_launcher_failure(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        raise OSError("missing python")

    monkeypatch.setattr(segmentation_module.subprocess, "run", fake_run)

    result = ensure_monai_bundle("test_bundle", tmp_path, python_executable="C:/missing/python.exe")

    assert result is not None
    assert result.returncode == 1
    assert "MONAI bundle download could not start" in result.stderr
    assert "C:/missing/python.exe" in result.stderr


def test_run_monai_rejects_unsupported_bundle_before_staging(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("unsupported bundles should not stage or download")

    monkeypatch.setattr(segmentation_module, "write_nifti", fail_if_called)
    monkeypatch.setattr(segmentation_module, "ensure_monai_bundle", fail_if_called)

    volume = Volume(data=np.zeros((1, 1, 1), dtype=np.float32), spacing_mm=(1.0, 1.0, 1.0))
    result, run = segmentation_module.run_monai_bundle(
        volume,
        bundle_name="multi_organ_segmentation",
        bundle_dir=tmp_path / "models",
        output_dir=tmp_path / "outputs",
        python_executable="python",
    )

    assert result is None
    assert run.command == ()
    assert "Unsupported MONAI bundle: multi_organ_segmentation" in run.stderr
    assert "wholeBody_ct_segmentation" in run.stderr


def test_run_monai_reports_download_failure_context(tmp_path, monkeypatch):
    def fake_write_nifti(volume, output_path):
        output_path.write_bytes(b"placeholder")
        return output_path

    def fake_download(*args, **kwargs):
        command = ["python", "-m", "monai.bundle", "download"]
        return subprocess.CompletedProcess(command, 2, "stdout text", "upstream error")

    monkeypatch.setattr(segmentation_module, "write_nifti", fake_write_nifti)
    monkeypatch.setattr(segmentation_module, "ensure_monai_bundle", fake_download)

    volume = Volume(data=np.zeros((1, 1, 1), dtype=np.float32), spacing_mm=(1.0, 1.0, 1.0))
    result, run = segmentation_module.run_monai_bundle(
        volume,
        bundle_name="wholeBody_ct_segmentation",
        bundle_dir=tmp_path / "models",
        output_dir=tmp_path / "outputs",
        python_executable="python",
    )

    assert result is None
    assert run.return_code == 2
    assert run.stdout == "stdout text"
    assert "upstream error" in run.stderr
    assert "MONAI bundle download failed for wholeBody_ct_segmentation" in run.stderr
    assert "Expected bundle directory" in run.stderr


def test_run_monai_reports_missing_download_directory(tmp_path, monkeypatch):
    def fake_write_nifti(volume, output_path):
        output_path.write_bytes(b"placeholder")
        return output_path

    def fake_download(*args, **kwargs):
        command = ["python", "-m", "monai.bundle", "download"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(segmentation_module, "write_nifti", fake_write_nifti)
    monkeypatch.setattr(segmentation_module, "ensure_monai_bundle", fake_download)

    volume = Volume(data=np.zeros((1, 1, 1), dtype=np.float32), spacing_mm=(1.0, 1.0, 1.0))
    result, run = segmentation_module.run_monai_bundle(
        volume,
        bundle_name="wholeBody_ct_segmentation",
        bundle_dir=tmp_path / "models",
        output_dir=tmp_path / "outputs",
        python_executable="python",
    )

    assert result is None
    assert "completed but did not create the bundle directory" in run.stderr
    assert "Expected bundle directory" in run.stderr


def test_run_monai_reports_missing_supported_inference_config(tmp_path, monkeypatch):
    bundle = tmp_path / "models" / "wholeBody_ct_segmentation"
    bundle.mkdir(parents=True)

    def fake_write_nifti(volume, output_path):
        output_path.write_bytes(b"placeholder")
        return output_path

    monkeypatch.setattr(segmentation_module, "write_nifti", fake_write_nifti)
    monkeypatch.setattr(segmentation_module, "ensure_monai_bundle", lambda *args, **kwargs: None)

    volume = Volume(data=np.zeros((1, 1, 1), dtype=np.float32), spacing_mm=(1.0, 1.0, 1.0))
    result, run = segmentation_module.run_monai_bundle(
        volume,
        bundle_name="wholeBody_ct_segmentation",
        bundle_dir=tmp_path / "models",
        output_dir=tmp_path / "outputs",
        python_executable="python",
    )

    assert result is None
    assert "missing the supported inference config" in run.stderr
    assert "configs" in run.stderr
    assert "inference.json" in run.stderr


def test_run_monai_stages_bundle_dataset_and_nested_highres_override(tmp_path, monkeypatch):
    seen = {}
    bundle = tmp_path / "models" / "wholeBody_ct_segmentation"
    (bundle / "configs").mkdir(parents=True)
    (bundle / "configs" / "inference.json").write_text("{}", encoding="utf-8")

    def fake_write_nifti(volume, output_path):
        seen["input_path"] = output_path
        output_path.write_bytes(b"placeholder")
        return output_path

    def fake_download(*args, **kwargs):
        return None

    def fake_process(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        output = tmp_path / "outputs" / "prediction.nii.gz"
        output.write_bytes(b"prediction")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(segmentation_module, "write_nifti", fake_write_nifti)
    monkeypatch.setattr(segmentation_module, "ensure_monai_bundle", fake_download)
    monkeypatch.setattr(segmentation_module, "_run_bounded_subprocess", fake_process)
    monkeypatch.setattr(
        segmentation_module,
        "load_label_nifti",
        lambda output_path, spacing_mm, label_names, source_volume=None: segmentation_from_array(
            np.array([[[1]]], dtype=np.uint8),
            spacing_mm,
            {1: "spleen"},
            output_path=output_path,
            source_volume=source_volume,
        ),
    )

    volume = Volume(data=np.zeros((1, 1, 1), dtype=np.float32), spacing_mm=(1.0, 1.0, 1.0))
    segmentation, run = segmentation_module.run_monai_bundle(
        volume,
        bundle_name="wholeBody_ct_segmentation",
        bundle_dir=tmp_path / "models",
        output_dir=tmp_path / "outputs",
        python_executable="python",
        highres=False,
    )

    assert segmentation is not None
    assert seen["input_path"].parts[-2:] == ("imagesTs", "organiq_input_ct.nii.gz")
    code = seen["command"][2]
    assert "displayable_configs#highres" in code
    assert "'displayable_configs#highres': False" in code
    assert "'dataset_dir':" in code
    assert "datalist" not in code
    assert "highres=" not in code
    assert run.output_path == tmp_path / "outputs" / "prediction.nii.gz"


def test_run_monai_retries_highres_when_lowres_output_is_empty(tmp_path, monkeypatch):
    calls = []
    bundle = tmp_path / "models" / "wholeBody_ct_segmentation"
    (bundle / "configs").mkdir(parents=True)
    (bundle / "configs" / "inference.json").write_text("{}", encoding="utf-8")

    def fake_write_nifti(volume, output_path):
        output_path.write_bytes(b"placeholder")
        return output_path

    def fake_process(command, **kwargs):
        calls.append(command[2])
        output = tmp_path / "outputs" / f"prediction_{len(calls)}.nii.gz"
        output.write_bytes(b"prediction")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_load_label_nifti(output_path, spacing_mm, label_names, source_volume=None):
        if output_path.name == "prediction_1.nii.gz":
            return segmentation_from_array(
                np.zeros((1, 1, 1), dtype=np.uint8),
                spacing_mm,
                output_path=output_path,
                source_volume=source_volume,
            )
        return segmentation_from_array(
            np.array([[[1]]], dtype=np.uint8),
            spacing_mm,
            {1: "spleen"},
            output_path=output_path,
            source_volume=source_volume,
        )

    monkeypatch.setattr(segmentation_module, "write_nifti", fake_write_nifti)
    monkeypatch.setattr(segmentation_module, "ensure_monai_bundle", lambda *args, **kwargs: None)
    monkeypatch.setattr(segmentation_module, "_run_bounded_subprocess", fake_process)
    monkeypatch.setattr(segmentation_module, "load_label_nifti", fake_load_label_nifti)

    volume = Volume(data=np.zeros((1, 1, 1), dtype=np.float32), spacing_mm=(1.0, 1.0, 1.0))
    segmentation, run = segmentation_module.run_monai_bundle(
        volume,
        bundle_name="wholeBody_ct_segmentation",
        bundle_dir=tmp_path / "models",
        output_dir=tmp_path / "outputs",
        python_executable="python",
        highres=False,
    )

    assert segmentation is not None
    assert [label.name for label in segmentation.labels if label.value != SYNTHETIC_SKIN_LABEL_VALUE] == ["spleen"]
    assert len(calls) == 2
    assert "'displayable_configs#highres': False" in calls[0]
    assert "'displayable_configs#highres': True" in calls[1]
    assert run.output_path == tmp_path / "outputs" / "prediction_2.nii.gz"


def test_bounded_subprocess_limits_output(tmp_path):
    result = segmentation_module._run_bounded_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 20000)"],
        cwd=tmp_path,
        timeout_seconds=30.0,
        progress=None,
        status="test process",
    )

    assert result.returncode == 0
    assert result.stdout.startswith("[truncated ")
    assert len(result.stdout) <= segmentation_module.MONAI_OUTPUT_LIMIT_CHARS + 64


def test_bounded_subprocess_times_out(tmp_path):
    result = segmentation_module._run_bounded_subprocess(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=tmp_path,
        timeout_seconds=0.1,
        progress=None,
        status="test process",
    )

    assert result.returncode != 0
    assert "timed out" in result.stderr


def test_latest_nifti_ignores_stale_outputs(tmp_path):
    stale = tmp_path / "old" / "old.nii.gz"
    stale.parent.mkdir()
    stale.write_bytes(b"old")
    stale_time = time.time() - 60
    os.utime(stale, (stale_time, stale_time))
    fresh_threshold = time.time() - 1

    assert segmentation_module._latest_nifti(tmp_path, exclude=set(), newer_than=fresh_threshold) is None

    fresh = tmp_path / "new" / "new.nii.gz"
    fresh.parent.mkdir()
    fresh.write_bytes(b"new")

    assert segmentation_module._latest_nifti(tmp_path, exclude=set(), newer_than=fresh_threshold) == fresh


def test_monai_metadata_channel_def_decodes_whole_body_labels(tmp_path):
    bundle = tmp_path / "wholeBody_ct_segmentation"
    config_dir = bundle / "configs"
    config_dir.mkdir(parents=True)
    (config_dir / "metadata.json").write_text(
        """
{
  "network_data_format": {
    "outputs": {
      "pred": {
        "channel_def": {
          "0": "background",
          "5": "liver",
          "44": "heart_myocardium",
          "104": "urinary_bladder"
        }
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    names = segmentation_module._label_names_from_bundle(bundle)

    assert names[5] == "liver"
    assert names[44] == "heart_myocardium"
    assert names[104] == "urinary_bladder"
    assert 0 not in names


def test_whole_body_ct_label_fallback_without_metadata(tmp_path):
    bundle = tmp_path / "wholeBody_ct_segmentation"
    bundle.mkdir()

    names = segmentation_module._label_names_from_bundle(bundle)

    assert names[1] == "spleen"
    assert names[13] == "lung_upper_lobe_left"
    assert names[58] == "rib_left_1"
    assert names[104] == "urinary_bladder"


def test_monai_bundle_presets_only_include_supported_models():
    assert segmentation_module.SUPPORTED_MONAI_BUNDLES == ("wholeBody_ct_segmentation",)
    assert segmentation_module.MONAI_BUNDLE_PRESETS == segmentation_module.SUPPORTED_MONAI_BUNDLES


def test_segmentation_uses_decoded_monai_labels():
    labels = np.array([[[0, 5], [44, 104]]], dtype=np.uint8)
    segmentation = segmentation_from_array(
        labels,
        (1.0, 1.0, 1.0),
        segmentation_module._whole_body_ct_label_names(),
    )

    assert [label.name for label in segmentation.labels] == ["liver", "heart_myocardium", "urinary_bladder"]

