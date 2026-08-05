"""
NIM Translator Sidecar
======================
Bridges Snowflake's SQL service function row format to NVIDIA NIM's OpenAI-compatible API.

Snowflake sends:  {"data": [[row_id, prompt], ...]}
NIM expects:      {"model": "...", "messages": [{"role": "user", "content": prompt}]}
Returns to SF:    {"data": [[row_id, response], ...]}

Also proxies the full OpenAI-compatible API (v1/chat/completions, v1/models, v1/health/*)
so that external clients can call NIM via this sidecar's public endpoint.  This is required
because the NIM container binds only to 127.0.0.1, so the SPCS ingress (which routes from
a separate network interface) cannot reach it directly.  The translator, running in the same
pod network namespace, CAN reach NIM on localhost:8000 and forwards without the Snowflake
auth header (which NIM does not understand).

The NIM container runs on localhost:8000 (shared network namespace in SPCS).
"""

import os
import logging
import requests
from flask import Flask, request, jsonify, make_response, Response, stream_with_context

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

NIM_BASE_URL  = os.getenv("NIM_BASE_URL", "http://localhost:8000")
NIM_MODEL_ENV = os.getenv("NIM_MODEL", "")   # explicit override; empty = auto-discover
MAX_TOKENS    = int(os.getenv("MAX_TOKENS", "1024"))
TIMEOUT_SECS  = int(os.getenv("TIMEOUT_SECS", "120"))

_PASSTHROUGH_HEADERS = {"Content-Type", "Accept", "Accept-Encoding"}

_discovered_model: str | None = None


def _get_model() -> str:
    """Return the model name to use, auto-discovering from /v1/models if not overridden."""
    global _discovered_model
    if NIM_MODEL_ENV:
        return NIM_MODEL_ENV
    if _discovered_model:
        return _discovered_model
    try:
        r = requests.get(f"{NIM_BASE_URL}/v1/models", timeout=15)
        r.raise_for_status()
        models = r.json().get("data", [])
        if models:
            _discovered_model = models[0]["id"]
            logging.info("Auto-discovered NIM model: %s", _discovered_model)
            return _discovered_model
    except Exception as exc:
        logging.warning("Could not auto-discover model from /v1/models: %s", exc)
    return "unknown-model"


def _nim_headers() -> dict:
    """Build headers for forwarding to NIM — strips Snowflake auth."""
    headers = {}
    for h in _PASSTHROUGH_HEADERS:
        val = request.headers.get(h)
        if val:
            headers[h] = val
    return headers


# ---------------------------------------------------------------------------
# SQL service function endpoint
# ---------------------------------------------------------------------------

@app.post("/complete")
def complete():
    """Single-turn completion — maps each prompt to a NIM chat completion."""
    payload = request.get_json(force=True)
    if not payload or "data" not in payload:
        return make_response(jsonify({"error": "expected {\"data\": [[row_id, prompt], ...]}"}), 400)

    model = _get_model()
    results = []
    for row in payload["data"]:
        row_id, prompt = row[0], row[1]
        try:
            resp = requests.post(
                f"{NIM_BASE_URL}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": str(prompt)}],
                    "max_tokens": MAX_TOKENS,
                },
                timeout=TIMEOUT_SECS,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            logging.error("NIM call failed for row %s: %s", row_id, exc)
            content = f"ERROR: {exc}"
        results.append([row_id, content])

    return make_response(jsonify({"data": results}))


# ---------------------------------------------------------------------------
# OpenAI-compatible proxy endpoints
# Strips the Snowflake Authorization header before forwarding to NIM.
# ---------------------------------------------------------------------------

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """Proxy chat completions to NIM, stripping Snowflake auth."""
    body = request.get_data()
    streaming = request.get_json(force=True, silent=True, cache=False) or {}
    is_stream = streaming.get("stream", False)

    if is_stream:
        nim_resp = requests.post(
            f"{NIM_BASE_URL}/v1/chat/completions",
            data=body,
            headers=_nim_headers(),
            stream=True,
            timeout=TIMEOUT_SECS,
        )
        return Response(
            stream_with_context(nim_resp.iter_content(chunk_size=None)),
            status=nim_resp.status_code,
            content_type=nim_resp.headers.get("Content-Type", "text/event-stream"),
        )

    nim_resp = requests.post(
        f"{NIM_BASE_URL}/v1/chat/completions",
        data=body,
        headers=_nim_headers(),
        timeout=TIMEOUT_SECS,
    )
    return make_response(nim_resp.content, nim_resp.status_code, {"Content-Type": "application/json"})


@app.route("/v1/models", methods=["GET"])
def openai_models():
    """Proxy /v1/models from NIM."""
    try:
        nim_resp = requests.get(f"{NIM_BASE_URL}/v1/models", timeout=15)
        return make_response(nim_resp.content, nim_resp.status_code, {"Content-Type": "application/json"})
    except Exception as exc:
        return make_response(jsonify({"error": str(exc)}), 503)


@app.route("/v1/health/ready", methods=["GET"])
def health_ready():
    """Proxy NIM readiness check."""
    try:
        nim_resp = requests.get(f"{NIM_BASE_URL}/v1/health/ready", timeout=10)
        return make_response(nim_resp.content, nim_resp.status_code)
    except Exception as exc:
        return make_response(str(exc), 503)


@app.route("/v1/health/live", methods=["GET"])
def health_live():
    """Proxy NIM liveness check."""
    try:
        nim_resp = requests.get(f"{NIM_BASE_URL}/v1/health/live", timeout=10)
        return make_response(nim_resp.content, nim_resp.status_code)
    except Exception as exc:
        return make_response(str(exc), 503)


# ---------------------------------------------------------------------------
# Legacy / internal endpoints
# ---------------------------------------------------------------------------

@app.get("/models")
def models_legacy():
    """Legacy: proxy /v1/models — kept for backward compat."""
    try:
        nim_resp = requests.get(f"{NIM_BASE_URL}/v1/models", timeout=15)
        return make_response(nim_resp.content, nim_resp.status_code, {"Content-Type": "application/json"})
    except Exception as exc:
        return make_response(jsonify({"error": str(exc)}), 503)


@app.get("/health")
def health():
    """Readiness probe for the translator itself — SPCS routes traffic after this is 200."""
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
