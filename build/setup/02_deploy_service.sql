-- =============================================================================
-- NVIDIA Nemotron-Mini-4B on SPCS: Deploy Service & SQL Function
-- Run AFTER scripts/push_images.sh has completed successfully.
-- =============================================================================

USE SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA;

-- ---------------------------------------------------------------------------
-- 1. Create the SPCS service
--    Two containers share a pod: NIM (GPU inference) + translator (SQL bridge)
--    Spec is read from the stage — edit service-spec.yaml, re-run push_images.sh
--    to upload it, then re-run this file (or ALTER SERVICE below).
-- ---------------------------------------------------------------------------
CREATE SERVICE IF NOT EXISTS NEMOTRON_SERVICE
    IN COMPUTE POOL NEMOTRON_GPU_POOL
    EXTERNAL_ACCESS_INTEGRATIONS = (NGC_ACCESS)
    MIN_INSTANCES = 1
    MAX_INSTANCES = 1
    FROM SPECIFICATION_FILE = '@NEMOTRON_STAGE/service-spec.yaml';

-- ---------------------------------------------------------------------------
-- 2. Monitor startup — first boot takes 5–15 min (TRT engine compilation)
--    Re-run until status shows READY
-- ---------------------------------------------------------------------------
SELECT SYSTEM$GET_SERVICE_STATUS('NEMOTRON_SERVICE');

-- Get the public endpoint URL (use this in Python notebooks / external apps)
SHOW ENDPOINTS IN SERVICE NEMOTRON_SERVICE;

-- View container logs if anything looks wrong
-- SELECT SYSTEM$GET_SERVICE_LOGS('NEMOTRON_SERVICE', 0, 'nim', 100);
-- SELECT SYSTEM$GET_SERVICE_LOGS('NEMOTRON_SERVICE', 0, 'translator', 50);

-- ---------------------------------------------------------------------------
-- 3. SQL Service Function
--    Calls the translator sidecar on port 5000 (internal endpoint).
--    Use exactly like any native Snowflake scalar function.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION NIM_COMPLETE(prompt TEXT)
    RETURNS TEXT
    SERVICE = NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_SERVICE
    ENDPOINT = 'nim-api'
    AS '/complete';

-- ---------------------------------------------------------------------------
-- 4. Quick smoke test
-- ---------------------------------------------------------------------------
SELECT NIM_COMPLETE('In one sentence, what is Snowpark Container Services?');

-- ---------------------------------------------------------------------------
-- 5. Grant access to consumers
--    Replace DATA_SCIENTIST with the role(s) that should call this model
-- ---------------------------------------------------------------------------
GRANT USAGE ON DATABASE NEMOTRON_DB TO ROLE DATA_SCIENTIST;
GRANT USAGE ON SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA TO ROLE DATA_SCIENTIST;
GRANT USAGE ON FUNCTION NEMOTRON_DB.NEMOTRON_SCHEMA.NIM_COMPLETE(TEXT) TO ROLE DATA_SCIENTIST;
GRANT SERVICE ROLE NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_SERVICE!ALL_ENDPOINTS_USAGE TO ROLE DATA_SCIENTIST;

-- ---------------------------------------------------------------------------
-- Operations: suspend/resume compute pool to control GPU costs
-- ---------------------------------------------------------------------------
-- ALTER COMPUTE POOL NEMOTRON_GPU_POOL SUSPEND;
-- ALTER COMPUTE POOL NEMOTRON_GPU_POOL RESUME;
