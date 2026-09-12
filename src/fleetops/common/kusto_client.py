"""Thin wrapper over azure-kusto-data for running KQL management commands and
queries against an Eventhouse's KQL database.
"""
from __future__ import annotations

from azure.kusto.data import KustoConnectionStringBuilder, KustoClient

from .auth import get_token_provider
from .logging_setup import setup_logging

logger = setup_logging(__name__)


class EventhouseKustoClient:
    def __init__(self, query_service_uri: str, database_name: str):
        self.database_name = database_name
        token_provider = get_token_provider()
        # with_azure_token_credential takes a TokenCredential and handles the
        # cluster's own per-cluster audience/scope internally — no fixed scope
        # string needed here.
        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
            connection_string=query_service_uri,
            credential=token_provider.get_credential(),
        )
        self._client = KustoClient(kcsb)

    def execute_mgmt(self, command: str):
        logger.info("KQL mgmt: %s", command.strip().splitlines()[0][:120])
        return self._client.execute_mgmt(self.database_name, command)

    def execute_query(self, query: str):
        return self._client.execute_query(self.database_name, query)

    def query_to_dicts(self, query: str) -> list[dict]:
        result = self.execute_query(query)
        table = result.primary_results[0]
        columns = [c.column_name for c in table.columns]
        return [dict(zip(columns, row)) for row in table.rows]

    def close(self):
        self._client.close()
