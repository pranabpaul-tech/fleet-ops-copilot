# Copyright (c) Microsoft. All rights reserved.
"""Fleet Incident Agent — MAF hosted agent.

Real application code, packaged as a container and deployed to Foundry Agent
Service (not a declarative Prompt Agent definition) — see
foundry/deploy_hosted_agent.py. Its one tool, `query_bus_telemetry`, calls
`azure-kusto-data` directly against the Eventhouse from the agent's own
process — no MCP, no Toolbox — using the identity granted to it by
`deploy_hosted_agent.py`.

FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME are injected by
the platform / set at registration time. EVENTHOUSE_QUERY_URI and
EVENTHOUSE_DATABASE_NAME are set explicitly in deploy_hosted_agent.py from
state.json. Do not set FOUNDRY_* or AGENT_* env vars yourself: they're
reserved and the platform rejects registrations that try.
"""
import asyncio
import os
from typing import Annotated

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from dotenv import load_dotenv
from pydantic import Field

load_dotenv()

_credential = DefaultAzureCredential()
_kusto_client: KustoClient | None = None


def _get_kusto_client() -> KustoClient:
    global _kusto_client
    if _kusto_client is None:
        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
            connection_string=os.environ["EVENTHOUSE_QUERY_URI"],
            credential=_credential,
        )
        _kusto_client = KustoClient(kcsb)
    return _kusto_client


@tool(approval_mode="never_require")
def query_bus_telemetry(
    kql: Annotated[str, Field(description=(
        "A KQL query against the BusTelemetry table (or BusTelemetryRaw). "
        "Write the full query yourself, e.g. "
        "\"BusTelemetry | where EventTime > ago(20m) | order by EventTime desc | take 20\"."
    ))],
) -> str:
    """Run a KQL query directly against the Fleet Ops Eventhouse and return the
    result rows as JSON. This is the only source of truth for current vehicle
    telemetry, delays, and timelines — always call this rather than guessing."""
    try:
        response = _get_kusto_client().execute(os.environ["EVENTHOUSE_DATABASE_NAME"], kql)
        table = response.primary_results[0]
        columns = [c.column_name for c in table.columns]
        rows = [dict(zip(columns, row)) for row in table.rows]
        return str(rows)
    except Exception as exc:  # noqa: BLE001 — surfaced to the model verbatim, not swallowed
        return f"QUERY FAILED: {exc}"

INSTRUCTIONS = """\
You are the Fleet Incident Agent for control-room operators.

Use the real-time Eventhouse tool for all statements about current vehicle
location, telemetry, delays and incident timelines. The BusTelemetry table has
these columns: EventTime (datetime, actual arrival), VehicleId (string, a
trip identifier — not necessarily a persistent physical vehicle across
trips), RouteId (string, the bus line), Latitude, Longitude, SpeedKph,
DelayMinutes (real, actual vs. scheduled arrival — this fleet's real
schedule-adherence signal), OccupancyPercent (real), Status (string),
IngestionTime (datetime, when the flattening update policy processed the
event — use EventTime for timelines, not this). EngineTemperatureC and
BatteryPercent exist in the schema but are always null for this data source
— never cite them or reason about engine/battery health; this fleet's
telemetry is schedule-adherence and position data, not vehicle-health data.
Query the table directly with KQL; don't assume schema discovery is available.

When investigating an alert:
1. establish the affected vehicle, route and time window;
2. retrieve the event timeline;
3. compare the vehicle with others on the same route;
4. separate observed facts from possible causes;
5. recommend the lowest-risk operational response;
6. cite the event evidence used;
7. request explicit approval before calling an action tool.

Every statement you make about current telemetry must be backed by a tool
result you actually retrieved in this conversation — never state a value from
memory or from an earlier turn without re-querying if it might have changed.

Never claim that an operational action succeeded unless the tool call you made
returned a successful result. If a tool call fails or times out, say so plainly
and do not proceed as if it had worked.
"""


async def main() -> None:
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=_credential,
    )

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=query_bus_telemetry,
        # History is managed by the hosting infrastructure (conversation ID) —
        # no need for the agent itself to persist it too.
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
