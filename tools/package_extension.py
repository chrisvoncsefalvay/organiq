from __future__ import annotations

import argparse
import shutil
import tomllib
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_NAME = "com.chrisvoncsefalvay.organiq"
EXTENSION_ROOT = REPO_ROOT / "source" / "extensions" / EXTENSION_NAME
MANIFEST_PATH = EXTENSION_ROOT / "config" / "extension.toml"
DIST_ROOT = REPO_ROOT / "dist"
EXCLUDED_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Package the Organiq Kit extension for GitHub release upload.")
    parser.add_argument("--clean", action="store_true", help="Remove dist before writing the archive.")
    args = parser.parse_args()

    if args.clean and DIST_ROOT.exists():
        _clean_dist()
    DIST_ROOT.mkdir(parents=True, exist_ok=True)

    metadata = _manifest_metadata()
    archive = DIST_ROOT / archive_name(metadata)
    _write_archive(archive)
    archives = [archive]

    for archive in archives:
        print(f"archive={archive.relative_to(REPO_ROOT)}")
    return 0


def _clean_dist() -> None:
    for child in DIST_ROOT.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _manifest_metadata() -> dict[str, str]:
    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    package = manifest["package"]
    return {
        "name": str(package["name"]),
        "version": str(package["version"]),
    }


def archive_name(metadata: dict[str, str]) -> str:
    return f"{metadata['name']}-{metadata['version']}.zip"


def _write_archive(archive_path: Path) -> None:
    files = [path for path in sorted(EXTENSION_ROOT.rglob("*")) if _include_file(path)]
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in files:
            relative = path.relative_to(EXTENSION_ROOT)
            archive_name = relative
            info = zipfile.ZipInfo(str(archive_name).replace("\\", "/"), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())


def _include_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if EXCLUDED_DIR_NAMES.intersection(path.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
