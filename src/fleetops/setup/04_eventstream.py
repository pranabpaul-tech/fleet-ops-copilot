#!/usr/bin/env python
"""Create the Eventstream from artifacts/eventstream.definition.json, or capture
a portal-authored Eventstream's real definition down into that file.

Workflow (see the file's own _note field):
  1. Author the Eventstream once in the Fabric portal — Buses sample source,
     Eventhouse destination pointed at BusTelemetryRaw, ProcessedIngestion mode.
  2. python 04_eventstream.py --capture <eventstreamId>
     writes the real definition over the placeholder.
  3. On any later environment: python 04_eventstream.py --apply
     creates the Eventstream from that captured file.

--apply refuses to run while the file still has "_placeholder": true, so a
fresh checkout can't silently create a broken Eventstream from the guessed shape.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fleetops.common.config import StateStore, get_settings
from fleetops.common.fabric_client import FabricClient, decode_definition_part, definition_part
from fleetops.common.logging_setup import setup_logging

logger = setup_logging(__name__)

DEFINITION_PATH = Path(__file__).resolve().parents[3] / "artifacts" / "eventstream.definition.json"
DEFINITION_ITEM_PATH = "eventstream.json"  # the part path Fabric expects inside the definition


def capture(client: FabricClient, workspace_id: str, eventstream_id: str) -> None:
    logger.info("Fetching definition for eventstream %s...", eventstream_id)
    result = client.call("POST", f"/workspaces/{workspace_id}/eventstreams/{eventstream_id}/getDefinition")
    parts = result.get("definition", {}).get("parts", [])
    part = next((p for p in parts if p["path"] == DEFINITION_ITEM_PATH), None)
    if part is None:
        raise RuntimeError(f"getDefinition response had no '{DEFINITION_ITEM_PATH}' part. Parts present: "
                            f"{[p.get('path') for p in parts]}")
    decoded = decode_definition_part(part)
    DEFINITION_PATH.write_text(json.dumps(decoded, indent=2), encoding="utf-8")
    logger.info("Captured real definition -> %s. Review it, then commit it.", DEFINITION_PATH)


def update(client: FabricClient, workspace_id: str, eventstream_id: str, state: StateStore) -> None:
    """Push artifacts/eventstream.definition.json back to an EXISTING (portal-
    created) Eventstream — used after editing a captured definition locally
    (e.g. flipping dataIngestionMode from the portal's DirectIngestion default
    to ProcessedIngestion) rather than redoing the edit by hand in the portal."""
    raw = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
    if raw.get("_placeholder"):
        raise RuntimeError(f"{DEFINITION_PATH} is still the placeholder shape — nothing real to push.")
    clean = {k: v for k, v in raw.items() if not k.startswith("_")}
    _retarget_eventhouse_destinations(clean, state)

    logger.info("Updating eventstream %s from %s...", eventstream_id, DEFINITION_PATH)
    client.call("POST", f"/workspaces/{workspace_id}/eventstreams/{eventstream_id}/updateDefinition", {
        "definition": {
            "parts": [definition_part(DEFINITION_ITEM_PATH, clean)],
        },
    })
    settings = get_settings()
    state.merge("eventstream", {
        "eventstreamId": eventstream_id,
        "eventstreamName": settings.eventstream_name,
    })
    logger.info("Done. state.json['eventstream'] updated.")
    logger.info("Now confirm rows are landing: BusTelemetryRaw | take 5 (from validate/smoke_kql.py or the portal).")


def _retarget_eventhouse_destinations(definition: dict, state: StateStore) -> None:
    """The captured definition's Eventhouse destination(s) hardcode the
    workspaceId/itemId/databaseName of whichever Eventhouse was live at
    capture time — those are per-deployment IDs, not part of the topology
    shape, and go stale the moment that Eventhouse is ever recreated (a fresh
    `azd provision`, a torn-down-and-rebuilt environment, ...). Overwrite them
    with the current state.json values on every apply, in place.

    `itemId` here must be the KQL *database's* item ID, not the Eventhouse
    item's — despite the destination `type` being "Eventhouse". Confirmed
    live: passing the Eventhouse's own item ID fails with
    "Unable to extract cluster URL from the Eventhouse KQL database item ID
    ...", since Fabric resolves the destination's cluster URL from the
    database item specifically.
    """
    eventhouse = state.require("eventhouse", "kqlDatabaseId", "kqlDatabaseName")
    workspace_id = state.output("workspace", "workspaceId")
    for destination in definition.get("destinations", []):
        if destination.get("type") == "Eventhouse":
            destination["properties"]["workspaceId"] = workspace_id
            destination["properties"]["itemId"] = eventhouse["kqlDatabaseId"]
            destination["properties"]["databaseName"] = eventhouse["kqlDatabaseName"]


def apply(client: FabricClient, workspace_id: str, state: StateStore) -> None:
    raw = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
    if raw.get("_placeholder"):
        raise RuntimeError(
            f"{DEFINITION_PATH} is still the placeholder shape. Author the Eventstream once in the "
            f"portal and run `python 04_eventstream.py --capture <eventstreamId>` first — see this "
            f"script's module docstring."
        )
    # Strip our own bookkeeping keys before handing the definition to Fabric.
    clean = {k: v for k, v in raw.items() if not k.startswith("_")}
    _retarget_eventhouse_destinations(clean, state)

    settings = get_settings()
    existing = state.get("eventstream")
    if existing and existing.get("eventstreamId"):
        logger.info("Eventstream already recorded in state.json — nothing to do.")
        return

    logger.info("Creating Eventstream '%s' from %s...", settings.eventstream_name, DEFINITION_PATH)
    result = client.call("POST", f"/workspaces/{workspace_id}/eventstreams", {
        "displayName": settings.eventstream_name,
        "definition": {
            "parts": [definition_part(DEFINITION_ITEM_PATH, clean)],
        },
    })
    eventstream_id = result["id"]
    state.merge("eventstream", {
        "eventstreamId": eventstream_id,
        "eventstreamName": settings.eventstream_name,
    })
    logger.info("Done. state.json['eventstream'] updated. eventstreamId=%s", eventstream_id)
    logger.info("Now confirm rows are landing: BusTelemetryRaw | take 5 (from validate/smoke_kql.py or the portal).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true", help="Create the Eventstream from artifacts/eventstream.definition.json")
    mode.add_argument("--capture", metavar="EVENTSTREAM_ID", help="Pull a portal-authored Eventstream's real definition into artifacts/eventstream.definition.json")
    mode.add_argument("--update", metavar="EVENTSTREAM_ID", help="Push a locally-edited artifacts/eventstream.definition.json back to an existing (portal-created) Eventstream")
    args = parser.parse_args()

    state = StateStore()
    workspace_id = state.output("workspace", "workspaceId")
    client = FabricClient()

    if args.capture:
        capture(client, workspace_id, args.capture)
    elif args.update:
        update(client, workspace_id, args.update, state)
    else:
        apply(client, workspace_id, state)


if __name__ == "__main__":
    main()
