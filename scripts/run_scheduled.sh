#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p data reports logs

lock_file="${AI_RESEARCH_LOCK_FILE:-data/ai-researcher.lock}"
exec 9>"$lock_file"
if ! flock -n 9; then
  echo "ai-researcher scheduled job is already running; skipping"
  exit 0
fi

python3 -m ai_researcher.cli \
  --db "${AI_RESEARCH_DB:-data/research.db}" \
  --config "${AI_RESEARCH_CONFIG:-config/sources.json}" \
  run-scheduled "$@"
