# Contributing

Keep the repository release-ready. Changes should preserve the extension structure under `source/extensions/com.chrisvoncsefalvay.organiq` and keep `config/extension.toml`, `pyproject.toml` and the visible extension version in sync.

Before opening a pull request, run:

```powershell
python -m pytest
python tools\check_extension.py
python tools\package_extension.py --clean
python tools\check_distribution.py --require-archives
```

When Isaac Sim is available, add the Kit runtime, USD export and acceptance checks from `README.md`.

Do not commit patient data, generated medical volumes, generated USD files, MONAI checkpoints, release archives, local caches or workstation-specific paths. Use synthetic test arrays or de-identified fixtures only when a test needs imaging-shaped data.

Keep documentation written as final product documentation. Avoid progress narration, placeholder language and implementation diary entries.
