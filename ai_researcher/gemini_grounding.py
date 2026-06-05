from __future__ import annotations

import json
import os
from typing import Any

from .gemini_selector import _access_token, _extract_text, _generate_content_url


def run_grounded_gemini(
    prompt: str,
    config: dict[str, Any],
    *,
    timeout: int | None = None,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    grounding_config = config.get("grounding", {})
    selection_config = config.get("selection", {})
    vertex_config = grounding_config.get("vertex") or selection_config.get("vertex", {})

    model = (
        os.environ.get("GEMINI_GROUNDING_MODEL")
        or grounding_config.get("model")
        or os.environ.get("GEMINI_MODEL")
        or selection_config.get("model")
        or "gemini-3.5-flash"
    )
    project = _env_from_config(vertex_config, "project_env", "GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Gemini grounding")

    location = (
        _env_from_config(vertex_config, "location_env", "GOOGLE_CLOUD_LOCATION")
        or os.environ.get("VERTEX_AI_LOCATION")
        or vertex_config.get("location")
        or vertex_config.get("default_location")
        or "global"
    )
    request_timeout = int(timeout or grounding_config.get("timeout_seconds", 180))
    payload = _build_grounded_payload(
        prompt,
        grounding_config,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        response_mime_type=True,
    )
    url = _generate_content_url(project=project, location=location, model=model)
    token = _access_token()

    fallback_used = False
    try:
        data = _post_vertex(url, token, payload, timeout=request_timeout)
    except RuntimeError as exc:
        if "HTTP 400" not in str(exc):
            raise
        payload = _build_grounded_payload(
            prompt,
            grounding_config,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            response_mime_type=False,
        )
        data = _post_vertex(url, token, payload, timeout=request_timeout)
        fallback_used = True
    if _looks_like_empty_json_text(data):
        payload = _build_grounded_payload(
            prompt,
            grounding_config,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            response_mime_type=False,
        )
        data = _post_vertex(url, token, payload, timeout=request_timeout)
        fallback_used = True

    candidate = (data.get("candidates") or [{}])[0]
    grounding = candidate.get("groundingMetadata") or {}
    return {
        "model": model,
        "location": location,
        "text": _extract_text(data),
        "usage": data.get("usageMetadata") or {},
        "grounding": {
            "queries": list(grounding.get("webSearchQueries") or []),
            "sources": _grounding_sources(grounding),
            "raw": grounding,
        },
        "response_mime_type_fallback": fallback_used,
    }


def _build_grounded_payload(
    prompt: str,
    grounding_config: dict[str, Any],
    *,
    max_output_tokens: int | None,
    temperature: float | None,
    response_mime_type: bool,
) -> dict[str, Any]:
    generation_config: dict[str, Any] = {
        "temperature": float(temperature if temperature is not None else grounding_config.get("temperature", 0.1)),
        "maxOutputTokens": int(max_output_tokens or grounding_config.get("max_output_tokens", 4096)),
    }
    if response_mime_type:
        generation_config["responseMimeType"] = "application/json"
    if "top_p" in grounding_config:
        generation_config["topP"] = float(grounding_config["top_p"])
    if "top_k" in grounding_config:
        generation_config["topK"] = int(grounding_config["top_k"])

    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "tools": [{"googleSearch": {}}],
        "generationConfig": generation_config,
    }


def _post_vertex(url: str, token: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests is required for Gemini grounding") from exc

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
        raise RuntimeError(f"Gemini grounding request failed: HTTP {response.status_code}: {response.text[:800]}")
    return response.json()


def _grounding_sources(grounding: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for chunk in grounding.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        uri = str(web.get("uri") or web.get("url") or "").strip()
        title = str(web.get("title") or "").strip()
        domain = str(web.get("domain") or "").strip()
        if uri or title:
            sources.append({"uri": uri, "title": title, "domain": domain})
    return sources[:20]


def _looks_like_empty_json_text(data: dict[str, Any]) -> bool:
    try:
        text = _extract_text(data)
    except RuntimeError:
        return False
    return text.strip() in {"{}", "[]"}


def _env_from_config(config: dict[str, Any], key: str, default_env: str) -> str | None:
    env_name = config.get(key) or default_env
    return os.environ.get(str(env_name))
