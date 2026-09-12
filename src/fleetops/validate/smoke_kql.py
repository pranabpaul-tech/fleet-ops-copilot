#!/usr/bin/env python
"""Smoke-test the Eventhouse: row counts, ingestion freshness, and the two
reference KQL queries from the design doc (artifacts/kql/investigate_vehicle.kql,
route_health.kql — with the hardcoded VehicleId/RouteId parameterized here).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fleetops.common.config import StateStore
from fleetops.common.kusto_client import EventhouseKustoClient
from fleetops.common.logging_setup import setup_logging

logger = setup_logging(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vehicle-id", default=None, help="Defaults to the first VehicleId seen in the last 20 minutes.")
    parser.add_argument("--route-id", default=None, help="Defaults to the first RouteId seen in the last 30 minutes.")
    parser.add_argument("--freshness-minutes", type=int, default=10,
                         help="Fail if no row has IngestionTime within this many minutes.")
    args = parser.parse_args()

    state = StateStore()
    eventhouse = state.require("eventhouse", "queryServiceUri", "kqlDatabaseName")
    client = EventhouseKustoClient(eventhouse["queryServiceUri"], eventhouse["kqlDatabaseName"])

    ok = True
    try:
        raw_count = client.query_to_dicts("BusTelemetryRaw | count")[0]["Count"]
        flat_count = client.query_to_dicts("BusTelemetry | count")[0]["Count"]
        logger.info("BusTelemetryRaw rows: %s, BusTelemetry rows: %s", raw_count, flat_count)
        if raw_count == 0:
            logger.error("BusTelemetryRaw is empty — the Eventstream isn't landing any events. "
                          "Check setup/04_eventstream.py and the Eventstream's publish status in the portal.")
            ok = False
        elif flat_count == 0:
            logger.error("BusTelemetryRaw has rows but BusTelemetry is empty — the flattening update policy "
                          "likely doesn't match the real event shape. See artifacts/kql/02_update_policy.kql.")
            ok = False

        freshness = client.query_to_dicts(
            f"BusTelemetry | summarize MaxIngestionTime = max(IngestionTime) "
            f"| extend AgeMinutes = datetime_diff('minute', now(), MaxIngestionTime)"
        )
        if freshness and freshness[0].get("AgeMinutes") is not None:
            age = freshness[0]["AgeMinutes"]
            logger.info("Most recent row is %s minute(s) old", age)
            if age > args.freshness_minutes:
                logger.error("Freshness check failed: newest row is %s min old, threshold is %s min",
                             age, args.freshness_minutes)
                ok = False

        vehicle_id = args.vehicle_id
        if vehicle_id is None:
            rows = client.query_to_dicts("BusTelemetry | where EventTime > ago(20m) | take 1 | project VehicleId")
            vehicle_id = rows[0]["VehicleId"] if rows else None

        if vehicle_id:
            timeline = client.query_to_dicts(
                f"BusTelemetry | where EventTime > ago(20m) | where VehicleId == '{vehicle_id}' "
                f"| project EventTime, VehicleId, RouteId, SpeedKph, EngineTemperatureC, DelayMinutes, Status "
                f"| order by EventTime asc"
            )
            logger.info("investigate_vehicle.kql for %s returned %d rows", vehicle_id, len(timeline))
        else:
            logger.warning("No recent VehicleId found — skipping investigate_vehicle.kql check.")

        route_id = args.route_id
        if route_id is None:
            rows = client.query_to_dicts("BusTelemetry | where EventTime > ago(30m) | take 1 | project RouteId")
            route_id = rows[0]["RouteId"] if rows else None

        if route_id:
            health = client.query_to_dicts(
                f"BusTelemetry | where EventTime > ago(30m) | where RouteId == '{route_id}' "
                f"| summarize Vehicles = dcount(VehicleId), AverageDelay = avg(DelayMinutes), "
                f"StoppedVehicles = dcountif(VehicleId, SpeedKph == 0) by bin(EventTime, 5m) "
                f"| order by EventTime asc"
            )
            logger.info("route_health.kql for route %s returned %d bins", route_id, len(health))
        else:
            logger.warning("No recent RouteId found — skipping route_health.kql check.")
    finally:
        client.close()

    if not ok:
        sys.exit(1)
    logger.info("smoke_kql: PASS")


if __name__ == "__main__":
    main()
