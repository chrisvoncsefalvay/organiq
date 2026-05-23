from __future__ import annotations

import importlib.metadata
import importlib.util
import importlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import DEFAULT_MODEL_ROOT, ensure_work_roots


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[6]


CONSTRAINTS_FILE = _repo_root() / "constraints" / "isaac-5.1.txt"


@dataclass(frozen=True)
class Dependency:
    package: str
    import_name: str
    reason: str
    required_for: str


@dataclass(frozen=True)
class DependencyStatus:
    dependency: Dependency
    available: bool


@dataclass(frozen=True)
class InstallReport:
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    installed: tuple[str, ...]
    failed: tuple[str, ...]


DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency("numpy>=1.26.4,<2", "numpy", "volume arrays", "DICOM loading and meshing"),
    Dependency("pydicom>=2.4,<4", "pydicom", "DICOM parsing", "DICOM loading"),
    Dependency("nibabel>=5.2,<6", "nibabel", "NIfTI exchange", "MONAI bundle inference"),
    Dependency("scikit-image>=0.22,<0.26", "skimage", "zero level set extraction", "smooth meshing"),
    Dependency("scipy>=1.11,<1.17", "scipy", "scikit-image support", "smooth meshing"),
    Dependency("imageio>=2.31,<3", "imageio", "scikit-image image IO support", "smooth meshing"),
    Dependency("monai[fire]>=1.3,<1.6", "monai", "MONAI bundle runtime", "MONAI segmentation"),
    Dependency("pytorch-ignite>=0.5,<0.6", "ignite", "MONAI engine runtime", "MONAI segmentation"),
    Dependency("torch>=2.2,<2.8", "torch", "model execution", "MONAI segmentation"),
)


def package_available(import_name: str, package_spec: str | None = None) -> bool:
    if importlib.util.find_spec(import_name) is None:
        return False
    if package_spec is None:
        return True
    try:
        from packaging.requirements import Requirement
        from packaging.version import Version

        requirement = Requirement(package_spec)
        version = Version(importlib.metadata.version(requirement.name))
        return version in requirement.specifier
    except Exception:
        return True


def dependency_status() -> tuple[DependencyStatus, ...]:
    return tuple(DependencyStatus(dep, package_available(dep.import_name, dep.package)) for dep in DEPENDENCIES)


def missing_packages() -> tuple[str, ...]:
    return tuple(status.dependency.package for status in dependency_status() if not status.available)


def resolve_python_executable(python_executable: str | None = None) -> str:
    if python_executable:
        return python_executable

    env_python = os.environ.get("ORGANIQ_PYTHON")
    if env_python:
        return env_python

    executable = Path(sys.executable)
    if executable.name.lower() in {"python.exe", "python"}:
        return str(executable)

    for parent in (executable.parent, *executable.parents):
        candidate = parent / "python.bat"
        if candidate.exists():
            return str(candidate)

    for isaac_python in isaac_python_candidates("python.bat"):
        if isaac_python.exists():
            return str(isaac_python)

    return sys.executable


def isaac_python_candidates(relative_path: str) -> tuple[Path, ...]:
    roots: list[Path] = []
    for variable in ("ISAACSIM_ROOT", "ISAAC_SIM_ROOT"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))

    package_root_value = os.environ.get("OMNI_USER_PACKAGE_ROOT")
    package_roots = [Path(package_root_value)] if package_root_value else []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        package_roots.append(Path(local_app_data) / "ov" / "pkg")

    for package_root in package_roots:
        if not package_root.exists():
            continue
        roots.extend(sorted(package_root.glob("isaac-sim*"), reverse=True))

    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidate = root / relative_path
        key = str(candidate).lower()
        if key not in seen:
            candidates.append(candidate)
            seen.add(key)
    return tuple(candidates)


def install_missing_packages(python_executable: str | None = None) -> InstallReport | None:
    missing = missing_packages()
    if not missing:
        return None
    ensure_work_roots()
    executable = resolve_python_executable(python_executable)
    pip_cache = DEFAULT_MODEL_ROOT.parent / "pip-cache"
    pip_cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("PIP_CACHE_DIR", str(pip_cache))
    command = [executable, "-m", "pip", "install", "--prefer-binary"]
    if CONSTRAINTS_FILE.exists():
        command.extend(["--constraint", str(CONSTRAINTS_FILE)])
    command.extend(missing)
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    importlib.invalidate_caches()
    now_missing = missing_packages()
    installed = tuple(package for package in missing if package not in now_missing)
    failed = tuple(package for package in missing if package in now_missing)
    return InstallReport(tuple(command), result.returncode, result.stdout, result.stderr, installed, failed)


def model_cache_root(path: str | Path | None = None) -> Path:
    root = Path(path) if path else DEFAULT_MODEL_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root
