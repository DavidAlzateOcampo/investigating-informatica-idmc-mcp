"""
Automated verification suite for Informatica IDMC Project-Scoped MCP Server.

Verifies:
1. Business Issue #1: Taskflow monitoring & error root-cause drill-down (`get_taskflow_runs`)
2. Business Issue #2: Project taskflow listing (`list_project_taskflows`) and chained execution (`execute_project_taskflow`)
3. Business Issue #3: Hardcoded single-project isolation & CDP + CDGC Rule Occurrence scoring (`get_project_profiling_and_dq_scores`)
4. Business Issue #4: CLAIRE auto-classifications, recommendations & anomaly correlations (`get_claire_project_insights`)
5. Security Boundary: Attempting to execute or view assets from another project (`HR_Confidential_Project`) is blocked.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

try:
    import pytest
except ImportError:
    pytest = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.idmc_client import InformaticaIDMCClient, ProjectAccessDeniedError
from src.idmc_mcp_server import (
    execute_project_taskflow,
    get_claire_project_insights,
    get_project_profiling_and_dq_scores,
    get_taskflow_runs,
    list_project_taskflows,
    project_summary_resource,
)


def test_issue_1_monitor_taskflows_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify monitoring taskflows and drilling into FAILED/SUSPENDED subtask errors."""
    monkeypatch.setenv("IDMC_PROJECT_NAME", "Finance_Customer360")
    monkeypatch.setenv("IDMC_MOCK_MODE", "true")

    all_runs = asyncio.run(get_taskflow_runs(status_filter="ALL", hours_back=24))
    assert all_runs["hardcoded_project"] == "Finance_Customer360"
    assert all_runs["summary"]["total_runs"] == 3
    assert all_runs["summary"]["succeeded"] == 1
    assert all_runs["summary"]["failed_or_suspended"] == 2

    # Verify cross-project runs (HR_Confidential_Project) were strictly filtered out
    run_names = {r["assetName"] for r in all_runs["runs"]}
    assert "tf_HR_Payroll_Confidential" not in run_names

    # Filter specifically for FAILED runs and inspect subtask error details
    failed_only = asyncio.run(get_taskflow_runs(status_filter="FAILED", hours_back=24))
    assert len(failed_only["runs"]) == 1
    failed_tf = failed_only["runs"][0]
    assert failed_tf["assetName"] == "tf_CrossBorder_Tax_Compliance"
    assert "error_drilldown" in failed_tf
    assert failed_tf["error_drilldown"]["faultCode"] == "DTM_10042_DQ_THRESHOLD_ABORT"


def test_issue_2_discover_and_execute_taskflow_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify listing and executing taskflows by name without exposing the behind-the-scenes API chain."""
    monkeypatch.setenv("IDMC_PROJECT_NAME", "Finance_Customer360")
    monkeypatch.setenv("IDMC_MOCK_MODE", "true")

    listing = asyncio.run(list_project_taskflows())
    assert listing["hardcoded_project"] == "Finance_Customer360"
    assert listing["total_taskflows"] == 3
    names = [tf["name"] for tf in listing["taskflows"]]
    assert "tf_Daily_Customer360_Refresh" in names
    assert "tf_HR_Payroll_Confidential" not in names

    # Execute a valid project taskflow by name
    exec_result = asyncio.run(
        execute_project_taskflow(
            taskflow_name="tf_Daily_Customer360_Refresh",
            input_payload_json='{"batchDate": "2026-11-10"}',
            wait_for_initial_status=True,
        )
    )
    assert exec_result["hardcoded_project"] == "Finance_Customer360"
    assert exec_result["taskflow_name"] == "tf_Daily_Customer360_Refresh"
    assert exec_result["execution_status"] == "RUNNING"
    assert len(exec_result["hidden_api_chain_executed"]) == 4


def test_issue_2_and_3_cross_project_execution_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that a user cannot execute a taskflow belonging to another project."""
    monkeypatch.setenv("IDMC_PROJECT_NAME", "Finance_Customer360")
    monkeypatch.setenv("IDMC_MOCK_MODE", "true")

    try:
        asyncio.run(execute_project_taskflow(taskflow_name="tf_HR_Payroll_Confidential"))
    except ProjectAccessDeniedError:
        return
    raise AssertionError("Expected ProjectAccessDeniedError when executing cross-project taskflow")


def test_issue_3_hardcoded_project_profiling_and_dq_rule_occurrences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Cloud Data Profiling + CDGC Rule Occurrence scores scoped strictly to IDMC_PROJECT_NAME."""
    monkeypatch.setenv("IDMC_PROJECT_NAME", "Finance_Customer360")
    monkeypatch.setenv("IDMC_MOCK_MODE", "true")

    dq_report = asyncio.run(get_project_profiling_and_dq_scores())
    assert dq_report["hardcoded_project"] == "Finance_Customer360"
    summary = dq_report["overall_project_dq_summary"]
    assert summary["total_profiles_evaluated"] == 2
    assert summary["total_rule_occurrences"] == 4
    assert summary["rules_breaching_threshold"] == 2
    assert summary["rules_passing_threshold"] == 2

    # Ensure HR profile is excluded
    profile_names = {p["name"] for p in dq_report["profiles"]}
    assert "PRF_HR_SALARY_BANDS" not in profile_names

    # Test filtering for only failing rules on CUSTOMER profile
    failing_cust = asyncio.run(
        get_project_profiling_and_dq_scores(
            asset_or_profile_filter="CUSTOMER",
            only_failing_rules=True,
        )
    )
    assert len(failing_cust["profiles"]) == 1
    cust_ros = failing_cust["profiles"][0]["rule_occurrences"]
    assert len(cust_ros) == 1
    assert cust_ros[0]["ruleOccurrenceId"] == "RO_CUST_TAX_ID_VALIDITY"
    assert cust_ros[0]["dqScorePercent"] == 82.4
    assert cust_ros[0]["thresholdStatus"] == "BREACHED"


def test_issue_4_claire_insights_and_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLAIRE auto-classifications, recommendations, and pipeline anomaly correlation."""
    monkeypatch.setenv("IDMC_PROJECT_NAME", "Finance_Customer360")
    monkeypatch.setenv("IDMC_MOCK_MODE", "true")

    claire_all = asyncio.run(get_claire_project_insights(focus_area="ALL"))
    assert claire_all["hardcoded_project"] == "Finance_Customer360"
    assert len(claire_all["claire_auto_classifications"]) == 3
    assert len(claire_all["claire_recommendations"]) == 2
    assert len(claire_all["claire_dq_anomalies_and_pipeline_correlations"]) == 1

    anomaly = claire_all["claire_dq_anomalies_and_pipeline_correlations"][0]
    assert anomaly["impactedTaskflow"] == "tf_CrossBorder_Tax_Compliance"
    assert anomaly["correlatedRuleOccurrenceId"] == "RO_CUST_TAX_ID_VALIDITY"

    summary_json = asyncio.run(project_summary_resource())
    assert "Finance_Customer360" in summary_json


def test_client_requires_non_empty_project() -> None:
    """Verify InformaticaIDMCClient rejects empty project names."""
    try:
        InformaticaIDMCClient(project_name="   ")
    except ValueError:
        return
    raise AssertionError("Expected ValueError for empty project_name")


def test_dm_us_regional_url_derivation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify dm-us region and pod URLs are automatically derived when IDMC_REGION='dm-us'."""
    monkeypatch.setenv("IDMC_REGION", "dm-us")
    monkeypatch.setenv("IDMC_POD", "na1")
    client = InformaticaIDMCClient(project_name="Finance_Customer360")
    assert client.region == "dm-us"
    assert client.login_url == "https://dm-us.informaticacloud.com"
    assert client.pod_base_url == "https://na1.dm-us.informaticacloud.com"
    assert client.profiling_base_url == "https://na1-dqprofile.dm-us.informaticacloud.com"
    assert client.cdgc_base_url == "https://idmc-api.dm-us.informaticacloud.com"


class _SimpleMonkeyPatch:
    """Lightweight env patcher so tests run under both pytest and standard unittest."""

    def setenv(self, key: str, value: str) -> None:
        os.environ[key] = value


class TestInformaticaIDMCMCP(unittest.TestCase):
    def setUp(self) -> None:
        self.mp = _SimpleMonkeyPatch()

    def test_1_monitor_taskflows_and_errors(self) -> None:
        test_issue_1_monitor_taskflows_and_errors(self.mp)  # type: ignore[arg-type]

    def test_2_discover_and_execute_taskflow_by_name(self) -> None:
        test_issue_2_discover_and_execute_taskflow_by_name(self.mp)  # type: ignore[arg-type]

    def test_3_cross_project_execution_blocked(self) -> None:
        test_issue_2_and_3_cross_project_execution_blocked(self.mp)  # type: ignore[arg-type]

    def test_4_hardcoded_project_profiling_and_dq_rule_occurrences(self) -> None:
        test_issue_3_hardcoded_project_profiling_and_dq_rule_occurrences(self.mp)  # type: ignore[arg-type]

    def test_5_claire_insights_and_resource(self) -> None:
        test_issue_4_claire_insights_and_resource(self.mp)  # type: ignore[arg-type]

    def test_6_client_requires_non_empty_project(self) -> None:
        test_client_requires_non_empty_project()

    def test_7_dm_us_regional_url_derivation(self) -> None:
        test_dm_us_regional_url_derivation(self.mp)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)

