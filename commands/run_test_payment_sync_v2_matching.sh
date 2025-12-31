#!/usr/bin/env bash
set -euo pipefail

# Tries to run the Django management test command with a Python that has Django installed.
# Usage:
#   bash commands/run_test_payment_sync_v2_matching.sh
#   RUN_AI=1 bash commands/run_test_payment_sync_v2_matching.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_PATH="${OUT_PATH:-${ROOT_DIR}/logs/payment_sync_v2_matching_test_results.json}"

RUN_AI="${RUN_AI:-0}"
AI_FLAG=""
if [[ "${RUN_AI}" == "1" || "${RUN_AI}" == "true" || "${RUN_AI}" == "yes" || "${RUN_AI}" == "on" ]]; then
  AI_FLAG="--ai"
  OUT_PATH="${OUT_PATH%.json}_ai.json"
fi

PY_CANDIDATES=(
  "${ROOT_DIR}/venv/bin/python"
  "${ROOT_DIR}/.venv/bin/python"
  "python3"
  "python"
)

pick_python() {
  for py in "${PY_CANDIDATES[@]}"; do
    if command -v "${py}" >/dev/null 2>&1 || [[ -x "${py}" ]]; then
      # shellcheck disable=SC2086
      "${py}" -c "import django; print(django.get_version())" >/dev/null 2>&1 && { echo "${py}"; return 0; }
    fi
  done
  return 1
}

PY="$(pick_python || true)"
if [[ -z "${PY}" ]]; then
  echo "Could not find a python interpreter with Django installed."
  echo "Tried: ${PY_CANDIDATES[*]}"
  exit 1
fi

mkdir -p "${ROOT_DIR}/logs"

echo "Using python: ${PY}"
echo "Writing: ${OUT_PATH}"

# shellcheck disable=SC2086
"${PY}" "${ROOT_DIR}/manage.py" test_payment_sync_v2_matching ${AI_FLAG} --out "${OUT_PATH}"

