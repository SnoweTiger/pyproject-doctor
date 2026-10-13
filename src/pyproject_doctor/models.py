from typing import Any, Literal

from pydantic import BaseModel


class CommandResult(BaseModel):
    status: Literal["passed", "issues_found", "error", "timeout", "unavailable"]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    timeout_seconds: int | None = None
    diagnostics: list[dict[str, Any]] | None = None


class PythonEnvironment(BaseModel):
    interpreter: str | None = None
    declared_version: str | None = None
    version: str | None = None
    status: Literal["available", "unavailable", "not_found"]
    error: str | None = None


class PackageManager(BaseModel):
    detected: list[str]
    selected: str | None
    versions: dict[str, str | None]


class InspectionResult(BaseModel):
    project: str
    looks_like_python_project: bool
    project_files: list[str]
    package_manager: PackageManager
    python_environment: PythonEnvironment
    ruff_version: CommandResult


class RuffChecksResult(BaseModel):
    project: str
    ruff_version: str | CommandResult
    lint: CommandResult
    format: CommandResult
    status: Literal["passed", "fail", "unavailable"]


class AnalysisResult(BaseModel):
    inspection: InspectionResult
    checks: RuffChecksResult
