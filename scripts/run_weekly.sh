#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m ai_researcher.cli --db data/research.db --config config/sources.json init
python3 -m ai_researcher.cli --db data/research.db --config config/sources.json seed
python3 -m ai_researcher.cli --db data/research.db --config config/sources.json report --period weekly "$@"
python3 -m ai_researcher.cli --db data/research.db --config config/sources.json evolve
