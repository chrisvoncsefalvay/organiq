# Organiq

Organiq is an NVIDIA Isaac Sim extension for turning CT DICOM studies into simulation-ready USD anatomy.

The extension workflow is:

1. Load a DICOM folder and select a CT series.
2. Segment the volume using a MONAI bundle and add a CT-derived outer skin shell.
3. Select labels to mesh from grouped anatomy trees, with global and group-level selection controls.
4. Build organic meshes from signed distance fields generated from the selected label masks and carry per-label mean Hounsfield values into the mesh records.
5. Export a USD scene.
6. Instantiate the scene in Isaac with tissue-specific textured visual materials and physics.

The supported MONAI bundle is `wholeBody_ct_segmentation`. It is the only default segment panel choice because it resolves with `configs/inference.json`, `models/model_lowres.pt`, `models/model.pt` and TotalSegmentator-style label metadata. Downloads, model caches and USD exports use a per-user Organiq cache directory by default. Set `ORGANIQ_WORK_ROOT` to move those files to a project, shared or scratch volume.

Organiq stages MONAI input volumes as a bundle-style `dataset_dir\imagesTs` folder and writes the whole-body bundle `displayable_configs#highres` override explicitly. The default UI path starts with the low-resolution checkpoint and retries the high-resolution checkpoint if the bundle returns only background.

If a manually supplied bundle name is not supported, or a cached bundle is missing the expected inference config, Organiq stops before inference and reports the unsupported name or missing file path.

## launch

From PowerShell:

```powershell
$env:ISAACSIM_ROOT = "C:\path\to\isaacsim"
tools\launch_organiq.ps1
```

The launcher enables `com.chrisvoncsefalvay.organiq` from this repository and also enables the timeline and PhysX extensions Organiq needs. It can discover Isaac Sim from `ISAACSIM_ROOT`, `ISAAC_SIM_ROOT`, `OMNI_USER_PACKAGE_ROOT` or the Omniverse package cache. You can also pass explicit paths:

```powershell
tools\launch_organiq.ps1 -Kit "C:\path\to\isaacsim\kit\kit.exe" -Experience "C:\path\to\isaacsim\apps\isaacsim.exp.full.kit"
```

## dependencies

Organiq keeps heavy medical imaging packages optional so the extension can load before the workstation environment is fully prepared.

Recommended Isaac Python packages:

```powershell
& "$env:ISAACSIM_ROOT\python.bat" -m pip install --prefer-binary --constraint .\constraints\isaac-5.1.txt ".[isaac]"
```

The extension preflight panel reports which packages are present and can install missing packages into the active Isaac Python environment using the same constraints file.

Set `ORGANIQ_MONAI_TIMEOUT_SECONDS` to override the default four-hour MONAI inference timeout. Active UI jobs can be cancelled from the progress panel. Subprocess output is bounded in memory and long-running MONAI status includes elapsed time and host memory telemetry when `psutil` is present.

The mesher derives a signed distance field for each selected label, extracts the zero level set with Flying Edges when VTK is present or marching cubes otherwise, removes degenerate triangles, applies displacement-limited Taubin smoothing, optionally decimates very large soft-tissue meshes and authors vertex normals. It falls back to explicit voxel faces for small masks when the smooth meshing stack has not been installed. USD mesh prims carry `organiq:meanHounsfield` for downstream reconstruction and simulation tuning.

## validation

Run the pure Python checks from the repo root:

```powershell
python -m pytest
python tools\check_extension.py
python tools\package_extension.py --clean
python tools\check_distribution.py --require-archives
```

Run USD export checks inside a Kit process where `pxr` is loaded:

```powershell
python tools\check_usd_export.py
```

Run the Isaac viewport runtime check from the repo root:

```powershell
& "$env:ISAACSIM_ROOT\kit\kit.exe" "$env:ISAACSIM_ROOT\apps\isaacsim.exp.full.kit" --ext-folder (Resolve-Path .\source\extensions) --enable omni.timeline --enable omni.physx.bundle --enable com.chrisvoncsefalvay.organiq --exec (Resolve-Path .\tools\check_kit_runtime.py)
```

Run the full DICOM plus MONAI plus viewport workflow check from the repo root:

```powershell
& "$env:ISAACSIM_ROOT\kit\kit.exe" "$env:ISAACSIM_ROOT\apps\isaacsim.exp.full.kit" --ext-folder (Resolve-Path .\source\extensions) --enable omni.timeline --enable omni.physx.bundle --enable com.chrisvoncsefalvay.organiq --exec (Resolve-Path .\tools\check_dicom_workflow.py)
```

Run the window startup check in the same Kit process shape when you need UI launch evidence:

```powershell
& "$env:ISAACSIM_ROOT\kit\kit.exe" "$env:ISAACSIM_ROOT\apps\isaacsim.exp.full.kit" --ext-folder (Resolve-Path .\source\extensions) --enable omni.timeline --enable omni.physx.bundle --enable com.chrisvoncsefalvay.organiq --exec (Resolve-Path .\tools\check_organiq_window.py)
```

Run the acceptance check after the USD, Kit runtime and DICOM workflow checks have produced reports:

```powershell
python tools\check_acceptance.py
```

## distribution

Organiq is prepared for NVIDIA Omniverse community registry distribution from `https://github.com/chrisvoncsefalvay/organiq`. The repository must be public, tagged with the GitHub topic `omniverse-kit-extension` and released with platform archives generated by:

```powershell
python tools\package_extension.py --clean
```

The archive names follow the NVIDIA community convention:

```text
chrisvoncsefalvay-organiq-linux-x86_64-v0.1.0.zip
chrisvoncsefalvay-organiq-windows-x86_64-v0.1.0.zip
```

Run `python tools\check_distribution.py --require-archives` before tagging a release.

## references

- `docs\physics_defaults.md` records the tissue density, material and PhysX defaults.
- `docs\omniverse_practices.md` records the Omniverse material, scene, physics and extension practices used by the code.
- `docs\distribution.md` records the Omniverse community registry release process, archive names and GitHub topic requirements.

