# Investigating Informatica IDMC MCP Server 🚀🤖

An architectural proposal, API evaluation, connection tutorial, and reference **Model Context Protocol (MCP) Server** for **Informatica Intelligent Data Management Cloud (IDMC)**.

This project solves five key enterprise requirements using **Platform REST APIs (v2/v3)**, **ActiveVOS Taskflow APIs**, **Cloud Data Profiling (CDP) APIs**, **Cloud Data Governance and Catalog (CDGC) APIs**, and **CLAIRE AI/Metadata APIs**:

1. **Taskflow Monitoring & Error Triage (`get_taskflow_runs`):** Identify which taskflows have run in the user's project and inspect runs that ended with errors (`FAILED` / `SUSPENDED`), including subtask-level fault details.
2. **Project-Scoped Taskflow Discovery & Execution (`list_project_taskflows`, `execute_project_taskflow`):** Allow users to list and trigger taskflows by human-readable name without knowing the underlying multi-step REST API chain (`v3/login` $\rightarrow$ `v3/objects` $\rightarrow$ `/active-bpel/rt/<service_name>` $\rightarrow$ `/active-bpel/services/tf/status/<runId>`).
3. **Hardcoded Single-Project Profiling & DQ Rule Occurrences (`get_project_profiling_and_dq_scores`):** Enforce strict tenant/project isolation via an environment variable (`IDMC_PROJECT_NAME`) set in `.mcp.json`. Chaining Cloud Data Profiling APIs (`/profiling-service/api/v1/profile` & `/ruleOccurrence/getAllRuleOccurrencesForProfile_V2_Token/{id}`) with CDGC Search APIs (`/data360/search/v1/assets` for `com.infa.ccgf.models.governance.RuleInstance`) to deliver unified profiling metrics and Data Quality Rule Occurrence scores.
4. **CLAIRE Insights & Recommendations (`get_claire_project_insights`):** Extract CLAIRE auto-classifications, business glossary recommendations, PK/FK relationship discoveries, and correlate failed taskflows with upstream data quality degradation.
5. **Step-by-Step Connection Guide:** Complete walkthrough for connecting AI clients (Claude Desktop, Claude Code, Cursor, VS Code, Antigravity) to Informatica's **Native Shared MCP Servers**, **Custom CAI Self-Managed MCP Servers**, and **this Project-Scoped Python MCP Server**.

---

## 📂 Repository Structure

```text
c:/obData/investigating-informatica-idmc-mcp/
├── README.md                                      # Project overview & quickstart
├── status.md                                      # Live task checklist & progress tracker
├── pyproject.toml                                 # Python package & dependency configuration
├── mcp.json.example                               # Example MCP client configuration (Custom + Native Shared)
├── docs/
│   ├── 01_API_Evaluation_and_MCP_Proposal.md      # Deep-dive API evaluation, payload schemas & MCP design
│   └── 02_Tutorial_Connect_Informatica_MCP.md     # Step-by-step tutorial for all 3 Informatica MCP patterns
├── src/
│   ├── __init__.py
│   ├── idmc_client.py                             # Project-scoped async IDMC REST/CDP/CDGC/CLAIRE client
│   └── idmc_mcp_server.py                         # FastMCP server exposing the 5 project-isolated tools
└── tests/
    └── test_idmc_mcp.py                           # Automated test suite verifying all 5 business scenarios
```

---

## ⚡ Quickstart

### 1. Run Automated Verification Tests (Mock Mode)
The client includes a realistic built-in simulator (`IDMC_MOCK_MODE=true`) so you can verify all tool chains immediately without live IDMC credentials:

```powershell
pytest c:/obData/investigating-informatica-idmc-mcp/tests/test_idmc_mcp.py -v
```

### 2. Configure Your MCP Client (`.mcp.json`)
Copy [`mcp.json.example`](file:///c:/obData/investigating-informatica-idmc-mcp/mcp.json.example) to your client's MCP configuration file and set `IDMC_PROJECT_NAME` to the single Informatica project assigned to the user:

```json
{
  "mcpServers": {
    "informatica-idmc-project": {
      "command": "python",
      "args": [
        "c:/obData/investigating-informatica-idmc-mcp/src/idmc_mcp_server.py"
      ],
      "env": {
        "IDMC_PROJECT_NAME": "Finance_Customer360",
        "IDMC_MOCK_MODE": "false",
        "IDMC_LOGIN_URL": "https://dm-us.informaticacloud.com",
        "IDMC_POD_BASE_URL": "https://usw5.dm-us.informaticacloud.com",
        "IDMC_PROFILING_BASE_URL": "https://usw5-dqprofile.dm-us.informaticacloud.com",
        "IDMC_CDGC_BASE_URL": "https://idmc-api.dm-us.informaticacloud.com",
        "IDMC_USERNAME": "svc_finance_mcp@company.com",
        "IDMC_PASSWORD": "YOUR_SECURE_PASSWORD",
        "IDMC_ORG_ID": "org_123456789"
      }
    }
  }
}
```

---

## 📖 Documentation Index

1. [**API Evaluation & MCP Server Architecture Proposal (`docs/01_API_Evaluation_and_MCP_Proposal.md`)**](file:///c:/obData/investigating-informatica-idmc-mcp/docs/01_API_Evaluation_and_MCP_Proposal.md)
2. [**Step-by-Step Tutorial: How to Connect to Informatica MCP (`docs/02_Tutorial_Connect_Informatica_MCP.md`)**](file:///c:/obData/investigating-informatica-idmc-mcp/docs/02_Tutorial_Connect_Informatica_MCP.md)
3. [**Project Task & Milestone Status (`status.md`)**](file:///c:/obData/investigating-informatica-idmc-mcp/status.md)
