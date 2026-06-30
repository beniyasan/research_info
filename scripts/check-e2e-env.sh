#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"

echo "E2E environment probe"
echo "root=$root_dir"

found=0
for pattern in \
  "playwright.config.*" \
  "cypress.config.*" \
  "*selenium*" \
  "*webdriver*" \
  "*maestro*" \
  "*detox*"
do
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    echo "config=$path"
    found=1
  done < <(find . -maxdepth 4 -type f -name "$pattern" -not -path "./.git/*" -print)
done

if [ -f package.json ]; then
  echo "package.json=present"
  node -e 'const p=require("./package.json"); for (const [k,v] of Object.entries(p.scripts || {})) if (/e2e|playwright|cypress|browser|smoke/i.test(k) || /playwright|cypress|selenium|webdriver|maestro|detox/i.test(v)) console.log(`script=${k}: ${v}`);'
fi

[ -f Dockerfile ] && echo "dockerfile=present"
[ -f docker-compose.yml ] && echo "compose=docker-compose.yml"
[ -f compose.yml ] && echo "compose=compose.yml"
[ -f pytest.ini ] && echo "pytest=pytest.ini"

if [ "$found" -eq 0 ]; then
  echo "e2e_config=not_found"
fi
