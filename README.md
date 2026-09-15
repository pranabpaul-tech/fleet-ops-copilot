# Fleet Ops Copilot

Real-time bus fleet telemetry, ingested and analyzed on Microsoft Fabric, watched
proactively by a Fabric Operations Agent, and explorable conversationally through
a Foundry-hosted agent published to Microsoft Teams — all on a Fabric F8 capacity
with the conversational agent VNet-injected and the Fabric workspace reachable
over a private link.

## What it does

- **Ingests** live bus telemetry from Fabric's built-in "Buses" sample source
  through an Eventstream into a Fabric Eventhouse (KQL database), flattening
  raw events into a typed `BusTelemetry` table.
- **Monitors** that table continuously with a Fabric **Operations Agent**,
  which detects delay incidents, dwell/holding incidents, and vehicles that
  stop reporting, and sends an alert for each one.
- **Answers questions** through a **Foundry-hosted conversational agent**,
  published to Microsoft Teams, that queries `BusTelemetry` directly via its
  own custom Kusto tool to ground its answers in live data.

## Architecture

```mermaid
flowchart LR
    subgraph Fabric["Microsoft Fabric — F8 capacity"]
        direction TB
        Buses(["Buses sample source"]) --> ES["Eventstream"]
        ES --> Raw[("BusTelemetryRaw")]
        Raw -- "update policy" --> Flat[("BusTelemetry")]
        Flat --> OpsAgent["Operations Agent"]
    end

    subgraph VNet["Azure VNet"]
        direction TB
        subgraph AgentSubnet["snet-agent"]
            HostedAgent["Foundry hosted agent<br/>(custom Kusto tool)"]
        end
        subgraph ContainerSubnet["snet-container"]
            Jumpbox["Jumpbox (Container Instance)"]
        end
        subgraph PeSubnet["snet-pe"]
            PE1["Fabric workspace<br/>private endpoint"]
            PE2["Key Vault<br/>private endpoint"]
        end
    end

    HostedAgent -- "queries live data" --> Flat
    OpsAgent -- "alert" --> Recipient(["Teams / email recipient"])
    HostedAgent --> Bot["Bot Service"]
    Bot -- "publish" --> Teams(["Microsoft Teams"])
    Jumpbox -. "management &amp; validation" .-> Fabric
```

- **Microsoft Fabric** — an F8 capacity hosts the workspace, Eventhouse, and
  Operations Agent. The workspace is reachable over a private link
  (`infra/wave2-fabric-privatelink.bicep`).
- **Azure VNet** — three subnets: `snet-agent` (the Foundry account is
  VNet-injected here), `snet-pe` (private endpoints for the Fabric workspace
  and Key Vault), and `snet-container` (a jumpbox reachable via
  `az container exec` — no RDP/Bastion needed).
- **Foundry hosted agent** — real application code (Microsoft Agent
  Framework), not a declarative prompt agent. Its one tool queries the
  Eventhouse directly using its own granted identity.
- **Bot Service + Teams** — the hosted agent is published to Teams through a
  Bot Service registration.

Curious *why* any of this is built the way it is — timing-sensitive steps,
platform quirks, non-obvious error messages? See **[How it works](#how-it-works)**
at the bottom. Everything below this point is just what to do.

## Deploy

### Prerequisites

- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) (`azd`) and the Azure CLI (`az`), both signed in
  (`azd auth login`, `az login`) as a real user — not a service principal.
- Python 3.11+.
- An existing Azure AI Search service, Storage account, and Cosmos DB account.
  If you don't have them:
  ```bash
  az deployment group create --resource-group <rg> --template-file infra/wave0-byo-resources.bicep
  ```
- A region that isn't `eastus`, that supports Fabric capacities and Foundry
  VNet injection. The default (`swedencentral`) is already the right choice
  for most cases — see **How it works** → "Region choice matters" before
  changing it.
- Two Fabric admin portal tenant settings enabled, before you start: **Azure
  Private Link** and **Configure workspace-level inbound network rules**
  (plus whatever Copilot / Azure OpenAI tenant settings your tenant needs for
  the Operations Agent).
- The following resource providers registered in your subscription:
  `Microsoft.Fabric`, `Microsoft.BotService`, `Microsoft.App`,
  `Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.DocumentDB`,
  `Microsoft.Storage`, `Microsoft.KeyVault`.

### Step 1 — Configure

```bash
git clone <this-repo>
cd fleet-ops-copilot
cp .env.example .env   # fill in values
```

Fill in `infra/main.bicepparam`: your Fabric admin UPN(s), your own operator
object ID (`az ad signed-in-user show --query id -o tsv`), and the resource
IDs of the AI Search / Storage / Cosmos DB accounts from the prerequisites
step. (The resource group name, Foundry account base name, Fabric capacity
name, and hosted agent name are asked interactively in Step 2 instead — no
need to edit those here.)

### Step 2 — Provision everything Fabric-side

```bash
azd provision
```

A `preprovision` hook asks for four names before anything gets created —
press Enter at each prompt to keep the default shown:

- **Resource group name**
- **Foundry account base name** (a short deterministic suffix gets appended,
  e.g. `fleetopsai` → `fleetopsai4fgm`)
- **Fabric capacity name**
- **Foundry hosted agent name** (the technical identifier used for the
  actual Fabric/Foundry item — no spaces)

**Result:** the VNet, F8 Fabric capacity, Key Vault, jumpbox, and the
VNet-injected Foundry account/project are all deployed — and the Fabric
workspace, Eventhouse, Eventstream, and Operations Agent are all created and
wired together automatically right after. Confirm telemetry is already
flowing:

```bash
.venv/bin/python src/fleetops/validate/smoke_kql.py
```

**✅ Check before proceeding:** the command above shows live `BusTelemetry`
rows, and `state.json['ops_agent']` has a real `opsAgentId`.

### Step 3 — Lock the Fabric workspace to private-only access

```bash
./infra/deploy.ps1 -Wave 2
```

Then, from the jumpbox (see **Where to run these** below):

```bash
python3 src/fleetops/validate/network_check.py
```

Once that confirms DNS resolves privately, run this from your own machine:

```bash
.venv/bin/python src/fleetops/setup/05_network_policy.py --confirm
```

**✅ Check before proceeding:** `network_check.py` shows DNS resolving to a
private IP, and `05_network_policy.py --status` confirms the workspace's
public access is actually `Deny`.

### Step 4 — Deploy the Foundry hosted agent

```bash
.venv/bin/python src/fleetops/foundry/deploy_hosted_agent.py
```

If this fails with a `ProvisioningError` that doesn't clear on retry, use
the image-based fallback instead — see **How it works** for the exact
sequence.

**✅ Check before proceeding — don't skip this one:** confirm the agent is
actually *responding*, not just registered as `active`. Send it a real
query directly (see **How it works** → "Testing the agent directly" for the
exact snippet). This is the step most likely to need patience — Microsoft
Foundry hosted agents can take a while after creation before their
invocation route is reachable, even though the account and agent both show
healthy. Don't move on until a real response comes back.

### Step 5 — Deploy the Bot Service

```bash
./infra/deploy.ps1 -Wave 3
```

**✅ Check before proceeding:**
```bash
az bot show --name <botName> --resource-group <rg> --query "{msaAppId:properties.msaAppId, state:properties.provisioningState}"
```
Confirm `msaAppId` matches the agent identity from Step 4 and
`provisioningState` is `Succeeded`.

### Step 6 — Publish to Microsoft Teams

```bash
.venv/bin/python src/fleetops/foundry/publish_teams.py
```

**Result:** prints a direct Teams link —
`https://teams.microsoft.com/l/app/<teamsAppId>`. That's the end result of
this whole step: open it, and message the agent.

**✅ Check before proceeding:** confirm the publish call actually returned a
`teamsAppId` (not just a `titleId`). After testing in Teams, you can also
check the Bot Service's `RequestsTraffic` metric or Log Analytics for a real
inbound hit, to confirm the message actually reached the agent.

### Step 7 — Start the Operations Agent (manual, one-time, portal-only)

Its item and real monitoring instructions were already created automatically
in Step 2 — this is the only part of the whole deployment with no API, so it
has to be a human clicking two buttons:

1. Open the [Fabric portal](https://app.fabric.microsoft.com), go to your
   workspace (`fleet-ops-copilot` by default), and open the **Fleet
   Operations Monitor** item.
2. Click **Generate Playbook**. It may take more than one try.
3. Click on Settings (Gear icon) of the Agent and click on Agent behavor.
4. Edit Message Delivery > Send as a direct message > and select your name as user.
5. Save everything. Click save in Agent ribbon as weell.
6. Click **Start**.

**Result:** the agent begins evaluating `BusTelemetry` on its own schedule
and will alert on delay incidents, dwell/holding incidents, and vehicles
that stop reporting.

### Step 8 — Validate end to end

From the jumpbox:

```bash
python3 src/fleetops/validate/e2e_flow.py
```

### You're done

- **Operations Agent**: running, watching `BusTelemetry`, alerting on
  incidents.
- **Conversational agent**: live in Microsoft Teams at the link Step 6
  printed. Send it a question about a vehicle or route — it queries the
  Eventhouse directly and answers from live data.

### Where to run these from

Two kinds of machine come up above:

- **local** — your own machine, wherever you ran `azd provision` from.
  Fine for plain Azure/Fabric control-plane calls.
- **jumpbox** — the Foundry account's data-plane API and the workspace's
  *private* endpoint are only reachable from inside the VNet. Copy the repo
  onto the jumpbox once, then run scripts there:
  ```bash
  az container exec --resource-group <rg> --name ci-fleetops-jump \
    --container-name jumpbox --exec-command "python3 /path/to/script.py"
  ```
  (See **How it works** for exactly which steps need which, and why.)

## Repo layout

```
infra/           Bicep — 3 waves (see infra/README.md), plus the ACI jumpbox
                 and Foundry account/project
infra/hooks/     azd postprovision hooks — run the Fabric pipeline setup
                 scripts automatically after `azd provision`
src/fleetops/    Python — setup/ (Fabric items), foundry/ (hosted agent +
                 Teams publish), actions/ (approval-gated operations),
                 validate/ (smoke tests + full e2e check)
artifacts/       KQL schema, Eventstream definition, Operations Agent
                 instructions
state.json       created at runtime — the seam between Bicep outputs and
                 Python-created resource IDs. Never commit real values;
                 .gitignore covers it.
```

---

## How it works

Everything above is what to do. This section is the *why* — platform quirks,
timing-sensitive ordering, and the exact error messages you'll see if a step
is skipped or run out of order. Nothing here is required reading to deploy;
it exists for when something needs debugging.

### Why the Foundry account has to be public while deploying and publishing

**The single most important, least obvious fact in this whole repo.** The
Foundry account's hosted-agent invocation routes (`/endpoint/protocols/openai/`,
and — critically — the `activityProtocol` route Teams itself calls) only get
correctly registered in Azure's internal routing/DNS layer if the account is
publicly reachable (`publicNetworkAccess: Enabled`, `networkAcls.defaultAction:
Allow`) at the time the agent is deployed and published. An account created
and left `Disabled` from the start — the normal, secure-by-default state this
repo's Bicep produces — hits a **permanent, not-transient** `403 Traffic is
not from an approved private endpoint` (from inside the VNet) or `404
Subdomain does not map to a resource` (from outside it) on every invocation
attempt, forever, regardless of how the account, Bot Service, or Teams
publish are configured. This isn't a propagation delay — a real deployment
left in this state for hours never recovers. Confirmed by direct A/B test:
the identical agent, identical Bot Service, identical Teams publish call,
failed permanently when the account was `Disabled` from creation, and worked
immediately when the account was flipped `Enabled` right after creation,
before the agent was ever deployed.

The fix costs nothing extra in the end state — it's purely about
**ordering**, and both scripts that touch this handle it automatically:
`deploy_hosted_agent.py` (Step 4) flips the account public before doing
anything else, and leaves it public (Bot Service and Teams publish both
still need it). `publish_teams.py` (also Step 4) flips it back to private as
its last action, once publishing has actually succeeded. You don't need to
run any `az resource update` yourself — pass `--skip-network-toggle` to
either script only if you're deliberately managing this by hand (e.g.
re-running Step 4 after a failed attempt where the account is already
public).

The account is still VNet-injected the whole time (`networkInjections` with
the `agent` scenario pointing at `snet-agent` isn't affected by this
toggle), so its own connection to the Fabric Eventhouse stays on the private
link throughout — only the account's own inbound reachability changes.
Locking back down takes a few minutes to actually take effect (confirmed
live: a direct public call briefly still succeeded right after the toggle,
then started correctly returning `403 Public access is disabled` a few
minutes later) — that's an ordinary propagation delay, not a sign anything
is wrong. Once locked down, real Teams/Bot Service traffic continues working
via the `enable_m365_public_endpoint` exception set during Teams publish;
only the account's general public reachability closes.

### Region choice matters — a lot

**`westus` has a severe, sometimes multi-hour-plus, backend propagation
delay for a freshly created hosted agent's invocation route.** This is
separate from — and on top of — the public-from-creation issue above: even
with the account public from the moment it's created, `westus` repeatedly
took 15–40+ minutes (and on one occasion never resolved at all across a
30-minute retry window) before `responses.create()` stopped returning `404
Subdomain does not map to a resource`. This happened consistently across
several independent full teardown-and-rebuild cycles, ruling out anything
specific to one account, one agent, or leftover state.

**`swedencentral` had none of this.** Same code, same Bicep, same sequence —
the hosted agent responded correctly to a direct query within seconds of
being deployed, on the first attempt, every time. If you're picking a
region and don't have another constraint, use `swedencentral` (that's why
it's the current default in `infra/main.bicepparam`) — or at minimum, avoid
`westus` for this specific workload.

Two regions this repo tried and rejected for unrelated reasons, if you're
choosing your own: **`uksouth`** — this subscription had a hard `0` Fabric
capacity quota there (check first: `az rest --method get --url
"https://management.azure.com/subscriptions/<sub>/providers/Microsoft.Fabric/locations/<region>/usages?api-version=2023-11-01"`
— look for `limit` under `CapacityQuota`, needs to be ≥ 8 for an F8
capacity). **`eastus`** — Operations Agent isn't available there at all
(unrelated to Foundry; a Fabric limitation).

### Testing the agent directly

Don't wait for Bot Service/Teams to find out whether the agent actually
works — test it directly first, from the same machine you ran
`deploy_hosted_agent.py` from:

```python
import sys
sys.path.insert(0, "src")
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from fleetops.foundry._rest import project_endpoint

endpoint = project_endpoint("<foundryAccountName>", "<foundryProjectName>")
with DefaultAzureCredential() as credential:
    project = AIProjectClient(endpoint=endpoint, credential=credential)
    openai_client = project.get_openai_client(agent_name="<agentName>")
    response = openai_client.responses.create(input="What is the most recent event for any vehicle in BusTelemetry?")
    print(response.output_text)
```

A `404 Subdomain does not map to a resource` or `403 Public access is
disabled` here — even though the agent shows `active` and the account shows
`publicNetworkAccess: Enabled` — means the invocation route just isn't
reachable yet. See **Region choice matters** above first: in `swedencentral`
this should succeed immediately (if it doesn't, something's actually wrong —
don't just wait it out). In `westus` this delay is real and can run
15–40+ minutes; retry every minute or two rather than assuming something's
broken. Deleting and recreating the agent does **not** skip this wait
either way, since it's the account's own subdomain/routing registration
that's slow, not anything agent-specific.

### `deploy_hosted_agent.py`: RBAC grant and the ACR fallback

This step also grants the new agent's identity Fabric workspace/Kusto
access, which needs a delegated human session — the jumpbox's own identity
only has enough Foundry permissions for the deploy call itself. If you
haven't done an interactive `az login` on this particular jumpbox container,
that grant will fail with `403 InsufficientPrivileges` (the deploy still
succeeds); re-run just the grant from your own machine — the failure message
tells you exactly how.

Separately, if this step fails with a `ProvisioningError` that doesn't clear
on retry, that's a platform-side issue with the source-upload build path
specifically — fall back to building and pushing an image instead of
building from source:

1. Deploy `infra/modules/foundry.bicep` with `enableContainerRegistry=true`
   (creates a private ACR).
2. `az acr update --name <acrName> --public-network-enabled true` —
   **temporarily makes the registry publicly reachable.** ACR Tasks' own
   build agent runs on Azure's general-purpose IP range, not yours, so
   there's no narrower allowlist option than this for a registry that's
   otherwise private-endpoint-only. Do this only for the few minutes the
   build takes.
3. `az acr build --registry <acrName> --image fleet-incident-agent:v1 --platform linux/amd64 src/fleetops/foundry/hosted_agent`
4. `az acr update --name <acrName> --public-network-enabled false` —
   **put it back.** Don't skip this.
5. `python deploy_hosted_agent.py --image <acrName>.azurecr.io/fleet-incident-agent:v1`

See `deploy_hosted_agent.py`'s module docstring for the same sequence inline
with the code that consumes it.

### `publish_teams.py`: the deep link, and the delegated-token requirement

The script's own log output prints a direct link once publishing succeeds —
`https://teams.microsoft.com/l/app/<teamsAppId>` — that's a real, working
deep link straight to the agent, not something you need to construct
yourself. `titleId` (also printed) isn't enough on its own to build one; you
need `teamsAppId` from the same publish response, which this script captures
into `state.json['teams_publish']`.

This call needs a genuinely delegated (human) token, not the jumpbox's own
managed identity — confirmed live as a `400 AADSTS500016 (... is not
supported as a resource application to execute the flow)` from the
`microsoft365/publish` endpoint specifically, because it does an
on-behalf-of exchange that a managed-identity token can't participate in.
`DefaultAzureCredential` on the jumpbox silently prefers the managed
identity over any interactive `az login` you've done on that container, so
run this one step with `FLEETOPS_FORCE_CLI_CREDENTIAL=1` to force the CLI
credential instead. From your own machine (not the jumpbox),
`DefaultAzureCredential` already falls back to the CLI credential on its own
— no env var needed there.

### Where scripts run from, in detail

Some of these steps are plain Azure/Fabric **control-plane** REST calls,
signed in as yourself — they don't need to run from inside the VNet, so your
own machine is fine (**local**). The rest either test the workspace's
*private* endpoint from inside the network, or call the Foundry account's
own data-plane API when it's not currently public — and genuinely fail with
`403 Public access is disabled` from anywhere but inside the VNet
(**jumpbox**). Copy the repo onto the jumpbox once (`curl`+`tar`, since
`git clone` doesn't reliably complete over `az container exec`), then run
scripts there the same way shown above.

### Operations Agent: why the definition can be reused indefinitely

Its schema isn't publicly documented in enough detail to author one blind,
so this repo captured a real definition from the portal once — the actual
instructions (three monitoring rules over `BusTelemetry`), committed at
`artifacts/ops-agent/OperationsAgentV1.json`. From that point on, every
deployment — including a full teardown-and-rebuild — can create the item via
the documented `POST .../operationsAgents` API using that same file, with no
portal authoring ever needed again: `06_ops_agent.py --apply` (wired into
the `postprovision` hook) does exactly this.

The one thing that *does* change every time the Eventhouse is recreated is
the `dataSources` block's `workspaceId`/`kqlDatabaseId` — those are
per-deployment IDs, not part of the authored config. `06_ops_agent.py`
re-targets them at whatever Eventhouse exists in the current environment on
every `--apply`/`--update` run, so the committed file never goes stale.

To edit the instructions themselves, edit
`artifacts/ops-agent/OperationsAgentV1.json` directly and push with
`06_ops_agent.py --update <opsAgentId>` (the ID is in `state.json`, or in
the portal URL bar). `--capture <id>` still exists for the rare case of
pulling a portal-made edit back into the repo. **Generate Playbook** and
**Start** remain genuinely portal-only actions with no API equivalent at
all — that's why Step 5 above is a manual click.

### Delegated sign-in, generally

Every setup/validation script must run under a real operator's own
`az login` session, not a service principal — the Operations Agent inherits
its creator's identity, and the workspace network lockdown is sensitive
enough that it should always have a human's fingerprints on it.
