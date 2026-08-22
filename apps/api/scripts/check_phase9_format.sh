#!/usr/bin/env sh
set -eu

base_ref="${PHASE9_FORMAT_BASE_REF:-v0.9.0-groups-agencies}"
files=$(git diff --name-only "$base_ref" HEAD -- '*.py' | sed 's#^apps/api/##')

if [ -n "$files" ]; then
  if [ -x .venv/bin/ruff ]; then
    printf '%s\n' "$files" | xargs .venv/bin/ruff format --check
  else
    printf '%s\n' "$files" | xargs uv run ruff format --check
  fi
fi
