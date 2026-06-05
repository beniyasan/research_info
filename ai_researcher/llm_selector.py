from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from typing import Any

from .gemini_selector import run_vertex_gemini


LLM_COMMAND_ENV = "AI_RESEARCH_LLM_COMMAND"


def select_report_articles(
    candidate_rows,
    config: dict[str, Any],
    *,
    period: str,
    label: str,
    max_articles: int,
) -> dict[str, Any]:
    candidates = [_row_to_dict(row) for row in candidate_rows]
    selection_config = config.get("selection", {})
    mode = selection_config.get("mode", "llm_if_available")
    provider = selection_config.get("provider", "command")

    if mode in {"heuristic", "score"}:
        return _heuristic_selection(candidates, max_articles, "heuristic")

    prompt = _build_prompt(candidates, period=period, label=label, max_articles=max_articles)
    timeout = int(selection_config.get("timeout_seconds", 180))

    if provider in {"vertex_gemini", "gemini_vertex"}:
        try:
            result = run_vertex_gemini(prompt, selection_config, timeout=timeout)
            method = f"vertex_gemini:{result['model']}"
            selected = _parse_llm_selection(result["text"], candidates, max_articles, method=method)
            if not selected:
                raise RuntimeError("Vertex AI Gemini returned no valid selected article IDs")
            return {
                "method": method,
                "status": "ok",
                "selected": selected,
                "raw_output": result["text"][:2000],
                "reason": f"usage={json.dumps(result.get('usage') or {}, ensure_ascii=False)}",
            }
        except Exception as exc:
            if mode == "llm":
                raise
            result = _heuristic_selection(candidates, max_articles, "heuristic_fallback")
            result["status"] = "fallback"
            result["reason"] = f"Vertex AI Gemini selection failed: {exc}"
            return result

    command_env = selection_config.get("command_env", LLM_COMMAND_ENV)
    command = os.environ.get(command_env)
    if not command:
        command = selection_config.get("command")
    if not command:
        if mode == "llm":
            raise RuntimeError(f"{command_env} is required when selection.mode is 'llm'")
        result = _heuristic_selection(candidates, max_articles, "heuristic_fallback")
        result["status"] = "fallback"
        result["reason"] = f"{command_env} not set and selection.command is empty"
        return result

    try:
        output = _run_llm_command(command, prompt, timeout=timeout)
        selected = _parse_llm_selection(output, candidates, max_articles, method="llm")
        if not selected:
            raise RuntimeError("LLM returned no valid selected article IDs")
        return {
            "method": "llm",
            "status": "ok",
            "selected": selected,
            "raw_output": output[:2000],
        }
    except Exception as exc:
        if mode == "llm":
            raise
        result = _heuristic_selection(candidates, max_articles, "heuristic_fallback")
        result["status"] = "fallback"
        result["reason"] = str(exc)
        return result


def _heuristic_selection(candidates: list[dict[str, Any]], max_articles: int, method: str) -> dict[str, Any]:
    selected = []
    for row in candidates[:max_articles]:
        selected.append(
            {
                **row,
                "selection_method": method,
                "selection_reason": "Selected by relevance score fallback.",
                "translated_title": None,
                "translated_summary": None,
            }
        )
    return {"method": method, "status": "ok", "selected": selected}


def _build_prompt(candidates: list[dict[str, Any]], *, period: str, label: str, max_articles: int) -> str:
    compact_articles = []
    for row in candidates:
        compact_articles.append(
            {
                "id": row["id"],
                "source": row["source_name"],
                "category": row["source_category"],
                "score": round(float(row["score"]), 2),
                "title": row["title"],
                "summary": _truncate(row.get("summary") or "", 700),
                "keywords": json.loads(row.get("matched_keywords") or "[]")[:12],
                "url": row["url"],
            }
        )

    return f"""
You are selecting articles for a Japanese AI engineering research report.

Report period: {period} {label}
Maximum selected articles: {max_articles}

Selection policy:
- Choose articles that contribute to the final report, not merely articles with many keyword hits.
- Prefer practical AI engineering, AI agents, LLM application architecture, model releases, safety/evals, research with engineering implications, and official vendor updates.
- Avoid near-duplicates. If several articles cover the same announcement, select the most primary or useful one.
- Include a balanced mix of Japan tech community, official vendor updates, research, and overseas news when useful.
- For non-Japanese articles, provide a Japanese title and Japanese summary. The report will also keep the original title and original summary.
- For Japanese articles, you may keep the title, but still write a concise Japanese summary.
- Do not browse the web, inspect files, or run commands. Use only the candidate articles below.

Return JSON only. Do not include Markdown fences.
Schema:
{{
  "selected": [
    {{
      "id": "article id from input",
      "reason": "why this article contributes to the report, in Japanese",
      "japanese_title": "Japanese title or natural Japanese rendering",
      "japanese_summary": "2-4 sentence Japanese summary focused on why it matters"
    }}
  ]
}}

Candidate articles:
{json.dumps(compact_articles, ensure_ascii=False, indent=2)}
""".strip()


def _run_llm_command(command: str, prompt: str, *, timeout: int) -> str:
    temp_path = None
    try:
        if "{prompt_file}" in command:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
                fh.write(prompt)
                temp_path = fh.name
            final_command = command.replace("{prompt_file}", shlex.quote(temp_path))
            stdin = None
        elif "{prompt}" in command:
            final_command = command.replace("{prompt}", shlex.quote(prompt))
            stdin = None
        else:
            final_command = command
            stdin = prompt

        proc = subprocess.run(
            final_command,
            input=stdin,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"LLM command failed: {proc.stderr.strip()[:500]}")
        return proc.stdout.strip()
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _parse_llm_selection(
    output: str,
    candidates: list[dict[str, Any]],
    max_articles: int,
    *,
    method: str,
) -> list[dict[str, Any]]:
    data = _loads_json_object(output)
    by_id = {row["id"]: row for row in candidates}
    selected = []
    seen_ids: set[str] = set()

    for item in data.get("selected", []):
        article_id = str(item.get("id", "")).strip()
        if article_id not in by_id or article_id in seen_ids:
            continue
        seen_ids.add(article_id)
        selected.append(
            {
                **by_id[article_id],
                "selection_method": method,
                "selection_reason": str(item.get("reason") or "").strip(),
                "translated_title": str(item.get("japanese_title") or "").strip() or None,
                "translated_summary": str(item.get("japanese_summary") or "").strip() or None,
            }
        )
        if len(selected) >= max_articles:
            break

    return selected


def _loads_json_object(output: str) -> dict[str, Any]:
    text = output.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in LLM output")
        text = text[start : end + 1]

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM output must be a JSON object")
    return data


def _row_to_dict(row) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    return {key: row[key] for key in row.keys()}


def _truncate(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
