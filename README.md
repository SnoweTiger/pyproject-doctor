# PyProject Doctor

[![Lint and format](https://github.com/SnoweTiger/pyproject-doctor/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/SnoweTiger/pyproject-doctor/actions/workflows/lint.yml)
[![Tests](https://github.com/SnoweTiger/pyproject-doctor/actions/workflows/tests.yml/badge.svg?branch=main&event=workflow_run)](https://github.com/SnoweTiger/pyproject-doctor/actions/workflows/tests.yml)


A local MCP server for inspecting Python projects and checking their code with Ruff.

Give your MCP client the path to one project. PyProject Doctor detects package manager signals, reports the project's local Python environment, and runs lint and formatting checks. Results are returned as structured Pydantic models.

## Features

- Detects uv, Poetry, Pipenv, and pip project files.
- Reports the version of detected package managers available on the server's `PATH`.
- Finds a local `.venv` or `venv` and reports its actual Python version.
- Reads the declared version from `.python-version`, when present.
- Runs `ruff check` and `ruff format --check` using Ruff installed with the server.
- Uses the project's Ruff configuration, or Ruff defaults when no configuration is found.
- Reports failures, missing tools, and command timeouts.

Checks do not apply fixes or reformat source files. The server does not install dependencies in the analyzed project.

## Requirements

- Python 3.12 or newer for the server.
- A local MCP client with stdio support.
- Access to the directory being analyzed.

Ruff, Pydantic, and MCP SDK 2.x are included as package dependencies. The analyzed project does not need its own Ruff installation.

## Install from source

Clone the repository and enter its directory. Replace `<repository-url>` with the repository's Git URL:

```sh
git clone <repository-url> pyproject-doctor
cd pyproject-doctor
```

From the repository directory, install the server as an isolated tool:

```sh
uv tool install .
```

For development, use an editable installation:

```sh
uv tool install --editable .
```

An editable installation uses the source files in your checkout. Restart the MCP process after changing code. Reinstall after changing dependencies or entry points, and keep the checkout at its original location.

Make sure the tool executable directory is on `PATH`. If needed, run `uv tool update-shell`, then restart your terminal and MCP client.

You can also install into an existing Python environment:

```sh
python -m pip install .
```

The installed command is:

```sh
pyproject-doctor
```

This starts a stdio MCP server that waits for a client on stdin. It is not an interactive command-line project checker. The MCP client normally launches and manages this process.

## Connect to an MCP client

### OpenCode

Add the server to the `mcp` section of your `opencode.json` or `opencode.jsonc`:

```json
{
  "mcp": {
    "pyproject-doctor": {
      "type": "local",
      "command": ["pyproject-doctor"],
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

### Codex

Add this section to your user configuration at `~/.codex/config.toml`:

```toml
[mcp_servers.pyproject_doctor]
command = "pyproject-doctor"
enabled = true
```

On Windows, the default user configuration location is `%USERPROFILE%\.codex\config.toml`.

Restart your client after configuring the server. If the command cannot be found, check the client's `PATH` or use the full path to the installed executable.

### Run directly from a checkout

During development, you can use `uv run` instead of a tool installation:

```sh
uv sync
uv run pyproject-doctor
```

For a client launched outside the repository, use `uv` as the executable and pass these arguments:

```text
run --directory /absolute/path/to/pyproject-doctor pyproject-doctor
```

## Tools

All tools accept a single `path` argument pointing to an existing local directory. Use an absolute path so it resolves consistently across clients.

| Tool | Result | Purpose |
| --- | --- | --- |
| `inspect` | `InspectionResult` | Inspect project files, package manager signals, Python environment, and server Ruff availability. |
| `ruff_check` | `RuffChecksResult` | Run lint and formatting checks. |
| `analyze` | `AnalysisResult` | Return inspection results and run both Ruff checks. |

Example arguments for `analyze`:

```json
{"path": "/path/to/python-project"}
```

On Windows:

```json
{"path": "F:\\Projects\\example-project"}
```

Example client prompt:

> Use pyproject-doctor to analyze /path/to/python-project. Report the package manager, Python version, and Ruff check results.

## Results

`AnalysisResult` contains two nested reports:

- `inspection`: resolved project path, recognized project files, detected package managers and their versions, Python environment, and the Ruff version probe.
- `checks`: Ruff version, lint result, formatting result, and overall status.

Each command result includes a status, exit code, captured output, and optional error or timeout details. Lint violations include parsed Ruff JSON diagnostics when parsing succeeds.

Command statuses are `passed`, `issues_found`, `error`, `timeout`, and `unavailable`. The overall Ruff status is `passed`, `fail`, or `unavailable`.

## Detection and execution details

| Manager | Detection signals |
| --- | --- |
| uv | `uv.lock` |
| Poetry | `poetry.lock`, or `[tool.poetry]` configuration when no file markers are found |
| Pipenv | `Pipfile` or `Pipfile.lock` |
| pip | `requirements.txt`, `requirements-dev.txt`, or a `requirements` path |

When no manager file markers are found, a `pyproject.toml` without `[tool.poetry]` currently falls back to pip. This is a heuristic, not proof of how the project was installed. If multiple managers are detected, all are reported and `selected` is `null`.

The Python search checks `.venv/Scripts/python.exe`, `.venv/bin/python`, `venv/Scripts/python.exe`, and `venv/bin/python`, in that order. Environments stored outside the project are not discovered. The declared version from `.python-version` is reported separately from the actual interpreter version.

Ruff runs through the server's Python interpreter with the analyzed directory as its working directory. Inspection also runs version probes for available managers and the discovered project interpreter.

Each subprocess has a 60-second timeout. Captured stdout and stderr are each limited to their last 20,000 characters in the report. Large lint output may therefore be truncated and unavailable as parsed diagnostics. Nonzero subprocess exit codes are currently reported as `issues_found`; consult stderr and the exit code to distinguish configuration failures from code violations.

The server handles one directory per call. It does not discover and separately report projects inside a monorepo. Ruff may still check files recursively under the supplied directory according to its configuration.
