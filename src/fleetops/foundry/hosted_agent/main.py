# Copyright (c) Microsoft. All rights reserved.
"""Fleet Incident Agent — MAF hosted agent.

This is real application code, packaged as a container and deployed to
Foundry Agent Service (not a Prompt Agent definition) — see
foundry/deploy_hosted_agent.py. Pattern confirmed against Microsoft's own
04-foundry-toolbox sample (foundry-samples repo) and a live private-network
reference deployment (pranabpaul-tech/foundry-iq-v2, same subscription).

Tools come from a single Foundry Toolbox (fleetops/foundry/toolbox.py) wrapping
the Fabric Eventhouse MCP endpoint, rather than a generic MCP client — the
platform's documented guidance for MAF hosted agents specifically: "If you use
Microsoft Agent Framework, connect through FoundryToolbox ... instead of a
generic MCP client."

FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME are injected by
the platform / set at registration time — see register_hosted_agent in
deploy_hosted_agent.py. Do not set FOUNDRY_* or AGENT_* env vars yourself:
they're reserved and the platform rejects registrations that try.
"""
import asyncio
import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

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
    credential = DefaultAzureCredential()

    # FoundryToolbox resolves the toolbox endpoint from the environment
    # (TOOLBOX_ENDPOINT, or FOUNDRY_PROJECT_ENDPOINT + TOOLBOX_NAME),
    # authenticates every request with the credential (the agent's own Entra
    # identity at runtime), and forwards the platform's per-request call-id
    # to the toolbox.
    toolbox = FoundryToolbox(credential)

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=toolbox,
        # History is managed by the hosting infrastructure (conversation ID) —
        # no need for the agent itself to persist it too.
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
