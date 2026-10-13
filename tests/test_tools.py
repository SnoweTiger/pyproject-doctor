"""Unit tests use temporary projects and never execute external tools."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from pyproject_doctor import tools
from pyproject_doctor.models import AnalysisResult, CommandResult


def test_project_directory_validation(tmp_path):
    assert tools._project_dir(str(tmp_path)) == tmp_path
    with pytest.raises(ValueError, match="does not exist"):
        tools._project_dir(str(tmp_path / "missing"))
    file = tmp_path / "file.py"
    file.touch()
    with pytest.raises(ValueError, match="not a directory"):
        tools._project_dir(str(file))


@pytest.mark.parametrize(
    "name,marker",
    (
        ("uv", "uv.lock"),
        ("poetry", "poetry.lock"),
        ("pipenv", "Pipfile.lock"),
        ("pip", "requirements.txt"),
    ),
)
def test_manager_markers(tmp_path, name, marker):
    file = tmp_path / marker
    file.touch()
    with patch.object(tools.shutil, "which", return_value=None):
        result = tools._package_manager(tmp_path)
    assert result.selected == name
    assert result.versions == {name: None}
    file.unlink()


def test_conflicting_managers_are_not_selected(tmp_path):
    (tmp_path / "uv.lock").touch()
    (tmp_path / "poetry.lock").touch()
    with patch.object(tools.shutil, "which", return_value=None):
        result = tools._package_manager(tmp_path)
    assert sorted(result.detected) == sorted(["uv", "poetry"])
    assert result.selected is None


def test_poetry_configuration_without_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
    with patch.object(tools.shutil, "which", return_value=None):
        assert tools._package_manager(tmp_path).selected == "poetry"


def test_missing_manager(tmp_path):
    result = tools._package_manager(tmp_path)
    assert result.detected == []
    assert result.selected is None


def test_manager_version(tmp_path):
    (tmp_path / "uv.lock").touch()
    with (
        patch.object(tools.shutil, "which", return_value="uv-executable"),
        patch.object(
            tools,
            "_run",
            return_value=CommandResult(status="passed", stdout="uv 0.12.1\n"),
        ) as run,
    ):
        result = tools._package_manager(tmp_path)
    assert result.versions["uv"] == "uv 0.12.1"
    run.assert_called_once_with(["uv-executable", "--version"], tmp_path)


def test_missing_python_preserves_declared_version(tmp_path):
    (tmp_path / ".python-version").write_text("3.12\n", encoding="utf-8")
    with patch.object(tools, "_run") as run:
        result = tools._python_environment(tmp_path)
    assert result.status == "not_found"
    assert result.declared_version == "3.12"
    assert result.version is None
    run.assert_not_called()


@pytest.mark.parametrize(
    "relative",
    (
        ".venv/Scripts/python.exe",
        ".venv/bin/python",
        "venv/Scripts/python.exe",
        "venv/bin/python",
    ),
)
def test_python_candidates(tmp_path, relative):
    interpreter = tmp_path / relative
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.touch()
    with patch.object(
        tools, "_run", return_value=CommandResult(status="passed", stdout="3.12.13\n")
    ):
        result = tools._python_environment(tmp_path)
    assert result.interpreter == str(interpreter)
    assert result.version == "3.12.13"
    assert result.status == "available"
    interpreter.unlink()


def test_python_probe_failure(tmp_path):
    interpreter = tmp_path / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    with patch.object(
        tools, "_run", return_value=CommandResult(status="error", error="cannot start")
    ):
        result = tools._python_environment(tmp_path)
    assert result.status == "unavailable"
    assert result.error == "cannot start"


def test_inspection_marks_python_project(tmp_path):
    (tmp_path / "pyproject.toml").touch()
    with (
        patch.object(tools.shutil, "which", return_value=None),
        patch.object(
            tools,
            "_run",
            return_value=CommandResult(status="passed", stdout="ruff 0.17.0"),
        ),
    ):
        result = tools.inspect_project(str(tmp_path))
    assert result.looks_like_python_project
    assert result.project_files == ["pyproject.toml"]
    assert result.project == str(tmp_path)


def test_ruff_unavailable_skips_checks(tmp_path):
    with patch.object(
        tools, "_run", return_value=CommandResult(status="error", error="missing")
    ) as run:
        result = tools.run_ruff_checks(str(tmp_path))
    assert result.status == "unavailable"
    assert result.lint.status == "unavailable"
    assert result.format.status == "unavailable"
    assert run.call_count == 1


def test_ruff_success_and_commands(tmp_path):
    with patch.object(
        tools,
        "_run",
        side_effect=[
            CommandResult(status="passed", stdout="ruff 0.17.0\n"),
            CommandResult(status="passed", stdout="[]"),
            CommandResult(status="passed", stdout="1 file already formatted"),
        ],
    ) as run:
        result = tools.run_ruff_checks(str(tmp_path))
    assert result.status == "passed"
    assert result.ruff_version == "ruff 0.17.0"
    assert run.call_args_list[1].args[0] == [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--output-format",
        "json",
        ".",
    ]
    assert run.call_args_list[2].args[0] == [
        sys.executable,
        "-m",
        "ruff",
        "format",
        "--check",
        ".",
    ]


def test_ruff_lint_diagnostics(tmp_path):
    diagnostics = [{"code": "F401", "message": "Unused import", "filename": "app.py"}]
    with patch.object(
        tools,
        "_run",
        side_effect=[
            CommandResult(status="passed", stdout="ruff 0.17.0"),
            CommandResult(status="issues_found", stdout=json.dumps(diagnostics)),
            CommandResult(status="passed"),
        ],
    ):
        result = tools.run_ruff_checks(str(tmp_path))
    assert result.status == "fail"
    assert result.lint.diagnostics == diagnostics


def test_ruff_invalid_json_preserves_output(tmp_path):
    with patch.object(
        tools,
        "_run",
        side_effect=[
            CommandResult(status="passed"),
            CommandResult(status="issues_found", stdout="truncated output"),
            CommandResult(status="passed"),
        ],
    ):
        result = tools.run_ruff_checks(str(tmp_path))
    assert result.lint.stdout == "truncated output"
    assert result.lint.diagnostics is None


@pytest.mark.parametrize("status", ("issues_found", "timeout", "error"))
def test_format_failure_and_timeout_fail_overall(tmp_path, status):
    with patch.object(
        tools,
        "_run",
        side_effect=[
            CommandResult(status="passed"),
            CommandResult(status="passed"),
            CommandResult(status=status),
        ],
    ):
        assert tools.run_ruff_checks(str(tmp_path)).status == "fail"


def test_analysis_json_round_trip(tmp_path):
    (tmp_path / "uv.lock").touch()
    with (
        patch.object(tools.shutil, "which", return_value=None),
        patch.object(
            tools, "_run", return_value=CommandResult(status="passed", stdout="[]")
        ),
    ):
        result = tools.analyze_project(str(tmp_path))
    assert isinstance(result, AnalysisResult)
    assert AnalysisResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("code,status", ((0, "passed"), (1, "issues_found")))
def test_exit_codes_and_output_limit(tmp_path, code, status):
    with patch.object(
        tools.subprocess,
        "run",
        return_value=subprocess.CompletedProcess(
            ["tool"], code, "x" * 21000, "details"
        ),
    ) as run:
        result = tools._run(["tool"], Path("."))
        assert result.status == status
        assert result.returncode == code
        assert len(result.stdout) == 20000
        assert result.stderr == "details"
        assert not run.call_args.kwargs.get("shell", False)
        assert run.call_args.kwargs["timeout"] == 60


def test_timeout(tmp_path):
    with patch.object(
        tools.subprocess, "run", side_effect=subprocess.TimeoutExpired("tool", 60)
    ):
        result = tools._run(["tool"], Path("."))
    assert result.status == "timeout"
    assert result.timeout_seconds == 60


def test_os_error(tmp_path):
    with patch.object(tools.subprocess, "run", side_effect=OSError("access denied")):
        result = tools._run(["tool"], Path("."))
    assert result.status == "error"
    assert result.error == "access denied"


def test_invalid_model_status_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        CommandResult(status="unknown")
