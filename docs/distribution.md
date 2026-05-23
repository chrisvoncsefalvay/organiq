# distribution

Organiq is distributed as a public Omniverse Kit community extension from the GitHub repository declared in `config/extension.toml`.

The distributable extension lives at `source/extensions/com.chrisvoncsefalvay.organiq`. Release archives contain that extension folder only, with this shape:

```text
com.chrisvoncsefalvay.organiq/
  config/extension.toml
  docs/README.md
  docs/CHANGELOG.md
  data/icon.png
  data/preview.png
  com/chrisvoncsefalvay/organiq/
```

## registry requirements

Before publishing, the GitHub repository must be public and must have the topic `omniverse-kit-extension`.

```powershell
& "C:\Program Files\GitHub CLI\gh.exe" repo edit chrisvoncsefalvay/organiq --add-topic omniverse-kit-extension
```

The extension manifest must keep these fields current:

```toml
[package]
name = "com.chrisvoncsefalvay.organiq"
version = "0.1.0"
title = "Organiq"
description = "CT DICOM to MONAI segmentation, USD meshing and Isaac physics authoring"
repository = "https://github.com/chrisvoncsefalvay/organiq"
keywords = ["omniverse", "kit", "extension", "isaac", "dicom", "ct", "monai", "usd", "physics"]

[package.target]
platform = ["linux-x86_64", "windows-x86_64"]
python = ["*"]
kit = ["107.3.3"]
```

The extension name must not start with `omni`.

## local checks

Run these from the repository root before tagging a release:

```powershell
python -m pytest
python tools\check_extension.py
python tools\package_extension.py --clean
python tools\check_distribution.py --require-archives
```

When Isaac Sim is available, also run:

```powershell
python tools\check_usd_export.py
python tools\check_acceptance.py
```

The distribution check writes `build\organiq_distribution_check.json` and validates manifest metadata, version consistency, required policy files, release workflow coverage, generated archive names and absence of generated medical artefacts.

## release archive names

GitHub release assets must use the NVIDIA community archive naming convention:

```text
chrisvoncsefalvay-organiq-linux-x86_64-v0.1.0.zip
chrisvoncsefalvay-organiq-windows-x86_64-v0.1.0.zip
```

The release workflow builds those archives from `source/extensions/com.chrisvoncsefalvay.organiq` and uploads them to the tag release.

## publishing

1. Commit the distributable root.
2. Push the repository to `https://github.com/chrisvoncsefalvay/organiq`.
3. Add the `omniverse-kit-extension` topic.
4. Tag the release with the manifest version.

```powershell
git tag v0.1.0
git push origin main v0.1.0
```

The community registry crawler discovers public repositories with that topic and publishes tagged releases after its periodic run.

## excluded artefacts

Do not commit patient data, generated USD, MONAI checkpoints, NIfTI volumes, DICOM files, local cache folders or release archives. Keep those in ignored paths such as `build`, `dist`, `outputs`, `work` and `models`.
