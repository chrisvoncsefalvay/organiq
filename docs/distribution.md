# Distribution

Organiq is distributed as a public Omniverse Kit community extension from the GitHub repository declared in `config/extension.toml`.

The distributable extension lives at `source/extensions/com.chrisvoncsefalvay.organiq`. The release archive contains the contents of that extension folder at the archive root, with this shape:

```text
config/extension.toml
docs/README.md
docs/CHANGELOG.md
data/icon.png
data/preview.png
com/chrisvoncsefalvay/organiq/
```

This root layout is required for Extension Manager zip import. Isaac unpacks an imported archive into a folder named after the archive, then looks for `config/extension.toml` inside that unpacked folder.

## Registry requirements

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
description = "Sim Ready anatomy from DICOM volumes"
repository = "https://github.com/chrisvoncsefalvay/organiq"
keywords = ["omniverse", "kit", "extension", "isaac", "dicom", "ct", "monai", "usd", "physics"]

[package.target]
platform = ["linux-x86_64", "windows-x86_64"]
python = ["*"]
kit = ["107.3.3"]
```

The extension name must not start with `omni`.

## Local checks

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

The distribution check writes `build\organiq_distribution_check.json` and validates manifest metadata, version consistency, required policy files, release workflow coverage, generated archive naming and absence of generated medical artefacts.

## Release archive name

GitHub release assets must use a Kit-importable package id as the archive basename. Extension Manager imports the zip into a folder named after the archive, and Kit uses that folder name during extension discovery.

```text
com.chrisvoncsefalvay.organiq-0.1.0.zip
```

The release workflow builds this archive from `source/extensions/com.chrisvoncsefalvay.organiq` and uploads it to the tag release.

For local development, add `source/extensions` as the extension search path. Adding the repository root makes Isaac scan `.git`, `build`, `dist`, `docs`, `tests` and other non-extension directories.

If a local import used an older archive name, close Isaac and run:

```powershell
.\tools\repair_isaac_extension_import.ps1
```

## Publishing

1. Commit the distributable root.
2. Push the repository to `https://github.com/chrisvoncsefalvay/organiq`.
3. Add the `omniverse-kit-extension` topic.
4. Tag the release with the manifest version.

```powershell
git tag v0.1.0
git push origin main v0.1.0
```

The community registry crawler discovers public repositories with that topic and publishes tagged releases after its periodic run.
