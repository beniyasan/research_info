from __future__ import annotations

import json
import os
from typing import Any


VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def run_vertex_gemini(prompt: str, selection_config: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    vertex_config = selection_config.get("vertex", {})
    model = (
        os.environ.get("GEMINI_MODEL")
        or os.environ.get("VERTEX_GEMINI_MODEL")
        or selection_config.get("model")
        or "gemini-3.5-flash"
    )
    project = _env_from_config(vertex_config, "project_env", "GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Vertex AI Gemini selection")

    location = (
        _env_from_config(vertex_config, "location_env", "GOOGLE_CLOUD_LOCATION")
        or os.environ.get("VERTEX_AI_LOCATION")
        or vertex_config.get("location")
        or vertex_config.get("default_location")
        or "global"
    )

    payload = _build_payload(prompt, selection_config)
    url = _generate_content_url(project=project, location=location, model=model)
    token = _access_token()

    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests is required for Vertex AI Gemini selection") from exc

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ai-researcher/0.1",
        },
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Vertex AI Gemini request failed: HTTP {response.status_code}: {response.text[:800]}")

    data = response.json()
    return {
        "model": model,
        "location": location,
        "text": _extract_text(data),
        "usage": data.get("usageMetadata") or {},
    }


def _env_from_config(config: dict[str, Any], key: str, default_env: str) -> str | None:
    env_name = config.get(key) or default_env
    return os.environ.get(str(env_name))


def _build_payload(prompt: str, selection_config: dict[str, Any]) -> dict[str, Any]:
    generation_config: dict[str, Any] = {
        "temperature": float(selection_config.get("temperature", 0.2)),
        "maxOutputTokens": int(selection_config.get("max_output_tokens", 8192)),
        "responseMimeType": "application/json",
    }
    if "top_p" in selection_config:
        generation_config["topP"] = float(selection_config["top_p"])
    if "top_k" in selection_config:
        generation_config["topK"] = int(selection_config["top_k"])

    thinking_level = os.environ.get("GEMINI_THINKING_LEVEL") or selection_config.get("thinking_level")
    if thinking_level:
        generation_config["thinkingConfig"] = {"thinkingLevel": str(thinking_level).upper()}

    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": generation_config,
    }


def _generate_content_url(*, project: str, location: str, model: str) -> str:
    if location == "global":
        host = "aiplatform.googleapis.com"
    else:
        host = f"{location}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )


def _access_token() -> str:
    try:
        import google.auth
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise RuntimeError("google-auth is required for Vertex AI Gemini selection") from exc

    credentials, _ = google.auth.default(scopes=[VERTEX_SCOPE])
    credentials.refresh(Request())
    if not credentials.token:
        raise RuntimeError("Could not obtain a Google Cloud access token")
    return credentials.token


def _extract_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Vertex AI Gemini returned no candidates: {json.dumps(data, ensure_ascii=False)[:800]}")

    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(str(part.get("text") or "") for part in parts)
    if not text.strip():
        finish_reason = candidates[0].get("finishReason")
        raise RuntimeError(f"Vertex AI Gemini returned empty text, finishReason={finish_reason}")
    return text.strip()
