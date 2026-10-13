from mcp.server import MCPServer

from pyproject_doctor.models import AnalysisResult, InspectionResult, RuffChecksResult
from pyproject_doctor.tools import analyze_project, inspect_project, run_ruff_checks

mcp = MCPServer("PyProjectDoctor")


@mcp.tool()
def inspect(path: str) -> InspectionResult:
    """Inspect package manager and Python environment for one project."""
    return inspect_project(path)


@mcp.tool()
def ruff_ckeck(path: str) -> RuffChecksResult:
    """Run Ruff lint and formatting checks for one project."""
    return run_ruff_checks(path)


@mcp.tool()
def analyze(path: str) -> AnalysisResult:
    """Inspect a project and run Ruff lint and formatting checks."""
    return analyze_project(path)


def main() -> None:
    """Run the server over stdio for local MCP clients."""
    mcp.run()


if __name__ == "__main__":
    main()
