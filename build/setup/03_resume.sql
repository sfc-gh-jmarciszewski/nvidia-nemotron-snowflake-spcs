-- ── resume services  ───────────────────────────────────────────────────────────────
USE SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA;

-- ── 1. Check current state ─────────────────────────────────────────────────
DESCRIBE COMPUTE POOL NEMOTRON_GPU_POOL;
SELECT SYSTEM$GET_SERVICE_STATUS('NEMOTRON_SERVICE');

-- ── 2. Resume — compute pool MUST come first (service depends on it) ────────
ALTER COMPUTE POOL NEMOTRON_GPU_POOL RESUME;
ALTER SERVICE NEMOTRON_SERVICE RESUME;

-- ── 3. Confirm — re-run until pool = IDLE/ACTIVE and service = READY ────────
-- First boot after resume takes 5–15 min (TRT engine recompilation)
DESCRIBE COMPUTE POOL NEMOTRON_GPU_POOL;
SELECT SYSTEM$GET_SERVICE_STATUS('NEMOTRON_SERVICE');
