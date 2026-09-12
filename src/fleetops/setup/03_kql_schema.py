#!/usr/bin/env python
"""Apply the BusTelemetry schema: raw landing table, flattened typed table with
column docstrings, the flattening update policy, and retention.

Runs the .kql files in artifacts/kql/01_create_tables.kql, 02_update_policy.kql,
and 03_retention.kql, in that order, against the Eventhouse's KQL database.

The update policy's field mapping is confirmed against live Buses sample
data (Properties.BusState / Properties.TimeToNextStation) — see
artifacts/kql/02_update_policy.kql for the specifics and how they were found.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fleetops.common.config import StateStore, get_settings
from fleetops.common.kusto_client import EventhouseKustoClient
from fleetops.common.logging_setup import setup_logging

logger = setup_logging(__name__)

ARTIFACTS_KQL_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "kql"


def parse_kql_statements(text: str) -> list[str]:
    """Split a .kql file into individual management commands.

    A new statement starts at a line beginning with '.' (a dot-command); every
    following non-comment, non-blank line is appended to it until the next
    dot-command line. '//' comment lines and blank lines are always dropped —
    including ones that trail a statement, like the docstring commentary
    between `.create table` and the next `.alter column` in
    01_create_tables.kql — never glued onto the preceding statement. This is a
    deliberately simple heuristic, not a real KQL parser — it's sufficient for
    the hand-authored files in artifacts/kql/, not for arbitrary KQL scripts.
    """
    statements: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or not stripped:
            continue
        if stripped.startswith("."):
            if current:
                statements.append("\n".join(current).strip())
            current = [line]
        elif current:
            current.append(line)
    if current:
        statements.append("\n".join(current).strip())
    return [s for s in statements if s]


def run_file(client: EventhouseKustoClient, path: Path) -> None:
    logger.info("Applying %s", path.name)
    for statement in parse_kql_statements(path.read_text(encoding="utf-8")):
        client.execute_mgmt(statement)


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()

    state = StateStore()
    eventhouse = state.require("eventhouse", "queryServiceUri", "kqlDatabaseName")

    client = EventhouseKustoClient(eventhouse["queryServiceUri"], eventhouse["kqlDatabaseName"])
    try:
        run_file(client, ARTIFACTS_KQL_DIR / "01_create_tables.kql")
        run_file(client, ARTIFACTS_KQL_DIR / "02_update_policy.kql")
        run_file(client, ARTIFACTS_KQL_DIR / "03_retention.kql")
    finally:
        client.close()

    state.merge("kql_schema", {"applied": True, "tableName": get_settings().table_name})
    logger.info("Schema applied. BusTelemetryRaw/BusTelemetry schemas and the flattening "
                "policy are confirmed against live Buses sample data — see artifacts/kql/ "
                "for the specifics (Properties.BusState / Properties.TimeToNextStation).")


if __name__ == "__main__":
    main()
