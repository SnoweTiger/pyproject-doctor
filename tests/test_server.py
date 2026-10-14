"""Check the MCP interface, including one real stdio client connection."""

import json
import sys
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

import pyproject_doctor
from pyproject_doctor import server
from pyproject_doctor.models import (
    AnalysisResult,
    CommandResult,
    InspectionResult,
    PackageManager,
    PythonEnvironment,
    RuffChecksResult,
)

TOOLS = (
    ("inspect", "inspect_project", InspectionResult),
    ("ruff_check", "run_ruff_checks", RuffChecksResult),
    ("analyze", "analyze_project", AnalysisResult),
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def backends(monkeypatch):
    inspection = InspectionResult(
        project="/projects/example",
        looks_like_python_project=True,
        project_files=["uv.lock"],
        package_manager=PackageManager(
            detected=["uv"], selected="uv", versions={"uv": None}
        ),
        python_environment=PythonEnvironment(
            status="not_found", declared_version="3.12"
        ),
        ruff_version=CommandResult(status="passed", stdout="ruff 0.17.0"),
    )
    checks = RuffChecksResult(
        project=inspection.project,
        ruff_version="ruff 0.17.0",
        lint=CommandResult(
            status="issues_found",
            returncode=1,
            diagnostics=[{"code": "F401", "filename": "app.py"}],
        ),
        format=CommandResult(status="timeout", timeout_seconds=60),
        status="fail",
    )
    results = (inspection, checks, AnalysisResult(inspection=inspection, checks=checks))
    mocks = {}
    for (_, backend, _), result in zip(TOOLS, results, strict=True):
        mocks[backend] = Mock(return_value=result)
        monkeypatch.setattr(server, backend, mocks[backend])
    return mocks


@pytest.mark.anyio
async def test_registered_tools_expose_required_path_and_result_schemas():
    registered = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert set(registered) == {name for name, _, _ in TOOLS}
    for name, _, model in TOOLS:
        tool = registered[name]
        assert tool.input_schema["required"] == ["path"]
        assert set(tool.input_schema["properties"]) == {"path"}
        assert tool.input_schema["properties"]["path"]["type"] == "string"
        assert tool.description
        expected = model.model_json_schema()
        assert tool.output_schema["properties"] == expected["properties"]
        assert tool.output_schema["required"] == expected["required"]


@pytest.mark.anyio
@pytest.mark.parametrize("name,backend,model", TOOLS)
async def test_tool_routes_path_and_serializes_complete_result(
    name, backend, model, backends
):
    path = "projects/example with spaces/проект"
    result = await server.mcp.call_tool(name, {"path": path})

    backends[backend].assert_called_once_with(path)
    for other, mock in backends.items():
        if other != backend:
            mock.assert_not_called()
    expected = backends[backend].return_value
    assert not result.is_error
    assert result.structured_content == expected.model_dump(mode="json")
    assert model.model_validate(result.structured_content) == expected
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert json.loads(result.content[0].text) == result.structured_content


@pytest.mark.anyio
@pytest.mark.parametrize("name,backend,model", TOOLS)
@pytest.mark.parametrize("arguments", ({}, {"path": None}))
async def test_invalid_arguments_are_rejected_before_analysis(
    name, backend, model, arguments, backends
):
    with pytest.raises(ToolError):
        await server.mcp.call_tool(name, arguments)
    for mock in backends.values():
        mock.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("name,backend,model", TOOLS)
async def test_backend_failure_is_not_returned_as_success(
    name, backend, model, backends
):
    failure = OSError("private internal details")
    backends[backend].side_effect = failure
    with pytest.raises(UnexpectedToolError) as caught:
        await server.mcp.call_tool(name, {"path": "project"})
    assert caught.value.__cause__ is failure
    assert "private internal details" not in str(caught.value)


@pytest.mark.parametrize("entrypoint", (server.main, pyproject_doctor.main))
def test_entrypoints_start_stdio_transport(entrypoint, monkeypatch):
    transport = AsyncMock()
    monkeypatch.setattr(server.mcp, "run_stdio_async", transport)
    entrypoint()
    transport.assert_awaited_once_with()


@pytest.mark.anyio
async def test_stdio_client_can_discover_tools_and_recovers_after_invalid_call(
    tmp_path,
):
    source = tmp_path / "app.py"
    original = "import os\nx=1\n"
    source.write_text(original, encoding="utf-8")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pyproject_doctor.server"],
        cwd=tmp_path,
    )
    with anyio.fail_after(20):
        async with (
            stdio_client(parameters) as streams,
            ClientSession(*streams) as session,
        ):
            initialized = await session.initialize()
            assert initialized.server_info.name == "PyProjectDoctor"
            assert initialized.capabilities.tools is not None
            registered = await session.list_tools()
            assert {tool.name for tool in registered.tools} == {
                name for name, _, _ in TOOLS
            }
            for name, _, _ in TOOLS:
                result = await session.call_tool(name, {})
                assert result.is_error
                assert result.structured_content is None
                assert "path" in result.content[0].text
            await session.send_ping()
            assert len((await session.list_tools()).tools) == 3
            result = await session.call_tool("analyze", {"path": str(tmp_path)})
            assert not result.is_error
            report = AnalysisResult.model_validate(result.structured_content)
            assert report.inspection.project == str(tmp_path.resolve())
            assert report.checks.project == report.inspection.project
            assert report.checks.status == "fail"
            assert report.checks.lint.status == "issues_found"
            assert any(
                item["code"] == "F401" for item in report.checks.lint.diagnostics
            )
            assert report.checks.format.status == "issues_found"
            assert source.read_text(encoding="utf-8") == original
