-- =============================================================================
-- NVIDIA Nemotron-Mini-4B on SPCS: Infrastructure Setup
-- Run this once with ACCOUNTADMIN (or a role with SYSADMIN + CREATE COMPUTE POOL)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Database & schema
-- ---------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS NEMOTRON_DB;
CREATE SCHEMA  IF NOT EXISTS NEMOTRON_DB.NEMOTRON_SCHEMA;
USE SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA;

-- ---------------------------------------------------------------------------
-- 2. Image repository  (where docker images are stored inside Snowflake)
-- ---------------------------------------------------------------------------
CREATE IMAGE REPOSITORY IF NOT EXISTS NEMOTRON_REPO;

-- Get the registry URL you'll need for docker push/pull:
SHOW IMAGE REPOSITORIES IN SCHEMA NEMOTRON_DB.NEMOTRON_SCHEMA;

-- ---------------------------------------------------------------------------
-- 2b. Stage — holds service-spec.yaml so the deploy SQL can reference it
--     by path rather than inlining the YAML.  push_images.sh uploads the
--     file here; 02_deploy_service.sql reads it via SPECIFICATION_FILE.
-- ---------------------------------------------------------------------------
CREATE STAGE IF NOT EXISTS NEMOTRON_STAGE
    COMMENT = 'Holds service-spec.yaml for NEMOTRON_SERVICE';

-- ---------------------------------------------------------------------------
-- 3. GPU compute pool  — GPU_NV_S = 1x A10G (24 GB VRAM)
--    Perfect for Nemotron-Mini-4B (~8 GB BF16 weights)
-- ---------------------------------------------------------------------------
CREATE COMPUTE POOL IF NOT EXISTS NEMOTRON_GPU_POOL
    MIN_NODES = 1
    MAX_NODES = 1
    INSTANCE_FAMILY = GPU_NV_S
    AUTO_SUSPEND_SECS = 1800   -- suspend after 30mins idle to avoid burning GPU credits
    COMMENT = 'Single A10G GPU pool for Nemotron-Mini-4B NIM inference';

-- Wait for the pool to reach IDLE before creating the service
DESCRIBE COMPUTE POOL NEMOTRON_GPU_POOL;

-- ---------------------------------------------------------------------------
-- 4. Secret — store your NVIDIA NGC API key
--    Replace <YOUR_NGC_API_KEY> with the key from https://org.ngc.nvidia.com/setup/api-key
-- ---------------------------------------------------------------------------
CREATE SECRET IF NOT EXISTS NGC_API_KEY
    TYPE = GENERIC_STRING
    SECRET_STRING = '<YOUR_NGC_API_KEY>'
    COMMENT = 'NVIDIA NGC API key — used by NIM container to pull model weights';

-- ---------------------------------------------------------------------------
-- 5. Network rule — allow the NIM container to reach NVIDIA & HuggingFace
--    to download model weights on first boot
-- ---------------------------------------------------------------------------
CREATE OR REPLACE NETWORK RULE NGC_NETWORK_RULE
    MODE      = EGRESS
    TYPE      = HOST_PORT
    VALUE_LIST = (
        'nvcr.io',
        'authn.nvidia.com',
        'cdn.nvcr.io',
        'api.ngc.nvidia.com',
        'huggingface.co',
        'cdn-lfs.huggingface.co',
        'cdn-lfs-us-1.huggingface.co',
        'cas-bridge.xethub.hf.co',
        'us.aws.cdn.hf.co',
        's3.amazonaws.com',
        's3.us-east-1.amazonaws.com'
    )
    COMMENT = 'Egress for NIM container to download Nemotron model weights';

-- ---------------------------------------------------------------------------
-- 6. External Access Integration — ties network rule + secret together
-- ---------------------------------------------------------------------------
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION NGC_ACCESS
    ALLOWED_NETWORK_RULES        = (NGC_NETWORK_RULE)
    ALLOWED_AUTHENTICATION_SECRETS = (NGC_API_KEY)
    ENABLED = TRUE
    COMMENT = 'Allows NIM service to reach NGC/HuggingFace for model download';

-- ---------------------------------------------------------------------------
-- Done — proceed to scripts/push_images.sh to push containers,
-- then run setup/02_deploy_service.sql
-- ---------------------------------------------------------------------------
