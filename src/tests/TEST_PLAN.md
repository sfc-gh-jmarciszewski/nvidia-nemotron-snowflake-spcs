# NVIDIA Nemotron-Mini-4B on SPCS — End-to-End Test Plan

## Architecture Under Test

```
Snowflake SQL
  → NIM_COMPLETE() service function
    → translator sidecar (port 5000, Flask)
      → NIM container (port 8000, OpenAI-compatible API)
        → nvidia/Nemotron-Mini-4B-Instruct (TensorRT-LLM)
```

---

## Root Cause & Fix

**Root cause:** The `model-free-nim` container (vLLM v0.25.1) registers its model
with the **full HuggingFace URI including the `hf://` scheme prefix** —
`hf://nvidia/Nemotron-Mini-4B-Instruct` — not the bare model ID
`nvidia/Nemotron-Mini-4B-Instruct`. Any request using the bare ID returns HTTP 404.

The original service spec set `NIM_MODEL: "nvidia/Nemotron-Mini-4B-Instruct"` in
the translator, which caused every `NIM_COMPLETE()` call to fail.

**Investigation steps taken:**
1. `NIM_MODEL=nvidia/Nemotron-Mini-4B-Instruct` → 404 `The model does not exist`
2. `NIM_MODEL=ga-model-free-nim` (notebook hint) → 404 `The model does not exist`
3. Added `/models` proxy endpoint to translator; called auto-discovery
4. Discovered actual name: `hf://nvidia/Nemotron-Mini-4B-Instruct`

**Fix applied:**
- `docker/translator/translator.py` redesigned with `_get_model()` helper that
  auto-discovers from `GET /v1/models` on first call (no `NIM_MODEL` env var needed)
- Added `/models` proxy endpoint to translator for diagnostics
- Removed `NIM_MODEL` env var from `service-spec.yaml` and `setup/02_deploy_service.sql`
  (auto-discovery is the default; correct name documented in comments)
- Translator image rebuilt and pushed (digest `sha256:561778...`)
- `ALTER SERVICE` applied — no `NIM_MODEL` override, auto-discovery in effect

---

## Test Suite

### T-01  Infrastructure Readiness

| Check | SQL | Expected |
|---|---|---|
| Compute pool state | `DESCRIBE COMPUTE POOL NEMOTRON_GPU_POOL` | `state = IDLE or ACTIVE` |
| Service status | `SHOW SERVICES IN SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA` | `status = RUNNING` |
| Container readiness | `SYSTEM$GET_SERVICE_STATUS(...)` | both `nim` and `translator` containers `ready=True` |
| Endpoints registered | `SHOW ENDPOINTS IN SERVICE NEMOTRON_SERVICE` | `nim-api` (public) and `sql-api` (internal) visible |

### T-02  Translator Health Probe

| Check | Method | Expected |
|---|---|---|
| `/health` responds | Internal — verified via service readiness | HTTP 200 `OK` |

### T-03  NIM Health Probe

| Check | Method | Expected |
|---|---|---|
| `/v1/health/ready` responds | Internal — verified via container readiness | HTTP 200 |

### T-04  Smoke Test — SQL Service Function

```sql
SELECT NIM_COMPLETE('In one sentence, what is Snowpark Container Services?');
```

**Expected:** Non-null, non-empty string that is not prefixed with `ERROR:`.
**Prior failure:** `ERROR: 404 Client Error: Not Found for url: http://localhost:8000/v1/chat/completions`
**Root cause:** Wrong model name (`nvidia/Nemotron-Mini-4B-Instruct` vs `ga-model-free-nim`).

### T-05  Multi-Prompt SQL Inference

```sql
SELECT NIM_COMPLETE(prompt)
FROM (VALUES
  ('What is NVIDIA NIM?'),
  ('Summarize Nemotron-Mini-4B in one sentence.'),
  ('Why is data governance important for enterprise AI?'),
  ('What does TensorRT-LLM optimize?')
) AS t(prompt);
```

**Expected:** 4 rows returned, all non-null, no `ERROR:` prefix.

### T-06  Batch Inference on Table

```sql
CREATE OR REPLACE TEMP TABLE DEMO_PRODUCTS (
    product_id INT, product_name VARCHAR, raw_description VARCHAR
);
INSERT INTO DEMO_PRODUCTS VALUES
  (1, 'Snowflake', 'A cloud-based data warehousing platform.'),
  (2, 'NVIDIA NIM', 'Microservices for AI model deployment.'),
  (3, 'SPCS', 'Containerized workloads inside Snowflake.'),
  (4, 'Nemotron-Mini-4B', 'A 4B-parameter chat LLM from NVIDIA.');

SELECT product_id, product_name,
  NIM_COMPLETE('Write a one-sentence tagline for: ' || raw_description) AS tagline
FROM DEMO_PRODUCTS ORDER BY product_id;
```

**Expected:** 4 rows, each with a non-empty tagline string.

### T-07  Python REST Inference (OpenAI SDK via Public Endpoint)

Executed from `notebooks/nemotron_demo.ipynb`, cell `python_inference_cell`.

```python
client.chat.completions.create(
    model="ga-model-free-nim",
    messages=[{"role": "user", "content": "..."}],
    max_tokens=512,
)
```

**Expected:** `response.choices[0].message.content` is non-empty, `response.usage.total_tokens > 0`.

### T-08  Model Name Consistency Check

```sql
-- Confirm the registered model name matches what the translator sends
SELECT SYSTEM$GET_SERVICE_STATUS('NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_SERVICE');
```

Inspect translator logs to confirm requests use `ga-model-free-nim`:
```sql
SELECT SYSTEM$GET_SERVICE_LOGS('NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_SERVICE', 0, 'translator', 50);
```

**Expected:** Log lines show `POST /complete 200`, `Auto-discovered NIM model: hf://nvidia/Nemotron-Mini-4B-Instruct`, no 404 errors.

---

## Test Results (2026-07-28)

| Test | Status | Notes |
|---|---|---|
| T-01 Infrastructure Readiness | **PASS** | Pool ACTIVE (1 A10G node), service RUNNING, both containers READY |
| T-02 Translator Health | **PASS** | `GET /health` → 200 every 5s, confirmed in logs |
| T-03 NIM Health | **PASS** | `GET /v1/health/ready` → 200, container status READY |
| T-04 Smoke Test | **PASS** | Was: `404 Not Found`. Fix: auto-discover model name `hf://nvidia/Nemotron-Mini-4B-Instruct` |
| T-05 Multi-Prompt SQL | **PASS** | 4 rows returned, all non-null, no ERROR prefix |
| T-06 Batch on Table | **PASS** | 4 taglines generated for DEMO_PRODUCTS |
| T-07 Python REST | SKIP | Requires Snowsight notebook session — run `notebooks/nemotron_demo.ipynb` manually |
| T-08 Model Name Check | **PASS** | Translator logs: `Auto-discovered NIM model: hf://nvidia/Nemotron-Mini-4B-Instruct` |

---

## Running the Tests

```sql
-- Run all SQL tests in order
USE SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA;

-- T-01
DESCRIBE COMPUTE POOL NEMOTRON_GPU_POOL;
SHOW SERVICES IN SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA;
SELECT SYSTEM$GET_SERVICE_STATUS('NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_SERVICE');
SHOW ENDPOINTS IN SERVICE NEMOTRON_SERVICE;

-- T-04
SELECT NIM_COMPLETE('In one sentence, what is Snowpark Container Services?');

-- T-05
SELECT NIM_COMPLETE(prompt)
FROM (VALUES
  ('What is NVIDIA NIM?'),
  ('Summarize Nemotron-Mini-4B in one sentence.'),
  ('Why is data governance important for enterprise AI?'),
  ('What does TensorRT-LLM optimize?')
) AS t(prompt);

-- T-06
CREATE OR REPLACE TEMP TABLE DEMO_PRODUCTS (
    product_id INT, product_name VARCHAR, raw_description VARCHAR
);
INSERT INTO DEMO_PRODUCTS VALUES
  (1, 'Snowflake', 'A cloud-based data warehousing platform.'),
  (2, 'NVIDIA NIM', 'Microservices for AI model deployment.'),
  (3, 'SPCS', 'Containerized workloads inside Snowflake.'),
  (4, 'Nemotron-Mini-4B', 'A 4B-parameter chat LLM from NVIDIA.');
SELECT product_id, product_name,
  NIM_COMPLETE('Write a one-sentence tagline for: ' || raw_description) AS tagline
FROM DEMO_PRODUCTS ORDER BY product_id;

-- T-08 logs check
SELECT SYSTEM$GET_SERVICE_LOGS('NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_SERVICE', 0, 'translator', 50);
```

For T-07 (Python REST), run `notebooks/nemotron_demo.ipynb` in Snowsight.

---

## Cost Controls

```sql
-- Run after all tests pass:
ALTER COMPUTE POOL NEMOTRON_GPU_POOL SUSPEND;
```

All compute pools are listed in `SHOW COMPUTE POOLS`. Only `NEMOTRON_GPU_POOL` needs
to be actively managed for this demo — it is the only GPU pool and bills per second
while `ACTIVE`.
