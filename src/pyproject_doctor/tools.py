"""Project inspection and Ruff checks exposed through the MCP server."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pyproject_doctor.models import (
    AnalysisResult,
    CommandResult,
    InspectionResult,
    PackageManager,
    PythonEnvironment,
    RuffChecksResult,
)

_TIMEOUT_SECONDS = 60


def _project_dir(path: str) -> Path:
    """Validate and resolve a local project directory."""
    project = Path(path).expanduser().resolve()

    if not project.exists():
        raise ValueError(f"Project path does not exist: {project}")

    if not project.is_dir():
        raise ValueError(f"Project path is not a directory: {project}")

    return project


def _run(command: list[str], cwd: Path) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )

    except subprocess.TimeoutExpired:
        return CommandResult(
            status="timeout",
            command=command,
            timeout_seconds=_TIMEOUT_SECONDS,
        )

    except OSError as exc:
        return CommandResult(
            status="error",
            command=command,
            error=str(exc),
        )

    return CommandResult(
        status="passed" if result.returncode == 0 else "issues_found",
        command=command,
        returncode=result.returncode,
        stdout=result.stdout[-20_000:],
        stderr=result.stderr[-20_000:],
    )


def _python_environment(project: Path) -> PythonEnvironment:
    """Report the project virtualenv interpreter, if one is present."""
    candidates = [
        project / ".venv" / "Scripts" / "python.exe",
        project / ".venv" / "bin" / "python",
        project / "venv" / "Scripts" / "python.exe",
        project / "venv" / "bin" / "python",
    ]
    interpreter = next(
        (candidate for candidate in candidates if candidate.is_file()), None
    )
    declared_version = None
    version_file = project / ".python-version"
    if version_file.is_file():
        declared_version = version_file.read_text(encoding="utf-8").strip()

    result = PythonEnvironment(
        interpreter=str(interpreter) if interpreter else None,
        declared_version=declared_version,
        status="not_found",
    )
    if interpreter:
        probe = _run(
            [
                str(interpreter),
                "-c",
                "import platform; print(platform.python_version())",
            ],
            project,
        )
        if probe.status == "passed":
            result.status = "available"
            result.version = probe.stdout.strip()
        else:
            result.status = "unavailable"
            result.error = probe.stderr or probe.error
    return result


def _package_manager(project: Path) -> PackageManager:
    markers = {
        "uv": ["uv.lock"],
        "poetry": ["poetry.lock"],
        "pipenv": ["Pipfile", "Pipfile.lock"],
        "pip": ["requirements.txt", "requirements-dev.txt", "requirements"],
    }
    detected = [
        name
        for name, files in markers.items()
        if any((project / file).exists() for file in files)
    ]
    if not detected and (project / "pyproject.toml").is_file():
        try:
            content = (project / "pyproject.toml").read_text(encoding="utf-8")
        except OSError:
            content = ""
        if re.search(r"(?m)^\[tool\.poetry(?:\.|\])", content):
            detected.append("poetry")
        else:
            detected.append("pip")

    # Return every signal when lock/config files disagree; do not guess silently.
    versions: dict[str, str | None] = {}
    for name in detected:
        executable = shutil.which(name)
        if executable:
            probe = _run([executable, "--version"], project)
            versions[name] = probe.stdout.strip() or probe.stderr.strip() or None
        else:
            versions[name] = None
    return PackageManager(
        detected=detected,
        selected=detected[0] if len(detected) == 1 else None,
        versions=versions,
    )


def inspect_project(path: str) -> InspectionResult:
    """Inspect one local Python project without running its code."""
    project = _project_dir(path)
    markers = [
        name
        for name in (
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            "Pipfile",
        )
        if (project / name).is_file()
    ]
    return InspectionResult(
        project=str(project),
        looks_like_python_project=bool(markers),
        project_files=markers,
        package_manager=_package_manager(project),
        python_environment=_python_environment(project),
        ruff_version=_run([sys.executable, "-m", "ruff", "--version"], project),
    )


def run_ruff_checks(path: str) -> RuffChecksResult:
    """Run Ruff lint and formatting checks for one local project."""
    project = _project_dir(path)

    # Check Ruff exist in path and it version
    version = _run([sys.executable, "-m", "ruff", "--version"], project)
    if version.status != "passed":
        return RuffChecksResult(
            project=str(project),
            ruff_version=version,
            lint=CommandResult(status="unavailable"),
            format=CommandResult(status="unavailable"),
            status="unavailable",
        )

    # Run Ruff linter
    lint = _run(
        [sys.executable, "-m", "ruff", "check", "--output-format", "json", "."], project
    )
    if lint.status == "issues_found":
        try:
            lint.diagnostics = json.loads(lint.stdout)
        except json.JSONDecodeError:
            pass

    # Run Ruff formatter check
    format = _run([sys.executable, "-m", "ruff", "format", "--check", "."], project)

    return RuffChecksResult(
        project=str(project),
        ruff_version=version.stdout.strip(),
        lint=lint,
        format=format,
        status="passed"
        if lint.status == "passed" and format.status == "passed"
        else "fail",
    )


def analyze_project(path: str) -> AnalysisResult:
    """Inspect a project and run the bundled Ruff checks."""

    inspection = inspect_project(path)
    checks = run_ruff_checks(path)

    return AnalysisResult(inspection=inspection, checks=checks)
