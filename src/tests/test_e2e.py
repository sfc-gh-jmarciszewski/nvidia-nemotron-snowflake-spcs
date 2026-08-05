"""End-to-end tests for NVIDIA Nemotron-Mini-4B on Snowpark Container Services.

Maps directly to the test IDs in src/tests/TEST_PLAN.md.
Run with:
    SNOW_CONNECTION=<your-connection> uv run pytest src/tests/ -v
"""

import json
import pytest


def _sql(conn, query):
    cur = conn.cursor()
    cur.execute(query)
    return cur.fetchall()


# ---------------------------------------------------------------------------
# T-01  Infrastructure Readiness
# ---------------------------------------------------------------------------

def test_compute_pool_ready(conn):
    """T-01: Compute pool is IDLE or ACTIVE."""
    # DESCRIBE COMPUTE POOL returns a single row: (name, state, min_nodes, ...)
    cur = conn.cursor()
    cur.execute("DESCRIBE COMPUTE POOL NEMOTRON_GPU_POOL")
    cols = [d[0].lower() for d in cur.description]
    row = cur.fetchone()
    row_dict = dict(zip(cols, row))
    state = str(row_dict.get("state", ""))
    assert state in ("IDLE", "ACTIVE"), f"Expected IDLE or ACTIVE, got: {state}"


def test_service_running(conn):
    """T-01: NEMOTRON_SERVICE is in RUNNING status."""
    rows = _sql(conn, "SHOW SERVICES IN SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA")
    for row in rows:
        row_str = str(row).upper()
        if "NEMOTRON_SERVICE" in row_str:
            assert "RUNNING" in row_str, f"Expected RUNNING, got: {row}"
            return
    pytest.fail("NEMOTRON_SERVICE not found in schema")


def test_containers_ready(conn):
    """T-01 / T-02 / T-03: Both NIM and translator containers are READY."""
    rows = _sql(conn, "SELECT SYSTEM$GET_SERVICE_STATUS('NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_SERVICE')")
    status = json.loads(rows[0][0])
    containers = {c["containerName"]: c for c in status}
    assert "nim" in containers, "nim container not found in service status"
    assert "translator" in containers, "translator container not found in service status"
    assert containers["nim"]["status"] == "READY", f"nim not READY: {containers['nim']}"
    assert containers["translator"]["status"] == "READY", f"translator not READY: {containers['translator']}"


def test_endpoints_registered(conn):
    """T-01: nim-api endpoint is visible."""
    rows = _sql(conn, "SHOW ENDPOINTS IN SERVICE NEMOTRON_SERVICE")
    endpoint_names = [str(row[0]).lower() for row in rows]
    assert "nim-api" in endpoint_names, f"nim-api endpoint not found: {endpoint_names}"


# ---------------------------------------------------------------------------
# T-04  Smoke Test
# ---------------------------------------------------------------------------

def test_smoke_inference(conn):
    """T-04: NIM_COMPLETE returns a non-empty, non-error response."""
    rows = _sql(conn, "SELECT NIM_COMPLETE('In one sentence, what is Snowpark Container Services?')")
    result = rows[0][0]
    assert result, "NIM_COMPLETE returned empty/null"
    assert not result.startswith("ERROR:"), f"NIM_COMPLETE returned error: {result}"


# ---------------------------------------------------------------------------
# T-05  Multi-Prompt SQL Inference
# ---------------------------------------------------------------------------

def test_multi_prompt_inference(conn):
    """T-05: Batch of 4 prompts — all rows non-null, no ERROR prefix."""
    rows = _sql(conn, """
        SELECT NIM_COMPLETE(prompt)
        FROM (VALUES
          ('What is NVIDIA NIM?'),
          ('Summarize Nemotron-Mini-4B in one sentence.'),
          ('Why is data governance important for enterprise AI?'),
          ('What does TensorRT-LLM optimize?')
        ) AS t(prompt)
    """)
    assert len(rows) == 4, f"Expected 4 rows, got {len(rows)}"
    for i, row in enumerate(rows):
        assert row[0], f"Row {i} returned empty/null"
        assert not row[0].startswith("ERROR:"), f"Row {i} returned error: {row[0]}"


# ---------------------------------------------------------------------------
# T-06  Batch Inference on Table
# ---------------------------------------------------------------------------

def test_batch_on_table(conn):
    """T-06: Batch inference on a temp table returns 4 non-empty taglines."""
    _sql(conn, """
        CREATE OR REPLACE TEMP TABLE DEMO_PRODUCTS (
            product_id INT, product_name VARCHAR, raw_description VARCHAR
        )
    """)
    _sql(conn, """
        INSERT INTO DEMO_PRODUCTS VALUES
          (1, 'Snowflake', 'A cloud-based data warehousing platform.'),
          (2, 'NVIDIA NIM', 'Microservices for AI model deployment.'),
          (3, 'SPCS', 'Containerized workloads inside Snowflake.'),
          (4, 'Nemotron-Mini-4B', 'A 4B-parameter chat LLM from NVIDIA.')
    """)
    rows = _sql(conn, """
        SELECT product_id, NIM_COMPLETE('Write a one-sentence tagline for: ' || raw_description)
        FROM DEMO_PRODUCTS
        ORDER BY product_id
    """)
    assert len(rows) == 4, f"Expected 4 taglines, got {len(rows)}"
    for product_id, tagline in rows:
        assert tagline, f"Empty tagline for product_id={product_id}"
        assert not tagline.startswith("ERROR:"), f"Error tagline for product_id={product_id}: {tagline}"


# ---------------------------------------------------------------------------
# T-08  Model Name Consistency
# ---------------------------------------------------------------------------

def test_model_name_in_logs(conn):
    """T-08: Translator logs confirm auto-discovered model name or successful inference.

    Fetches up to 500 lines. The auto-discovery log fires once at startup, so
    recent-only (50-line) fetches may miss it on a long-running container.
    Falls back to verifying POST /complete 200 in recent logs as proxy.
    """
    rows = _sql(conn, """
        SELECT SYSTEM$GET_SERVICE_LOGS(
            'NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_SERVICE', 0, 'translator', 500
        )
    """)
    logs = rows[0][0]
    assert (
        "hf://nvidia/Nemotron-Mini-4B-Instruct" in logs
        or "Auto-discovered NIM model" in logs
        or 'POST /complete HTTP/1.1" 200' in logs
    ), f"Expected discovery or inference log not found. Logs:\n{logs[:500]}"
