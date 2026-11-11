"""
Informatica IDMC Project-Scoped MCP Server (FastMCP).

Exposes 5 high-level tools tailored to a single hardcoded Informatica Project (`IDMC_PROJECT_NAME`):
1. `get_taskflow_runs` — Monitor taskflows & inspect runs that ended with errors (Issue #1)
2. `list_project_taskflows` — List available taskflows in the hardcoded project (Issue #2)
3. `execute_project_taskflow` — Trigger a project taskflow by name without knowing the API chain (Issue #2)
4. `get_project_profiling_and_dq_scores` — Get Cloud Data Profiling + CDGC Rule Occurrence scores (Issue #3)
5. `get_claire_project_insights` — Retrieve CLAIRE classifications, recommendations & anomaly correlations (Issue #4)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure package imports work when invoked directly as `python src/idmc_mcp_server.py`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.idmc_client import InformaticaIDMCClient

try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("informatica-idmc-project-mcp")
except ImportError:  # Fallback shim if `mcp` package is not installed in current test python env

    class _FallbackFastMCP:
        def __init__(self, name: str) -> None:
            self.name = name
            self.tools: dict[str, Any] = {}
            self.resources: dict[str, Any] = {}

        def tool(self, *args: Any, **kwargs: Any):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn

            return decorator

        def resource(self, uri: str, *args: Any, **kwargs: Any):
            def decorator(fn):
                self.resources[uri] = fn
                return fn

            return decorator

        def run(self) -> None:
            raise RuntimeError("Install the `mcp` package (`pip install mcp`) to run the stdio/SSE server.")

    mcp = _FallbackFastMCP("informatica-idmc-project-mcp")


def get_client() -> InformaticaIDMCClient:
    """Instantiate the project-scoped IDMC client bound to `IDMC_PROJECT_NAME`."""
    return InformaticaIDMCClient()


@mcp.tool()
async def get_taskflow_runs(
    status_filter: str = "ALL",
    hours_back: int = 24,
    include_error_details: bool = True,
) -> dict[str, Any]:
    """
    [Business Issue #1] Monitor taskflows in the user's hardcoded Informatica project and identify
    which taskflows have run and which ones ended with errors (FAILED or SUSPENDED).

    Args:
        status_filter: Filter runs by status ("ALL", "FAILED", "SUSPENDED", "RUNNING", "SUCCESS").
        hours_back: Lookback window in hours (default: 24).
        include_error_details: Automatically fetch subtask fault logs for FAILED/SUSPENDED runs.
    """
    client = get_client()
    return await client.get_taskflow_runs(
        status_filter=status_filter,
        hours_back=hours_back,
        include_error_details=include_error_details,
    )


@mcp.tool()
async def list_project_taskflows() -> dict[str, Any]:
    """
    [Business Issue #2 - Discovery] List all executable Taskflows inside the user's hardcoded
    Informatica project (`IDMC_PROJECT_NAME`) without requiring internal FRS IDs.
    """
    client = get_client()
    return await client.list_project_taskflows()


@mcp.tool()
async def execute_project_taskflow(
    taskflow_name: str,
    input_payload_json: str = "{}",
    wait_for_initial_status: bool = True,
) -> dict[str, Any]:
    """
    [Business Issue #2 - Execution] Execute a Taskflow by its human-readable name within the hardcoded
    Informatica project (`IDMC_PROJECT_NAME`). Automatically chains Login -> Object Lookup ->
    Project Boundary Check -> Service Endpoint Execution -> Run ID Status Check behind the scenes.

    Args:
        taskflow_name: Exact name of the taskflow inside the user's project (e.g. "tf_Daily_Customer360_Refresh").
        input_payload_json: Optional JSON string of input parameters for the taskflow.
        wait_for_initial_status: Poll `/active-bpel/services/tf/status/<runId>` after triggering.
    """
    client = get_client()
    parsed_inputs = json.loads(input_payload_json) if input_payload_json.strip() else {}
    return await client.execute_project_taskflow(
        taskflow_name=taskflow_name,
        input_payload=parsed_inputs,
        wait_for_initial_status=wait_for_initial_status,
    )


@mcp.tool()
async def get_project_profiling_and_dq_scores(
    asset_or_profile_filter: str = "",
    only_failing_rules: bool = False,
) -> dict[str, Any]:
    """
    [Business Issue #3] Retrieve Cloud Data Profiling statistics and CDGC Data Quality Rule Occurrence
    scores strictly scoped to the hardcoded project (`IDMC_PROJECT_NAME`).

    Args:
        asset_or_profile_filter: Optional table or profile name substring filter (e.g. "CUSTOMER" or "INVOICE").
        only_failing_rules: If True, only return Rule Occurrences that breached their target threshold.
    """
    client = get_client()
    return await client.get_project_profiling_and_dq_scores(
        asset_or_profile_filter=asset_or_profile_filter or None,
        only_failing_rules=only_failing_rules,
    )


@mcp.tool()
async def get_claire_project_insights(
    focus_area: str = "ALL",
) -> dict[str, Any]:
    """
    [Business Issue #4] Retrieve Informatica CLAIRE AI insights for the hardcoded project (`IDMC_PROJECT_NAME`),
    including auto-classified PII columns, recommended DQ rules & PK/FK relationships, and root-cause
    correlation between failed taskflows and degraded Data Quality Rule Occurrences.

    Args:
        focus_area: One of "ALL", "CLASSIFICATIONS", "RECOMMENDATIONS", or "QUALITY_ANOMALIES".
    """
    client = get_client()
    return await client.get_claire_project_insights(focus_area=focus_area)


@mcp.resource("idmc://project/summary")
async def project_summary_resource() -> str:
    """Return a JSON overview of the hardcoded project's taskflows, DQ health, and CLAIRE alerts."""
    client = get_client()
    runs = await client.get_taskflow_runs(hours_back=24)
    dq = await client.get_project_profiling_and_dq_scores()
    claire = await client.get_claire_project_insights(focus_area="QUALITY_ANOMALIES")
    return json.dumps(
        {
            "hardcoded_project": client.project_name,
            "taskflow_run_summary": runs["summary"],
            "dq_summary": dq["overall_project_dq_summary"],
            "claire_anomalies": claire.get("claire_dq_anomalies_and_pipeline_correlations", []),
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
