"""Pytest fixtures for Nemotron SPCS end-to-end tests.

Usage:
    SNOW_CONNECTION=<your-connection-name> uv run pytest src/tests/ -v
"""

import os
import pytest
import snowflake.connector


@pytest.fixture(scope="session")
def conn():
    connection_name = os.environ.get("SNOW_CONNECTION")
    if not connection_name:
        pytest.skip("Set SNOW_CONNECTION env var to your snow CLI connection name")
    cn = snowflake.connector.connect(connection_name=connection_name)
    cn.cursor().execute("USE SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA")
    yield cn
    cn.close()
