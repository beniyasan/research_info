#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex command not found" >&2
  exit 127
fi

prompt_file="$(mktemp)"
output_file="$(mktemp)"
trap 'rm -f "$prompt_file" "$output_file"' EXIT

cat > "$prompt_file"

codex exec \
  --ephemeral \
  --sandbox read-only \
  --output-schema config/selection.schema.json \
  --output-last-message "$output_file" \
  - < "$prompt_file" >/dev/null

cat "$output_file"
