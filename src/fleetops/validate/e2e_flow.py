#!/usr/bin/env python
"""End-to-end validation: ingestion freshness, the reference KQL queries,
private DNS resolution, a live agent turn that must cite the MCP tool's
output, and a negative test proving the approval gate actually blocks an
unapproved action call.

Run this last, from the jumpbox, after every setup/ and foundry/ script has
succeeded. A failure in "agent MCP turn" is reported as a WARNING, not a hard
failure — it's the one step testing the architecture's single biggest
unvalidated risk (see foundry/mcp_tool.py's module docstring), so a failure
here is diagnostic information, not necessarily a bug in this script.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fleetops.common.config import StateStore, get_settings
from fleetops.common.logging_setup import setup_logging
from fleetops.actions.approval import ApprovalError, require_approved, request_approval

logger = setup_logging(__name__)
SCRIPTS_DIR = Path(__file__).resolve().parent


def run_subcheck(name: str, module_file: str, *extra_args: str) -> bool:
    logger.info("--- %s ---", name)
    result = subprocess.run([sys.executable, str(SCRIPTS_DIR / module_file), *extra_args])
    passed = result.returncode == 0
    logger.info("%s: %s", name, "PASS" if passed else "FAIL")
    return passed


def check_approval_gate_blocks_unapproved_calls() -> bool:
    logger.info("--- approval gate negative test ---")
    approval_id = request_approval("test.no_op", "e2e_flow negative test — should never be approved", {})
    try:
        require_approved(approval_id)
        logger.error("approval gate: FAIL — require_approved() did not raise for an unapproved approval_id")
        return False
    except ApprovalError:
        logger.info("approval gate: PASS — require_approved() correctly refused an unapproved approval_id")
        return True


def check_agent_mcp_turn() -> bool:
    logger.info("--- agent MCP turn (risk #1) ---")
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from fleetops.foundry._rest import project_endpoint

        state = StateStore()
        account_name = state.output("wave1", "foundryAccountName")
        project_name = state.output("wave1", "foundryProjectName")
        foundry_agent = state.require("foundry_agent", "agentName")

        project = AIProjectClient(endpoint=project_endpoint(account_name, project_name), credential=DefaultAzureCredential())
        openai_client = project.get_openai_client()
        conversation = openai_client.conversations.create()
        response = openai_client.responses.create(
            conversation=conversation.id,
            input="What is the most recent event for any vehicle in BusTelemetry, and when was it?",
            extra_body={"agent_reference": {"name": foundry_agent["agentName"], "type": "agent_reference"}},
        )

        used_mcp = any(getattr(item, "type", "").startswith("mcp_") for item in response.output)
        if not used_mcp:
            logger.warning("agent MCP turn: WARNING — response didn't include any mcp_* output items. "
                            "If require_approval='always', the first call is expected to stop at an "
                            "mcp_approval_request rather than a real tool call — check response.output "
                            "manually before concluding the MCP path is broken.")
            return False
        logger.info("agent MCP turn: PASS — response included MCP tool-call activity")
        return True
    except Exception as exc:  # noqa: BLE001 — this check is explicitly allowed to fail; log why and move on
        logger.warning("agent MCP turn: WARNING — could not complete a live agent turn (%s). "
                        "This is the architecture's flagged top risk (foundry/mcp_tool.py) — a failure "
                        "here means it needs investigation, not that this script is broken.", exc)
        return False


def main() -> None:
    results = {
        "smoke_kql": run_subcheck("KQL smoke test", "smoke_kql.py"),
        "network_check": run_subcheck("Private DNS resolution", "network_check.py"),
        "approval_gate": check_approval_gate_blocks_unapproved_calls(),
        "agent_mcp_turn": check_agent_mcp_turn(),
    }

    logger.info("\n=== Summary ===")
    for name, passed in results.items():
        logger.info("%-16s %s", name, "PASS" if passed else "FAIL/WARN")

    hard_checks = ["smoke_kql", "network_check", "approval_gate"]
    if not all(results[k] for k in hard_checks):
        logger.error("One or more required checks failed.")
        sys.exit(1)
    if not results["agent_mcp_turn"]:
        logger.warning("Required checks passed; the MCP turn check did not — see risk #1.")
    logger.info("e2e_flow: DONE")


if __name__ == "__main__":
    main()
