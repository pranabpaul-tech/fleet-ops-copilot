#!/usr/bin/env python
"""Create the "Fleet Operations Monitor" Operations Agent from
artifacts/ops-agent/OperationsAgentV1.json, or capture a portal-authored one.

MUST run under the operator's own delegated identity, not a service principal
— the Operations Agent inherits its creator's identity and runs actions as
them. This script fails fast (assert_delegated_identity) rather than silently
creating an agent nobody can trace.

Not available in East US, not in sovereign clouds, not in CMK-encrypted
workspaces — if creation fails with a region/policy error, that's most likely
why, not a bug in this script.

Same capture/apply workflow as setup/04_eventstream.py:
  1. Author once in the portal (instructions, KQL data source, Generate
     Playbook, review the generated per-rule KQL in Query Insights).
  2. python 06_ops_agent.py --capture <opsAgentId>
  3. python 06_ops_agent.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fleetops.common.auth import assert_delegated_identity
from fleetops.common.config import StateStore, get_settings
from fleetops.common.fabric_client import FabricClient, decode_definition_part, definition_part
from fleetops.common.logging_setup import setup_logging

logger = setup_logging(__name__)

DEFINITION_PATH = Path(__file__).resolve().parents[3] / "artifacts" / "ops-agent" / "OperationsAgentV1.json"
DEFINITION_ITEM_PATH = "OperationsAgentV1.json"


def capture(client: FabricClient, workspace_id: str, ops_agent_id: str) -> None:
    logger.info("Fetching definition for Operations Agent %s...", ops_agent_id)
    result = client.call("POST", f"/workspaces/{workspace_id}/operationsAgents/{ops_agent_id}/getDefinition"
                                  f"?format=OperationsAgentV1")
    parts = result.get("definition", {}).get("parts", [])
    part = next((p for p in parts if p["path"] == DEFINITION_ITEM_PATH), None)
    if part is None:
        raise RuntimeError(f"getDefinition response had no '{DEFINITION_ITEM_PATH}' part. Parts present: "
                            f"{[p.get('path') for p in parts]}")
    decoded = decode_definition_part(part)
    DEFINITION_PATH.write_text(json.dumps(decoded, indent=2), encoding="utf-8")
    logger.info("Captured real definition -> %s. Review it, then commit it.", DEFINITION_PATH)


def apply(client: FabricClient, workspace_id: str, state: StateStore) -> None:
    raw = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
    if raw.get("_placeholder"):
        raise RuntimeError(
            f"{DEFINITION_PATH} is still the placeholder shape. Author the Operations Agent once in "
            f"the portal and run `python 06_ops_agent.py --capture <opsAgentId>` first — see this "
            f"script's module docstring."
        )
    clean = {k: v for k, v in raw.items() if not k.startswith("_")}

    settings = get_settings()
    existing = state.get("ops_agent")
    if existing and existing.get("opsAgentId"):
        logger.info("Operations Agent already recorded in state.json — nothing to do.")
        return

    logger.info("Creating Operations Agent '%s' from %s...", settings.ops_agent_name, DEFINITION_PATH)
    result = client.call("POST", f"/workspaces/{workspace_id}/operationsAgents", {
        "displayName": settings.ops_agent_name,
        "definition": {
            "parts": [definition_part(DEFINITION_ITEM_PATH, clean)],
        },
    })
    ops_agent_id = result["id"]
    state.merge("ops_agent", {
        "opsAgentId": ops_agent_id,
        "opsAgentName": settings.ops_agent_name,
    })
    logger.info("Done. state.json['ops_agent'] updated. opsAgentId=%s", ops_agent_id)
    logger.info("Next: install the Fabric Operations Agent Teams app, set the Fleet Operations "
                "channel as recipient, wire the Power Automate action, and start the agent.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--capture", metavar="OPS_AGENT_ID")
    parser.add_argument("--skip-identity-check", action="store_true")
    args = parser.parse_args()

    if not args.skip_identity_check:
        assert_delegated_identity()

    state = StateStore()
    workspace_id = state.output("workspace", "workspaceId")
    client = FabricClient()

    if args.capture:
        capture(client, workspace_id, args.capture)
    else:
        apply(client, workspace_id, state)


if __name__ == "__main__":
    main()
