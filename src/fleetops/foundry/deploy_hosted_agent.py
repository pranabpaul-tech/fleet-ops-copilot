#!/usr/bin/env python
"""Package foundry/hosted_agent/ as a container and deploy it as a Foundry
hosted agent — the MAF pattern, replacing the retired Prompt Agent
(foundry/agent.py).

Uses the SDK code-upload path (`create_version_from_code` +
`CodeDependencyResolution.REMOTE_BUILD`, confirmed against Microsoft's own
04-foundry-toolbox sample) rather than build-your-own-container-and-push-to-
ACR: we deliberately didn't provision an ACR in Wave 1 (skipped along with
Container Registry — see modules/foundry.bicep), so this path avoids needing
one. A live private-network reference deployment
(pranabpaul-tech/foundry-iq-v2, same subscription) instead builds and pushes
its own image via `az acr build` and registers hosted agents with a raw
`POST .../agents/{name}/versions` body of `{"definition": {"kind": "hosted",
"container_configuration": {"image": ...}, ...}}` plus a required
`Foundry-Features: HostedAgents=V1Preview,...` header — if REMOTE_BUILD fails
in this network-isolated project (a real possibility this hasn't been tested
against), that's the documented fallback, but it needs an ACR we don't have.

Run toolbox.py first — this script reads the toolbox's MCP endpoint from
state.json['toolbox'].
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fleetops.common.config import StateStore, get_settings
from fleetops.common.logging_setup import setup_logging
from fleetops.foundry._rest import FoundryAgentRest, project_endpoint

logger = setup_logging(__name__)

HOSTED_AGENT_NAME = "fleet-incident-agent"
SOURCE_DIR = Path(__file__).resolve().parent / "hosted_agent"


def _zip_source(source_dir: Path) -> Path:
    zip_path = Path(tempfile.gettempdir()) / f"{HOSTED_AGENT_NAME}.zip"
    excluded = {".git", ".venv", "__pycache__", ".env"}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in source_dir.rglob("*"):
            if not path.is_file() or any(part in excluded for part in path.parts):
                continue
            zf.write(path, path.relative_to(source_dir))
    return zip_path


def deploy(model_name: str) -> None:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import (
        AgentEndpointConfig,
        CodeConfiguration,
        CodeDependencyResolution,
        FixedRatioVersionSelectionRule,
        HostedAgentDefinition,
        ProtocolConfiguration,
        ProtocolVersionRecord,
        ResponsesProtocolConfiguration,
        VersionSelector,
    )
    from azure.identity import DefaultAzureCredential

    state = StateStore()
    account_name = state.output("wave1", "foundryAccountName")
    project_name = state.output("wave1", "foundryProjectName")
    toolbox = state.require("toolbox", "toolboxName", "mcpEndpoint")

    endpoint = project_endpoint(account_name, project_name)
    zip_path = _zip_source(SOURCE_DIR)
    logger.info("Packaged %s -> %s", SOURCE_DIR, zip_path)

    with zip_path.open("rb") as code_stream, DefaultAzureCredential() as credential, \
            AIProjectClient(endpoint=endpoint, credential=credential) as project:

        created = project.agents.create_version_from_code(
            agent_name=HOSTED_AGENT_NAME,
            description="Fleet Incident Agent — conversational investigator for the Fleet Ops Copilot.",
            definition=HostedAgentDefinition(
                cpu="1",
                memory="2Gi",
                code_configuration=CodeConfiguration(
                    runtime="python_3_13",
                    entry_point=["python", "main.py"],
                    dependency_resolution=CodeDependencyResolution.REMOTE_BUILD,
                ),
                environment_variables={
                    # FOUNDRY_PROJECT_ENDPOINT is deliberately NOT set here — a
                    # live reference found FOUNDRY_*/AGENT_* env var names are
                    # platform-reserved and rejected; it's auto-injected.
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME": model_name,
                    "TOOLBOX_ENDPOINT": toolbox["mcpEndpoint"],
                },
                protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
            ),
            code=code_stream,
        )
        logger.info("Created hosted agent version %s", created.version)

        for attempt in range(60):
            time.sleep(10)
            details = project.agents.get_version(agent_name=HOSTED_AGENT_NAME, agent_version=created.version)
            status = details["status"]
            logger.info("Provisioning status: %s (attempt %d/60)", status, attempt + 1)
            if status == "active":
                break
            if status == "failed":
                raise RuntimeError(f"Hosted agent provisioning failed: {dict(details)}")
        else:
            raise RuntimeError("Timed out waiting for the hosted agent version to become active.")

        project.agents.update_details(
            agent_name=HOSTED_AGENT_NAME,
            agent_endpoint=AgentEndpointConfig(
                version_selector=VersionSelector(
                    version_selection_rules=[
                        FixedRatioVersionSelectionRule(agent_version=created.version, traffic_percentage=100),
                    ]
                ),
                protocol_configuration=ProtocolConfiguration(responses=ResponsesProtocolConfiguration()),
            ),
        )

    # publish_teams.py needs instance_identity.client_id + the activityProtocol
    # endpoint — fetch both now rather than leaving it as a manual follow-up.
    rest = FoundryAgentRest(account_name, project_name)
    details = rest.get_agent(HOSTED_AGENT_NAME)
    identity = details.get("instance_identity", {})
    client_id = identity.get("client_id")
    if not client_id:
        raise RuntimeError(f"Hosted agent {HOSTED_AGENT_NAME} has no instance_identity.client_id yet. "
                            f"Full response: {details}")

    state.merge("foundry_agent", {
        "agentName": HOSTED_AGENT_NAME,
        "agentVersion": created.version,
        "kind": "hosted",
        "clientId": client_id,
        "principalId": identity.get("principal_id"),
        "activityEndpoint": rest.activity_protocol_endpoint(HOSTED_AGENT_NAME),
    })
    logger.info("Done. state.json['foundry_agent'] updated. Next: infra/wave3-bot.bicep, then foundry/publish_teams.py.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-4.1")
    args = parser.parse_args()
    deploy(args.model)


if __name__ == "__main__":
    main()
