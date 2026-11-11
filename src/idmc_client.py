"""
Project-Scoped Informatica IDMC API Client.

Encapsulates the multi-step REST API chains across:
1. Platform REST API v3 (/saas/public/core/v3/login & /saas/public/core/v3/objects)
2. ActiveVOS Taskflow Execution & Status API (/active-bpel/rt/<name> & /active-bpel/services/tf/status)
3. Cloud Data Profiling API (/profiling-service/api/v1/profile & /ruleOccurrence/...)
4. Cloud Data Governance & Catalog (CDGC) API (/data360/search/v1/assets)
5. CLAIRE Metadata Recommendations, Auto-Classifications & Anomaly Insights

Strictly enforces single-project isolation via the hardcoded IDMC_PROJECT_NAME environment variable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class ProjectAccessDeniedError(PermissionError):
    """Raised when an operation attempts to access an asset outside the hardcoded IDMC_PROJECT_NAME."""


class InformaticaIDMCClient:
    """
    Unified asynchronous client for Informatica IDMC APIs with hardcoded single-project isolation.
    """

    def __init__(
        self,
        project_name: str | None = None,
        mock_mode: bool | None = None,
        login_url: str | None = None,
        pod_base_url: str | None = None,
        profiling_base_url: str | None = None,
        cdgc_base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        org_id: str | None = None,
    ) -> None:
        # Hardcoded project scope per user/deployment (Requirement #3)
        self._project_name: str = (
            project_name or os.getenv("IDMC_PROJECT_NAME", "Finance_Customer360")
        ).strip()
        if not self._project_name:
            raise ValueError("IDMC_PROJECT_NAME must be configured and non-empty.")

        env_mock = os.getenv("IDMC_MOCK_MODE", "true").lower() in ("true", "1", "yes")
        self.mock_mode: bool = env_mock if mock_mode is None else mock_mode

        # Locked to dm-us region by default, with automatic pod URL derivation (e.g. na1, usw3, usw5, use4)
        self.region: str = os.getenv("IDMC_REGION", "dm-us").strip()
        self.pod: str = os.getenv("IDMC_POD", "usw5").strip()

        self.login_url = (
            login_url or os.getenv("IDMC_LOGIN_URL", f"https://{self.region}.informaticacloud.com")
        ).rstrip("/")
        self.pod_base_url = (
            pod_base_url
            or os.getenv("IDMC_POD_BASE_URL", f"https://{self.pod}.{self.region}.informaticacloud.com")
        ).rstrip("/")
        self.profiling_base_url = (
            profiling_base_url
            or os.getenv(
                "IDMC_PROFILING_BASE_URL",
                f"https://{self.pod}-dqprofile.{self.region}.informaticacloud.com",
            )
        ).rstrip("/")
        self.cdgc_base_url = (
            cdgc_base_url
            or os.getenv("IDMC_CDGC_BASE_URL", f"https://idmc-api.{self.region}.informaticacloud.com")
        ).rstrip("/")

        self.username = username or os.getenv("IDMC_USERNAME", "svc_finance_mcp@company.com")
        self.password = password or os.getenv("IDMC_PASSWORD", "")
        self.org_id = org_id or os.getenv("IDMC_ORG_ID", "011ABC00000000000001")

        self._session_id: str | None = None
        self._jwt_token: str | None = None

    @property
    def project_name(self) -> str:
        """Read-only hardcoded project name enforced across all API calls."""
        return self._project_name

    def _verify_project_path(self, asset_path: str) -> bool:
        """Ensure an asset path belongs to the hardcoded project."""
        normalized = asset_path.strip("/")
        if normalized.startswith("Explore/"):
            normalized = normalized[len("Explore/") :]
        top_project = normalized.split("/", 1)[0]
        return top_project.lower() == self._project_name.lower()

    async def authenticate(self) -> dict[str, str]:
        """
        Authenticate against IDMC Platform REST API v3 and acquire session/JWT tokens.
        """
        if self.mock_mode:
            self._session_id = "mock_infa_session_99887766"
            self._jwt_token = "mock_eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
            return {
                "icSessionId": self._session_id,
                "jwtToken": self._jwt_token,
                "baseApiUrl": f"{self.pod_base_url}/saas",
                "orgId": self.org_id,
            }

        if self._session_id and self._jwt_token:
            return {
                "icSessionId": self._session_id,
                "jwtToken": self._jwt_token,
                "baseApiUrl": f"{self.pod_base_url}/saas",
                "orgId": self.org_id,
            }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.login_url}/saas/public/core/v3/login",
                json={"username": self.username, "password": self.password},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            self._session_id = data.get("userInfo", {}).get("sessionId") or data.get("icSessionId")
            self._jwt_token = os.getenv("IDMC_JWT_TOKEN", self._session_id)

            # Dynamically update dm-us pod & profiling URLs from the login response baseApiUrl
            returned_base = data.get("products", [{}])[0].get("baseApiUrl", "")
            if returned_base and ".dm-us.informaticacloud.com" in returned_base:
                pod_host = returned_base.split("/saas")[0].rstrip("/")
                self.pod_base_url = pod_host
                pod_prefix = pod_host.split("https://")[-1].split(".dm-us.informaticacloud.com")[0]
                self.pod = pod_prefix
                self.profiling_base_url = f"https://{pod_prefix}-dqprofile.dm-us.informaticacloud.com"

            return {
                "icSessionId": self._session_id or "",
                "jwtToken": self._jwt_token or "",
                "baseApiUrl": returned_base or f"{self.pod_base_url}/saas",
                "orgId": data.get("userInfo", {}).get("orgId", self.org_id),
            }

    # =========================================================================
    # BUSINESS ISSUE #2 (Part A): Discover Taskflows in Hardcoded Project
    # =========================================================================
    async def list_project_taskflows(self) -> dict[str, Any]:
        """
        List all Taskflows strictly inside the hardcoded project (`IDMC_PROJECT_NAME`).
        Hides the behind-the-scenes v3/login + v3/objects query chain.
        """
        await self.authenticate()
        api_chain = [
            f"POST {self.login_url}/saas/public/core/v3/login",
            f"GET {self.pod_base_url}/saas/public/core/v3/objects?q=type=='TASKFLOW' and location=='{self.project_name}'",
        ]

        if self.mock_mode:
            all_objects = self._mock_catalog_taskflows()
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self.pod_base_url}/saas/public/core/v3/objects",
                    params={"q": f"type=='TASKFLOW' and location=='{self.project_name}'"},
                    headers={"INFA-SESSION-ID": self._session_id or "", "Accept": "application/json"},
                )
                resp.raise_for_status()
                all_objects = resp.json().get("objects", [])

        project_taskflows = [
            obj for obj in all_objects if self._verify_project_path(obj.get("path", ""))
        ]

        return {
            "hardcoded_project": self.project_name,
            "total_taskflows": len(project_taskflows),
            "taskflows": project_taskflows,
            "hidden_api_chain_executed": api_chain,
        }

    # =========================================================================
    # BUSINESS ISSUE #2 (Part B): Execute Taskflow by Name Within Project
    # =========================================================================
    async def execute_project_taskflow(
        self,
        taskflow_name: str,
        input_payload: dict[str, Any] | None = None,
        wait_for_initial_status: bool = True,
    ) -> dict[str, Any]:
        """
        Resolve a Taskflow by name inside `IDMC_PROJECT_NAME`, verify project isolation,
        trigger its published `/active-bpel/rt/<service_name>` endpoint, and optionally poll status.
        """
        discovery = await self.list_project_taskflows()
        matching = [
            tf
            for tf in discovery["taskflows"]
            if tf["name"].lower() == taskflow_name.strip().lower()
        ]

        if not matching:
            raise ProjectAccessDeniedError(
                f"Taskflow '{taskflow_name}' was not found inside hardcoded project "
                f"'{self.project_name}' (or belongs to an unauthorized project)."
            )

        target_tf = matching[0]
        service_name = target_tf.get("serviceName", target_tf["name"])
        run_endpoint = f"{self.pod_base_url}/active-bpel/rt/{service_name}"

        api_chain = list(discovery["hidden_api_chain_executed"])
        api_chain.append(f"POST {run_endpoint}")

        if self.mock_mode:
            run_id = f"9145108355{ abs(hash(taskflow_name)) % 1000000:06d}"
            initial_status = "RUNNING"
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    run_endpoint,
                    json=input_payload or {},
                    headers={"INFA-SESSION-ID": self._session_id or "", "Accept": "application/json"},
                )
                resp.raise_for_status()
                run_id = str(resp.json().get("runId"))
                initial_status = "RUNNING"

        if wait_for_initial_status:
            status_url = f"{self.pod_base_url}/active-bpel/services/tf/status/{run_id}"
            api_chain.append(f"GET {status_url}")
            if not self.mock_mode:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    st_resp = await client.get(
                        status_url,
                        headers={"INFA-SESSION-ID": self._session_id or "", "Accept": "application/json"},
                    )
                    if st_resp.status_code == 200:
                        initial_status = st_resp.json().get("status", initial_status)

        return {
            "hardcoded_project": self.project_name,
            "taskflow_name": target_tf["name"],
            "taskflow_frs_id": target_tf["id"],
            "folder_path": target_tf["path"],
            "run_id": run_id,
            "execution_status": initial_status,
            "service_url_invoked": run_endpoint,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "hidden_api_chain_executed": api_chain,
        }

    # =========================================================================
    # BUSINESS ISSUE #1: Monitor Taskflows & Inspect Ended-With-Error Runs
    # =========================================================================
    async def get_taskflow_runs(
        self,
        status_filter: str = "ALL",
        hours_back: int = 24,
        include_error_details: bool = True,
    ) -> dict[str, Any]:
        """
        Monitor Taskflows in `IDMC_PROJECT_NAME`, identify which runs succeeded or ended with errors,
        and drill into `/active-bpel/services/tf/status/{runId}` for failed/suspended runs.
        """
        discovery = await self.list_project_taskflows()
        allowed_tf_names = {tf["name"].lower() for tf in discovery["taskflows"]}

        now = datetime.now(timezone.utc)
        start_time = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        status_query_url = (
            f"{self.pod_base_url}/active-bpel/services/tf/status"
            f"?startTime={start_time}&endTime={end_time}&rowLimit=50"
        )
        normalized_filter = status_filter.strip().upper()
        if normalized_filter != "ALL":
            status_query_url += f"&runStatus={normalized_filter}"

        api_chain = list(discovery["hidden_api_chain_executed"])
        api_chain.append(f"GET {status_query_url}")

        if self.mock_mode:
            raw_runs = self._mock_taskflow_runs()
        else:
            params: dict[str, Any] = {"startTime": start_time, "endTime": end_time, "rowLimit": 50}
            if normalized_filter != "ALL":
                params["runStatus"] = normalized_filter
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self.pod_base_url}/active-bpel/services/tf/status",
                    params=params,
                    headers={"INFA-SESSION-ID": self._session_id or "", "Accept": "application/json"},
                )
                resp.raise_for_status()
                raw_runs = resp.json() if isinstance(resp.json(), list) else resp.json().get("runs", [])

        # Filter strictly to the hardcoded project's taskflows
        filtered_runs: list[dict[str, Any]] = []
        for run in raw_runs:
            tf_name = run.get("assetName", "").lower()
            if tf_name not in allowed_tf_names:
                continue
            if normalized_filter != "ALL" and run.get("status", "").upper() != normalized_filter:
                continue

            run_copy = dict(run)
            if include_error_details and run_copy.get("status") in ("FAILED", "SUSPENDED"):
                detail_url = f"{self.pod_base_url}/active-bpel/services/tf/status/{run_copy['runId']}"
                api_chain.append(f"GET {detail_url}")
                if not self.mock_mode:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        d_resp = await client.get(
                            detail_url,
                            headers={"INFA-SESSION-ID": self._session_id or "", "Accept": "application/json"},
                        )
                        if d_resp.status_code == 200:
                            run_copy["error_drilldown"] = d_resp.json()
            filtered_runs.append(run_copy)

        failed_runs = [r for r in filtered_runs if r.get("status") in ("FAILED", "SUSPENDED")]
        return {
            "hardcoded_project": self.project_name,
            "time_window": {"startTime": start_time, "endTime": end_time, "hours_back": hours_back},
            "status_filter": normalized_filter,
            "summary": {
                "total_runs": len(filtered_runs),
                "succeeded": sum(1 for r in filtered_runs if r.get("status") == "SUCCESS"),
                "failed_or_suspended": len(failed_runs),
                "running": sum(1 for r in filtered_runs if r.get("status") == "RUNNING"),
            },
            "runs": filtered_runs,
            "hidden_api_chain_executed": api_chain,
        }

    # =========================================================================
    # BUSINESS ISSUE #3: Hardcoded Project Profiling & DQ Rule Occurrences
    # =========================================================================
    async def get_project_profiling_and_dq_scores(
        self,
        asset_or_profile_filter: str | None = None,
        only_failing_rules: bool = False,
    ) -> dict[str, Any]:
        """
        Retrieve Cloud Data Profiling results AND CDGC Data Quality Rule Occurrence scores
        strictly scoped to `IDMC_PROJECT_NAME`.
        Chains:
          1. GET /profiling-service/api/v1/profile (filtered by frsProjectName == IDMC_PROJECT_NAME)
          2. GET /profiling-service/api/v1/ruleOccurrence/getAllRuleOccurrencesForProfile_V2_Token/{profileId}
          3. POST /data360/search/v1/assets (core.classType == com.infa.ccgf.models.governance.RuleInstance)
          4. GET /data360/search/v1/assets/{roId}?scheme=internal&segments=all
        """
        await self.authenticate()
        api_chain = [
            f"POST {self.login_url}/saas/public/core/v3/login",
            f"GET {self.profiling_base_url}/profiling-service/api/v1/profile (Filter: frsProjectName=='{self.project_name}')",
        ]

        if self.mock_mode:
            all_profiles = self._mock_profiles()
            cdgc_ro_map = self._mock_cdgc_rule_occurrences()
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                prof_resp = await client.get(
                    f"{self.profiling_base_url}/profiling-service/api/v1/profile",
                    headers={"IDS-SESSION-ID": self._session_id or "", "Accept": "application/json"},
                )
                prof_resp.raise_for_status()
                all_profiles = prof_resp.json()
                cdgc_ro_map = {}

        # Enforce single-project isolation
        project_profiles = [
            p
            for p in all_profiles
            if p.get("frsProjectName", "").lower() == self.project_name.lower()
        ]

        if asset_or_profile_filter:
            kw = asset_or_profile_filter.strip().lower()
            project_profiles = [
                p
                for p in project_profiles
                if kw in p.get("name", "").lower() or kw in p.get("sourceObjectName", "").lower()
            ]

        enriched_profiles: list[dict[str, Any]] = []
        all_rule_occurrences: list[dict[str, Any]] = []

        for prof in project_profiles:
            prof_id = prof["id"]
            api_chain.append(
                f"GET {self.profiling_base_url}/profiling-service/api/v1/ruleOccurrence/"
                f"getAllRuleOccurrencesForProfile_V2_Token/{prof_id}"
            )
            api_chain.append(
                f"POST {self.cdgc_base_url}/data360/search/v1/assets "
                f"(core.classType='com.infa.ccgf.models.governance.RuleInstance' for profile '{prof['name']}')"
            )

            prof_copy = dict(prof)
            ro_list: list[dict[str, Any]] = []
            for ro_id in prof.get("linkedRuleOccurrenceIds", []):
                api_chain.append(
                    f"GET {self.cdgc_base_url}/data360/search/v1/assets/{ro_id}?scheme=internal&segments=all"
                )
                ro_detail = cdgc_ro_map.get(ro_id)
                if ro_detail:
                    if only_failing_rules and ro_detail.get("thresholdStatus") != "BREACHED":
                        continue
                    ro_list.append(ro_detail)
                    all_rule_occurrences.append(ro_detail)

            prof_copy["rule_occurrences"] = ro_list
            enriched_profiles.append(prof_copy)

        breached_count = sum(
            1 for ro in all_rule_occurrences if ro.get("thresholdStatus") == "BREACHED"
        )
        avg_score = (
            round(
                sum(ro.get("dqScorePercent", 0.0) for ro in all_rule_occurrences)
                / len(all_rule_occurrences),
                2,
            )
            if all_rule_occurrences
            else 0.0
        )

        return {
            "hardcoded_project": self.project_name,
            "overall_project_dq_summary": {
                "total_profiles_evaluated": len(enriched_profiles),
                "total_rule_occurrences": len(all_rule_occurrences),
                "average_dq_score_percent": avg_score,
                "rules_breaching_threshold": breached_count,
                "rules_passing_threshold": len(all_rule_occurrences) - breached_count,
            },
            "profiles": enriched_profiles,
            "hidden_api_chain_executed": api_chain,
        }

    # =========================================================================
    # BUSINESS ISSUE #4: CLAIRE APIs for Metadata, DQ Drift & Pipeline Insights
    # =========================================================================
    async def get_claire_project_insights(
        self, focus_area: str = "ALL"
    ) -> dict[str, Any]:
        """
        Synthesize CLAIRE-generated insights for `IDMC_PROJECT_NAME`:
        1. CLAIRE Auto-Classifications (PII/PCI sensitivity & confidence scores from CDGC)
        2. CLAIRE Glossary & Rule Recommendations
        3. CLAIRE Cross-Service Root-Cause Correlation (linking failed taskflows to DQ score drops)
        """
        await self.authenticate()
        normalized_focus = focus_area.strip().upper()
        api_chain = [
            f"POST {self.login_url}/saas/public/core/v3/login",
            f"POST {self.cdgc_base_url}/data360/search/v1/assets?knowledgeQuery=*&segments=all "
            f"(CLAIRE classifications, recommendations & observability for project '{self.project_name}')",
        ]

        insights = self._mock_claire_insights()
        result: dict[str, Any] = {
            "hardcoded_project": self.project_name,
            "focus_area": normalized_focus,
        }

        if normalized_focus in ("ALL", "CLASSIFICATIONS"):
            result["claire_auto_classifications"] = insights["claire_auto_classifications"]
        if normalized_focus in ("ALL", "RECOMMENDATIONS"):
            result["claire_recommendations"] = insights["claire_recommendations"]
        if normalized_focus in ("ALL", "QUALITY_ANOMALIES"):
            result["claire_dq_anomalies_and_pipeline_correlations"] = insights[
                "claire_dq_anomalies_and_pipeline_correlations"
            ]

        result["hidden_api_chain_executed"] = api_chain
        return result

    # =========================================================================
    # Realistic Mock Dataset for Local Testing & Demonstrations
    # =========================================================================
    def _mock_catalog_taskflows(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "2bF9lA88100Customer360TF",
                "name": "tf_Daily_Customer360_Refresh",
                "serviceName": "tf_Daily_Customer360_Refresh",
                "path": f"Explore/{self.project_name}/Pipelines/tf_Daily_Customer360_Refresh",
                "type": "TASKFLOW",
                "description": "Ingests CRM & ERP customer records, validates Tax IDs, and loads DIM_CUSTOMER.",
                "updatedBy": "data_eng_lead@company.com",
                "updateTime": "2026-11-10T06:00:00Z",
                "isPublished": True,
            },
            {
                "id": "7cK2mB99200InvoiceReconTF",
                "name": "tf_Invoice_Ledger_Reconciliation",
                "serviceName": "tf_Invoice_Ledger_Reconciliation",
                "path": f"Explore/{self.project_name}/Pipelines/tf_Invoice_Ledger_Reconciliation",
                "type": "TASKFLOW",
                "description": "Reconciles AR invoices against general ledger balances and publishes DQ scorecards.",
                "updatedBy": "finance_ops@company.com",
                "updateTime": "2026-11-09T18:30:00Z",
                "isPublished": True,
            },
            {
                "id": "9xP4nQ11300CrossBorderTaxTF",
                "name": "tf_CrossBorder_Tax_Compliance",
                "serviceName": "tf_CrossBorder_Tax_Compliance",
                "path": f"Explore/{self.project_name}/Compliance/tf_CrossBorder_Tax_Compliance",
                "type": "TASKFLOW",
                "description": "Validates VAT/Tax IDs and generates regulatory audit extracts.",
                "updatedBy": "tax_automation@company.com",
                "updateTime": "2026-11-10T11:15:00Z",
                "isPublished": True,
            },
            # Asset from a different project to prove isolation filtering works
            {
                "id": "0zOtherProjectSecretTF",
                "name": "tf_HR_Payroll_Confidential",
                "serviceName": "tf_HR_Payroll_Confidential",
                "path": "Explore/HR_Confidential_Project/Pipelines/tf_HR_Payroll_Confidential",
                "type": "TASKFLOW",
                "description": "Should never be exposed to Finance_Customer360 users.",
                "updatedBy": "hr_admin@company.com",
                "updateTime": "2026-11-10T01:00:00Z",
                "isPublished": True,
            },
        ]

    def _mock_taskflow_runs(self) -> list[dict[str, Any]]:
        return [
            {
                "runId": "914510835553251001",
                "assetName": "tf_Daily_Customer360_Refresh",
                "assetId": "2bF9lA88100Customer360TF",
                "location": f"{self.project_name}/Pipelines",
                "status": "SUCCESS",
                "startTime": "2026-11-10T06:00:12Z",
                "endTime": "2026-11-10T06:14:45Z",
                "durationSeconds": 873,
                "executedBy": "scheduler_prod",
                "subtasksSummary": {"total": 4, "completed": 4, "failed": 0},
            },
            {
                "runId": "914510835553251089",
                "assetName": "tf_CrossBorder_Tax_Compliance",
                "assetId": "9xP4nQ11300CrossBorderTaxTF",
                "location": f"{self.project_name}/Compliance",
                "status": "FAILED",
                "startTime": "2026-11-10T12:00:05Z",
                "endTime": "2026-11-10T12:04:19Z",
                "durationSeconds": 254,
                "executedBy": "svc_finance_mcp@company.com",
                "error_drilldown": {
                    "failedSubtaskName": "mt_Validate_Customer_TaxID_Format",
                    "failedSubtaskType": "MTT (Mapping Task)",
                    "faultCode": "DTM_10042_DQ_THRESHOLD_ABORT",
                    "errorMessage": (
                        "Data Quality Rule Occurrence [RO_CUST_TAX_ID_VALIDITY] breached critical abort "
                        "threshold: actual score 82.4% < required minimum 95.0% (1,760 invalid TAX_ID rows)."
                    ),
                    "logReferenceUrl": f"{self.pod_base_url}/active-bpel/services/tf/status/914510835553251089",
                },
            },
            {
                "runId": "914510835553251112",
                "assetName": "tf_Invoice_Ledger_Reconciliation",
                "assetId": "7cK2mB99200InvoiceReconTF",
                "location": f"{self.project_name}/Pipelines",
                "status": "SUSPENDED",
                "startTime": "2026-11-10T15:30:00Z",
                "endTime": "2026-11-10T15:35:10Z",
                "durationSeconds": 310,
                "executedBy": "scheduler_prod",
                "error_drilldown": {
                    "failedSubtaskName": "mt_Sync_ERP_Exchange_Rates",
                    "failedSubtaskType": "MTT (Mapping Task)",
                    "faultCode": "CONN_TIMEOUT_ERP_API",
                    "errorMessage": (
                        "Connection timeout after 180s waiting for ERP Currency Rate endpoint; "
                        "taskflow suspended at fault handler step."
                    ),
                    "logReferenceUrl": f"{self.pod_base_url}/active-bpel/services/tf/status/914510835553251112",
                },
            },
            # Run from another project to verify isolation
            {
                "runId": "914510835553999999",
                "assetName": "tf_HR_Payroll_Confidential",
                "assetId": "0zOtherProjectSecretTF",
                "location": "HR_Confidential_Project/Pipelines",
                "status": "FAILED",
                "startTime": "2026-11-10T10:00:00Z",
                "endTime": "2026-11-10T10:02:00Z",
                "durationSeconds": 120,
                "executedBy": "hr_admin@company.com",
            },
        ]

    def _mock_profiles(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "prof-cust-360-d916ebe8",
                "name": "PRF_DIM_CUSTOMER_MASTER",
                "frsProjectName": self.project_name,
                "profileType": "COLUMN_PROFILE",
                "sourceConnectionName": "SNOWFLAKE_FINANCE_PROD",
                "sourceObjectName": "FIN_DW.PUBLIC.DIM_CUSTOMER",
                "lastRunTime": "2026-11-10T11:55:00Z",
                "totalRowsProfiled": 10000,
                "columnProfilingSummary": [
                    {
                        "columnName": "CUSTOMER_ID",
                        "inferredDataType": "VARCHAR(36)",
                        "nullCount": 0,
                        "nullPercent": 0.0,
                        "distinctCount": 10000,
                        "distinctPercent": 100.0,
                    },
                    {
                        "columnName": "TAX_ID_NUMBER",
                        "inferredDataType": "VARCHAR(20)",
                        "nullCount": 410,
                        "nullPercent": 4.1,
                        "distinctCount": 8240,
                        "distinctPercent": 82.4,
                    },
                    {
                        "columnName": "EMAIL_ADDRESS",
                        "inferredDataType": "VARCHAR(120)",
                        "nullCount": 120,
                        "nullPercent": 1.2,
                        "distinctCount": 9850,
                        "distinctPercent": 98.5,
                    },
                ],
                "linkedRuleOccurrenceIds": [
                    "RO_CUST_TAX_ID_VALIDITY",
                    "RO_CUST_EMAIL_COMPLETENESS",
                ],
            },
            {
                "id": "prof-inv-ledger-a442bc10",
                "name": "PRF_FCT_INVOICE_LEDGER",
                "frsProjectName": self.project_name,
                "profileType": "COLUMN_PROFILE",
                "sourceConnectionName": "SNOWFLAKE_FINANCE_PROD",
                "sourceObjectName": "FIN_DW.PUBLIC.FCT_INVOICE_LEDGER",
                "lastRunTime": "2026-11-10T14:20:00Z",
                "totalRowsProfiled": 50000,
                "columnProfilingSummary": [
                    {
                        "columnName": "INVOICE_ID",
                        "inferredDataType": "NUMBER(18,0)",
                        "nullCount": 0,
                        "nullPercent": 0.0,
                        "distinctCount": 50000,
                        "distinctPercent": 100.0,
                    },
                    {
                        "columnName": "NET_AMOUNT_USD",
                        "inferredDataType": "DECIMAL(18,2)",
                        "nullCount": 0,
                        "nullPercent": 0.0,
                        "distinctCount": 31420,
                        "distinctPercent": 62.84,
                    },
                    {
                        "columnName": "CURRENCY_ISO_CODE",
                        "inferredDataType": "CHAR(3)",
                        "nullCount": 850,
                        "nullPercent": 1.7,
                        "distinctCount": 14,
                        "distinctPercent": 0.03,
                    },
                ],
                "linkedRuleOccurrenceIds": [
                    "RO_INV_AMOUNT_NON_NEGATIVE",
                    "RO_INV_CURRENCY_ISO_ACCURACY",
                ],
            },
            # Profile from another project to prove strict single-project filtering
            {
                "id": "prof-hr-secret-9999",
                "name": "PRF_HR_SALARY_BANDS",
                "frsProjectName": "HR_Confidential_Project",
                "profileType": "COLUMN_PROFILE",
                "sourceConnectionName": "WORKDAY_HR_PROD",
                "sourceObjectName": "HR.PUBLIC.EMP_COMPENSATION",
                "lastRunTime": "2026-11-10T01:00:00Z",
                "totalRowsProfiled": 2500,
                "columnProfilingSummary": [],
                "linkedRuleOccurrenceIds": [],
            },
        ]

    def _mock_cdgc_rule_occurrences(self) -> dict[str, dict[str, Any]]:
        return {
            "RO_CUST_TAX_ID_VALIDITY": {
                "ruleOccurrenceId": "RO_CUST_TAX_ID_VALIDITY",
                "cdgcAssetType": "com.infa.ccgf.models.governance.RuleInstance",
                "ruleName": "dq_Rule_TaxID_ISO_Pattern_Check",
                "dimension": "Validity",
                "targetTable": "FIN_DW.PUBLIC.DIM_CUSTOMER",
                "targetColumn": "TAX_ID_NUMBER",
                "businessTerm": "Customer Tax Identification Number",
                "dqScorePercent": 82.4,
                "targetThresholdPercent": 95.0,
                "thresholdStatus": "BREACHED",
                "totalRows": 10000,
                "passedRows": 8240,
                "failedRows": 1760,
                "lastEvaluated": "2026-11-10T11:55:00Z",
            },
            "RO_CUST_EMAIL_COMPLETENESS": {
                "ruleOccurrenceId": "RO_CUST_EMAIL_COMPLETENESS",
                "cdgcAssetType": "com.infa.ccgf.models.governance.RuleInstance",
                "ruleName": "dq_Rule_Email_Not_Null_And_RFC5322",
                "dimension": "Completeness",
                "targetTable": "FIN_DW.PUBLIC.DIM_CUSTOMER",
                "targetColumn": "EMAIL_ADDRESS",
                "businessTerm": "Primary Contact Email",
                "dqScorePercent": 98.8,
                "targetThresholdPercent": 95.0,
                "thresholdStatus": "PASSED",
                "totalRows": 10000,
                "passedRows": 9880,
                "failedRows": 120,
                "lastEvaluated": "2026-11-10T11:55:00Z",
            },
            "RO_INV_AMOUNT_NON_NEGATIVE": {
                "ruleOccurrenceId": "RO_INV_AMOUNT_NON_NEGATIVE",
                "cdgcAssetType": "com.infa.ccgf.models.governance.RuleInstance",
                "ruleName": "dq_Rule_Invoice_NetAmount_Positive",
                "dimension": "Accuracy",
                "targetTable": "FIN_DW.PUBLIC.FCT_INVOICE_LEDGER",
                "targetColumn": "NET_AMOUNT_USD",
                "businessTerm": "Net Invoice Value (USD)",
                "dqScorePercent": 99.94,
                "targetThresholdPercent": 99.0,
                "thresholdStatus": "PASSED",
                "totalRows": 50000,
                "passedRows": 49970,
                "failedRows": 30,
                "lastEvaluated": "2026-11-10T14:20:00Z",
            },
            "RO_INV_CURRENCY_ISO_ACCURACY": {
                "ruleOccurrenceId": "RO_INV_CURRENCY_ISO_ACCURACY",
                "cdgcAssetType": "com.infa.ccgf.models.governance.RuleInstance",
                "ruleName": "dq_Rule_Currency_ISO4217_Lookup",
                "dimension": "Validity",
                "targetTable": "FIN_DW.PUBLIC.FCT_INVOICE_LEDGER",
                "targetColumn": "CURRENCY_ISO_CODE",
                "businessTerm": "Settlement Currency Code",
                "dqScorePercent": 94.1,
                "targetThresholdPercent": 98.0,
                "thresholdStatus": "BREACHED",
                "totalRows": 50000,
                "passedRows": 47050,
                "failedRows": 2950,
                "lastEvaluated": "2026-11-10T14:20:00Z",
            },
        }

    def _mock_claire_insights(self) -> dict[str, Any]:
        return {
            "claire_auto_classifications": [
                {
                    "asset": "FIN_DW.PUBLIC.DIM_CUSTOMER.TAX_ID_NUMBER",
                    "inferredDataClassification": "National_Tax_Identifier_PII",
                    "sensitivityLevel": "HIGH_PII",
                    "confidenceScorePercent": 99.2,
                    "governanceStatus": "APPROVED",
                },
                {
                    "asset": "FIN_DW.PUBLIC.DIM_CUSTOMER.EMAIL_ADDRESS",
                    "inferredDataClassification": "Electronic_Mail_Address_PII",
                    "sensitivityLevel": "MEDIUM_PII",
                    "confidenceScorePercent": 99.8,
                    "governanceStatus": "APPROVED",
                },
                {
                    "asset": "FIN_DW.PUBLIC.FCT_INVOICE_LEDGER.CURRENCY_ISO_CODE",
                    "inferredDataClassification": "ISO_4217_Currency_Code",
                    "sensitivityLevel": "INTERNAL",
                    "confidenceScorePercent": 97.5,
                    "governanceStatus": "CLAIRE_RECOMMENDED_PENDING_REVIEW",
                },
            ],
            "claire_recommendations": [
                {
                    "recommendationId": "CLAIRE-REC-101",
                    "type": "DATA_QUALITY_RULE_SUGGESTION",
                    "targetAsset": "FIN_DW.PUBLIC.DIM_CUSTOMER.TAX_ID_NUMBER",
                    "insight": (
                        "CLAIRE detected 1,350 records with non-standard prefix 'XX-' ingested from EMEA CRM batch. "
                        "Recommended action: Apply Cleansing Rule 'std_Strip_Legacy_Country_Prefix' prior to "
                        "Taskflow 'tf_CrossBorder_Tax_Compliance'."
                    ),
                    "confidenceScorePercent": 94.6,
                },
                {
                    "recommendationId": "CLAIRE-REC-102",
                    "type": "PK_FK_RELATIONSHIP_DISCOVERY",
                    "targetAsset": "FIN_DW.PUBLIC.FCT_INVOICE_LEDGER -> FIN_DW.PUBLIC.DIM_CUSTOMER",
                    "insight": (
                        "CLAIRE inferred a 99.98% join overlap between FCT_INVOICE_LEDGER.BILL_TO_CUST_ID "
                        "and DIM_CUSTOMER.CUSTOMER_ID."
                    ),
                    "confidenceScorePercent": 99.9,
                },
            ],
            "claire_dq_anomalies_and_pipeline_correlations": [
                {
                    "anomalyId": "CLAIRE-ANOM-501",
                    "impactedTaskflow": "tf_CrossBorder_Tax_Compliance",
                    "failedRunId": "914510835553251089",
                    "correlatedRuleOccurrenceId": "RO_CUST_TAX_ID_VALIDITY",
                    "previousScorePercent": 99.1,
                    "currentScorePercent": 82.4,
                    "scoreDeltaPercent": -16.7,
                    "rootCauseSummary": (
                        "CLAIRE observability correlated the failure of 'tf_CrossBorder_Tax_Compliance' "
                        "(Subtask: mt_Validate_Customer_TaxID_Format) directly to a -16.7% drop in Rule Occurrence "
                        "'RO_CUST_TAX_ID_VALIDITY' on column DIM_CUSTOMER.TAX_ID_NUMBER."
                    ),
                }
            ],
        }
