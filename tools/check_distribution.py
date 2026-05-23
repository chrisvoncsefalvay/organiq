from __future__ import annotations

import argparse
import json
import re
import struct
import tomllib
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_NAME = "com.chrisvoncsefalvay.organiq"
EXTENSION_ROOT = REPO_ROOT / "source" / "extensions" / EXTENSION_NAME
MANIFEST_PATH = EXTENSION_ROOT / "config" / "extension.toml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
BUILD_ROOT = REPO_ROOT / "build"
DIST_ROOT = REPO_ROOT / "dist"
PLATFORMS = ("linux-x86_64", "windows-x86_64")
REQUIRED_PACKAGE_FIELDS = (
    "name",
    "version",
    "title",
    "description",
    "repository",
    "keywords",
    "changelog",
    "readme",
    "preview_image",
    "icon",
)
REQUIRED_KEYWORDS = {"omniverse", "kit", "extension"}
FORBIDDEN_DISTRIBUTABLE_SUFFIXES = {
    ".dcm",
    ".dicom",
    ".nii",
    ".nrrd",
    ".mha",
    ".mhd",
    ".npz",
    ".usd",
    ".usda",
    ".usdc",
    ".pt",
    ".pth",
    ".ckpt",
    ".onnx",
    ".safetensors",
}
IGNORED_DIR_NAMES = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist", "__pycache__"}
LOCAL_PATH_PATTERNS = (
    "C:" + "\\Users\\" + "chris",
    "Users/" + "chris",
    "D:" + "\\isaac" + "sim",
    "D:" + "/isaac" + "sim",
    "D:" + "\\data",
    "D:" + "/data",
    "Z" + ":",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether the repo is ready for Omniverse community distribution.")
    parser.add_argument("--require-archives", action="store_true", help="Require release archives to exist in dist.")
    args = parser.parse_args()

    checks: list[dict[str, str]] = []

    manifest = _read_toml(MANIFEST_PATH)
    pyproject = _read_toml(PYPROJECT_PATH)
    package = _package_table(manifest)

    _record(checks, "extension manifest", bool(package), _path_evidence(MANIFEST_PATH))
    _check_manifest_metadata(checks, package)
    _check_pyproject(checks, package, pyproject)
    _check_extension_structure(checks, package)
    _check_release_files(checks)
    _check_repo_hygiene(checks)
    _check_archives(checks, package, require_archives=args.require_archives)

    ok = all(check["status"] == "ok" for check in checks)
    report = {
        "status": "ok" if ok else "failed",
        "repo_root": str(REPO_ROOT),
        "checks": checks,
    }
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_ROOT / "organiq_distribution_check.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"report={report_path}")
    if ok:
        print("organiq_distribution_check=ok")
        return 0

    print("organiq_distribution_check=failed")
    for check in checks:
        if check["status"] != "ok":
            print(f"failed={check['name']}: {check['evidence']}")
    return 1


def _check_manifest_metadata(checks: list[dict[str, str]], package: dict[str, Any]) -> None:
    for field in REQUIRED_PACKAGE_FIELDS:
        value = package.get(field)
        _record(checks, f"manifest package.{field}", _has_value(value), str(value))

    name = str(package.get("name", ""))
    version = str(package.get("version", ""))
    repository = str(package.get("repository", ""))
    keywords = {str(keyword).lower() for keyword in package.get("keywords", []) if isinstance(keyword, str)}
    target = package.get("target", {}) if isinstance(package.get("target"), dict) else {}
    target_platforms = {str(platform) for platform in target.get("platform", []) if isinstance(platform, str)}
    target_kit = {str(kit) for kit in target.get("kit", []) if isinstance(kit, str)}

    _record(checks, "manifest name matches extension folder", name == EXTENSION_NAME, name)
    _record(checks, "manifest name avoids omni prefix", not name.startswith("omni"), name)
    _record(checks, "manifest version is semver", bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), version)
    _record(checks, "manifest repository is GitHub", _is_github_repository(repository), repository)
    _record(
        checks,
        "manifest registry keywords",
        REQUIRED_KEYWORDS.issubset(keywords),
        ",".join(sorted(keywords)),
    )
    _record(
        checks,
        "manifest target platforms",
        {"linux-x86_64", "windows-x86_64"}.issubset(target_platforms),
        ",".join(sorted(target_platforms)),
    )
    _record(checks, "manifest target kit", "107.3.3" in target_kit, ",".join(sorted(target_kit)))


def _check_pyproject(checks: list[dict[str, str]], package: dict[str, Any], pyproject: dict[str, Any]) -> None:
    project = pyproject.get("project", {}) if isinstance(pyproject, dict) else {}
    build_system = pyproject.get("build-system", {}) if isinstance(pyproject, dict) else {}
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    setuptools = pyproject.get("tool", {}).get("setuptools", {}) if isinstance(pyproject.get("tool"), dict) else {}

    _record(checks, "pyproject build backend", bool(build_system.get("build-backend")), str(build_system.get("build-backend")))
    _record(checks, "pyproject version matches manifest", project.get("version") == package.get("version"), str(project.get("version")))
    _record(checks, "pyproject readme", project.get("readme") == "README.md", str(project.get("readme")))
    _record(checks, "pyproject licence", bool(project.get("license")), str(project.get("license")))
    _record(checks, "pyproject dev extra", "dev" in optional, ",".join(sorted(optional.keys())))
    _record(checks, "pyproject isaac extra", "isaac" in optional, ",".join(sorted(optional.keys())))
    _record(
        checks,
        "pyproject package-dir points at extension",
        Path(str(setuptools.get("package-dir", {}).get(""))) == Path("source/extensions") / EXTENSION_NAME,
        str(setuptools.get("package-dir")),
    )


def _check_extension_structure(checks: list[dict[str, str]], package: dict[str, Any]) -> None:
    expected = (
        EXTENSION_ROOT / "config" / "extension.toml",
        EXTENSION_ROOT / "docs" / "README.md",
        EXTENSION_ROOT / "docs" / "CHANGELOG.md",
        EXTENSION_ROOT / "data" / "icon.png",
        EXTENSION_ROOT / "data" / "preview.png",
        EXTENSION_ROOT / "com" / "chrisvoncsefalvay" / "organiq" / "__init__.py",
    )
    for path in expected:
        _record(checks, f"extension file {path.relative_to(REPO_ROOT)}", path.exists(), _path_evidence(path))

    for field in ("readme", "changelog", "preview_image", "icon"):
        value = str(package.get(field, ""))
        path = EXTENSION_ROOT / value
        _record(checks, f"manifest {field} target exists", bool(value) and path.exists(), str(path.relative_to(REPO_ROOT)))

    icon_size = _png_dimensions(EXTENSION_ROOT / "data" / "icon.png")
    preview_size = _png_dimensions(EXTENSION_ROOT / "data" / "preview.png")
    _record(checks, "icon dimensions", icon_size == (256, 256), str(icon_size))
    _record(checks, "preview dimensions", preview_size == (1200, 675), str(preview_size))


def _check_release_files(checks: list[dict[str, str]]) -> None:
    required = (
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "CONTRIBUTING.md",
        REPO_ROOT / "SECURITY.md",
        REPO_ROOT / "docs" / "distribution.md",
        REPO_ROOT / ".github" / "workflows" / "ci.yml",
        REPO_ROOT / ".github" / "workflows" / "release.yml",
        REPO_ROOT / "tools" / "package_extension.py",
    )
    for path in required:
        _record(checks, f"release file {path.relative_to(REPO_ROOT)}", path.exists(), _path_evidence(path))

    distribution_text = _read_text(REPO_ROOT / "docs" / "distribution.md")
    release_workflow = _read_text(REPO_ROOT / ".github" / "workflows" / "release.yml")
    ci_workflow = _read_text(REPO_ROOT / ".github" / "workflows" / "ci.yml")

    _record(checks, "distribution docs mention GitHub topic", "omniverse-kit-extension" in distribution_text, "docs/distribution.md")
    _record(checks, "distribution docs mention release archives", "windows-x86_64" in distribution_text and "linux-x86_64" in distribution_text, "docs/distribution.md")
    _record(checks, "release workflow tags", "tags:" in release_workflow and "v*" in release_workflow, ".github/workflows/release.yml")
    _record(checks, "release workflow uploads archives", "gh release upload" in release_workflow, ".github/workflows/release.yml")
    _record(checks, "ci workflow runs distribution check", "tools/check_distribution.py" in ci_workflow, ".github/workflows/ci.yml")
    _record(checks, "ci workflow runs tests", "python -m pytest" in ci_workflow, ".github/workflows/ci.yml")


def _check_repo_hygiene(checks: list[dict[str, str]]) -> None:
    forbidden_files = []
    local_path_hits = []
    for path in _repo_files():
        suffixes = [suffix.lower() for suffix in path.suffixes]
        if any(suffix in FORBIDDEN_DISTRIBUTABLE_SUFFIXES for suffix in suffixes):
            forbidden_files.append(str(path.relative_to(REPO_ROOT)))
        if path.suffix.lower() in {".pyc", ".pyo"}:
            forbidden_files.append(str(path.relative_to(REPO_ROOT)))
        if path.suffix.lower() in {".py", ".md", ".toml", ".yml", ".yaml", ".txt", ".ps1"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in LOCAL_PATH_PATTERNS:
                if pattern in text:
                    local_path_hits.append(f"{path.relative_to(REPO_ROOT)} contains {pattern!r}")

    gitignore = _read_text(REPO_ROOT / ".gitignore")
    expected_ignores = ("dist/", "build/", "*.dcm", "*.nii", "*.pt", "*.usd", "models/")

    _record(checks, "no generated or patient data files in repo", not forbidden_files, ",".join(forbidden_files[:8]))
    _record(checks, "no local workstation paths in distributable text", not local_path_hits, "; ".join(local_path_hits[:8]))
    _record(checks, "gitignore excludes release by-products and medical data", all(item in gitignore for item in expected_ignores), ".gitignore")


def _check_archives(checks: list[dict[str, str]], package: dict[str, Any], require_archives: bool) -> None:
    if not require_archives:
        _record(checks, "release archives", True, "not required for this run")
        return

    repository = str(package.get("repository", ""))
    namespace, repo = _github_namespace_and_repo(repository)
    tag = f"v{package.get('version')}"
    expected = [DIST_ROOT / f"{namespace}-{repo}-{platform}-{tag}.zip" for platform in PLATFORMS]

    for archive in expected:
        _record(checks, f"archive {archive.name}", archive.exists(), _path_evidence(archive))
        if archive.exists():
            _check_archive_contents(checks, archive)


def _check_archive_contents(checks: list[dict[str, str]], archive: Path) -> None:
    required = {
        f"{EXTENSION_NAME}/config/extension.toml",
        f"{EXTENSION_NAME}/docs/README.md",
        f"{EXTENSION_NAME}/docs/CHANGELOG.md",
        f"{EXTENSION_NAME}/data/icon.png",
        f"{EXTENSION_NAME}/data/preview.png",
        f"{EXTENSION_NAME}/com/chrisvoncsefalvay/organiq/__init__.py",
    }
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
    forbidden_prefixes = (f"{EXTENSION_NAME}/tests/", f"{EXTENSION_NAME}/tools/")
    forbidden_suffixes = (".pyc", ".pyo", ".dcm", ".dicom", ".nii", ".nii.gz", ".usd", ".usda", ".usdc", ".pt", ".pth")
    forbidden = [
        name
        for name in names
        if name.startswith(forbidden_prefixes) or any(name.lower().endswith(suffix) for suffix in forbidden_suffixes)
    ]

    _record(checks, f"archive contents {archive.name}", required.issubset(names), f"entries={len(names)}")
    _record(checks, f"archive excludes generated data {archive.name}", not forbidden, ",".join(forbidden[:8]))


def _repo_files() -> list[Path]:
    files = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if IGNORED_DIR_NAMES.intersection(path.relative_to(REPO_ROOT).parts):
            continue
        files.append(path)
    return files


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", data[16:24])


def _package_table(manifest: dict[str, Any]) -> dict[str, Any]:
    package = manifest.get("package", {})
    return package if isinstance(package, dict) else {}


def _is_github_repository(repository_url: str) -> bool:
    parsed = urlparse(repository_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "github.com" and len(parts) >= 2


def _github_namespace_and_repo(repository_url: str) -> tuple[str, str]:
    parsed = urlparse(repository_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return "", ""
    repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    return parts[0], repo


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _path_evidence(path: Path) -> str:
    if not path.exists():
        return "missing"
    return str(path.relative_to(REPO_ROOT))


def _record(checks: list[dict[str, str]], name: str, condition: bool, evidence: str) -> None:
    checks.append(
        {
            "name": name,
            "status": "ok" if condition else "failed",
            "evidence": evidence,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
