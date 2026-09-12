#!/usr/bin/env python
"""Create/update the Foundry Toolbox that wraps the Fabric Eventhouse MCP
connection behind one managed, versioned endpoint.

This replaces attaching MCPTool directly to an agent (the pattern in the
retired foundry/agent.py) — a Toolbox is Microsoft's recommended pattern
specifically because it "centralizes credential management, versioning, and
policy enforcement through a managed MCP endpoint" rather than wiring a
connection into one agent's definition. It's also required for the MAF
hosted-agent path: hosted agents connect to tools "through a Toolbox MCP
endpoint provisioned in your Foundry project rather than by adding them
directly to the agent definition" (Microsoft's hosted-agents concepts doc).

Still requires the same underlying project connection as the direct-attach
approach — run `python mcp_tool.py --apply` first, which creates it via a
direct `az rest PUT` (see that module's docstring for why, not `azd`); this
script only wraps that connection in a Toolbox, it doesn't create it.

risk #1 (see mcp_tool.py) is unchanged by this switch — the Toolbox is a
different way of *presenting* the same unvalidated MCP call, not a fix for it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fleetops.common.config import StateStore
from fleetops.common.logging_setup import setup_logging
from fleetops.foundry._rest import project_endpoint
from fleetops.foundry.mcp_tool import eventhouse_mcp_url

logger = setup_logging(__name__)

DEFAULT_TOOLBOX_NAME = "fleetops-toolbox"


def create_or_update(connection_name: str, toolbox_name: str) -> str:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import MCPToolboxTool
    from azure.identity import DefaultAzureCredential

    state = StateStore()
    account_name = state.output("wave1", "foundryAccountName")
    project_name = state.output("wave1", "foundryProjectName")
    eventhouse = state.require("eventhouse", "kqlDatabaseId")
    workspace_id = state.output("workspace", "workspaceId")

    endpoint = project_endpoint(account_name, project_name)
    server_url = eventhouse_mcp_url(workspace_id, eventhouse["kqlDatabaseId"])

    # project_connection_id must be the connection's full ARM resource ID, not
    # its bare name — passing just the name is a silent bug (confirmed against
    # a live reference), not something that fails loudly. mcp_tool.py --apply
    # writes this to state.json['mcp_connection']['connectionId'].
    mcp_connection = state.require("mcp_connection", "connectionId")
    connection_id = mcp_connection["connectionId"]

    with DefaultAzureCredential() as credential, AIProjectClient(endpoint=endpoint, credential=credential) as project:
        created = project.toolboxes.create_version(
            name=toolbox_name,
            description="Fabric Eventhouse MCP endpoint for the Fleet Incident Agent.",
            tools=[
                MCPToolboxTool(
                    server_label="fabric-eventhouse",
                    server_url=server_url,
                    project_connection_id=connection_id,
                    require_approval="always",  # tighten only after risk #1 is validated
                ),
            ],
        )

    mcp_endpoint = f"{endpoint}/toolboxes/{created.name}/versions/{created.version}/mcp?api-version=v1"
    logger.info("Toolbox '%s' version %s created.", created.name, created.version)
    logger.info("MCP endpoint: %s", mcp_endpoint)

    state.merge("toolbox", {
        "toolboxName": created.name,
        "toolboxVersion": created.version,
        "mcpEndpoint": mcp_endpoint,
        "connectionName": connection_name,
    })
    return mcp_endpoint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-name", default="fleetops-eventhouse-mcp",
                         help="Must match the --connection-name used with `python mcp_tool.py --apply`.")
    parser.add_argument("--toolbox-name", default=DEFAULT_TOOLBOX_NAME)
    args = parser.parse_args()

    create_or_update(args.connection_name, args.toolbox_name)


if __name__ == "__main__":
    main()
