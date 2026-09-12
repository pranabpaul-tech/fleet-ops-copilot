#!/usr/bin/env python
"""Build the Fabric Eventhouse MCP server URL, and create the Foundry project
connection that authenticates it — via a direct `az rest PUT` on the
connection's ARM resource, not `azd ai connection create`.

============================== risk #1, read this ==============================
This is the single highest-risk edge in the whole architecture (see the
Architecture diagram's teal path). One part of it is now de-risked, one isn't:

1. NETWORK PATH (still unvalidated): the MCP URL below is Fabric's *global*
   endpoint (api.fabric.microsoft.com). Whether it honors workspace-level
   Fabric private link when called from the Foundry agent's own VNet-injected
   subnet isn't documented anywhere Microsoft publishes. Test this in the
   Foundry playground before building anything else on top of it. If it
   doesn't work, the contingency is a custom tool that queries Kusto directly
   over the private endpoint instead of going through MCP at all.

2. AUTH PATH (de-risked): a prior attempt to pass bearer tokens directly via
   the SDK's tool object (`update_headers()`) is a known, still-open SDK bug
   (Azure/azure-sdk-for-python#43071) that silently drops the header. The
   working pattern — confirmed against a *live, deployed* reference
   (pranabpaul-tech/foundry-iq-v2, a private-network Foundry project with its
   own Fabric connection, in this same subscription) — is a `UserEntraToken`
   connection created via a direct ARM PUT, not `azd`:

     PUT https://management.azure.com{connection-arm-id}?api-version=2025-10-01-preview
     {"properties": {"category": "RemoteTool", "authType": "UserEntraToken",
                      "target": "<mcp-server-url>", "audience": "https://api.fabric.microsoft.com"}}

   Two corrections this gave over the generic MCP docs example: the audience
   is `https://api.fabric.microsoft.com` (matching this endpoint's own host),
   not `https://analysis.windows.net/powerbi/api` (that was for a *different*
   Fabric MCP path, /v1/mcp/fabricaihub/integrations/m365); and
   `project_connection_id` on the tool must be the connection's **full ARM
   resource ID**, not its short name — passing just the name is a silent bug,
   not a validation error.

   Going through `az rest` directly also sidesteps `azd`'s interactive
   sign-in, which is its own documented risk in tenants that enforce
   Conditional Access requiring a managed device (see the second README risk
   section) — one less thing that can block this step.
==================================================================================
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fleetops.common.config import StateStore
from fleetops.common.logging_setup import setup_logging

logger = setup_logging(__name__)

# Confirmed against a live deployment (see module docstring) — not the
# powerbi.com audience shown in Microsoft's generic MCP connection example,
# which was for a different Fabric MCP path.
FABRIC_MCP_AUDIENCE = "https://api.fabric.microsoft.com"
CONNECTION_API_VERSION = "2025-10-01-preview"


def eventhouse_mcp_url(workspace_id: str, kql_database_item_id: str) -> str:
    return (
        f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace_id}"
        f"/items/{kql_database_item_id}/kqlEndpoint"
    )


def connection_arm_id(subscription_id: str, resource_group: str, account_name: str,
                       project_name: str, connection_name: str) -> str:
    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/projects/{project_name}/connections/{connection_name}"
    )


def create_connection(connection_name: str, server_url: str) -> str:
    """Creates (or updates — PUT is idempotent) the UserEntraToken project
    connection for the Eventhouse MCP endpoint. Returns the connection's full
    ARM resource ID, which is what MCPToolboxTool's project_connection_id
    needs — not the bare connection_name."""
    import os
    import shutil

    az_path = shutil.which("az")
    if az_path is None:
        raise RuntimeError("az CLI not found on PATH.")

    state = StateStore()
    subscription_id = os.environ.get("FLEETOPS_SUBSCRIPTION_ID") or state.get("wave1", {}).get("subscriptionId")
    if not subscription_id:
        # Not every wave1 output includes it explicitly — fall back to the az CLI's current context.
        result = subprocess.run([az_path, "account", "show", "--query", "id", "-o", "tsv"],
                                 capture_output=True, text=True, timeout=20)
        subscription_id = result.stdout.strip()
    resource_group = state.output("wave1", "resourceGroupName")
    account_name = state.output("wave1", "foundryAccountName")
    project_name = state.output("wave1", "foundryProjectName")

    conn_id = connection_arm_id(subscription_id, resource_group, account_name, project_name, connection_name)
    body = {
        "properties": {
            "category": "RemoteTool",
            "authType": "UserEntraToken",
            "target": server_url,
            "audience": FABRIC_MCP_AUDIENCE,
        }
    }

    logger.info("Creating/updating connection %s...", conn_id)
    result = subprocess.run(
        [az_path, "rest", "--method", "put",
         "--url", f"https://management.azure.com{conn_id}?api-version={CONNECTION_API_VERSION}",
         "--body", json.dumps(body)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Connection creation failed: {result.stderr.strip()}")

    state.merge("mcp_connection", {"connectionName": connection_name, "connectionId": conn_id})
    return conn_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-name", default="fleetops-eventhouse-mcp")
    parser.add_argument("--apply", action="store_true", help="Actually create the connection (default: just print what would happen).")
    args = parser.parse_args()

    state = StateStore()
    eventhouse = state.require("eventhouse", "kqlDatabaseId")
    workspace_id = state.output("workspace", "workspaceId")
    server_url = eventhouse_mcp_url(workspace_id, eventhouse["kqlDatabaseId"])

    logger.info("MCP server URL: %s", server_url)
    if args.apply:
        conn_id = create_connection(args.connection_name, server_url)
        logger.info("Done. Connection ARM ID (state.json['mcp_connection']): %s", conn_id)
    else:
        logger.info("Dry run — pass --apply to actually create the connection %s.", args.connection_name)


if __name__ == "__main__":
    main()
