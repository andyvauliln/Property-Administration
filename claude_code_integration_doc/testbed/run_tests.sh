#!/bin/bash
# AI agent tests on a throwaway SQLite database: fake Claude, no Telegram, no Twilio, no cost.
# Usage: bash claude_code_integration_doc/testbed/run_tests.sh      (from the project root)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$HERE:$ROOT" DJANGO_SETTINGS_MODULE=testbed_settings
rm -rf "${TMPDIR:-/tmp}/ai_agent_testbed"
./venv/bin/python manage.py migrate --run-syncdb > /dev/null 2>&1
./venv/bin/python "$HERE/e2e_phase2.py" 2>&1 | grep -E "^(===|PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/ui_test.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_phase3.py" 2>&1 | grep -E "^(===|PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_clickup.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_misc.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_notification_window.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_answer_review.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_duplicate_chats.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_user_phones.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_merged_chats.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_legal.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_chat_ui_claude.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_media.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_prompts.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
./venv/bin/python "$HERE/e2e_kb_documents.py" 2>&1 | grep -E "^(PASS|FAIL|[0-9]+/|Traceback|\w+Error)"
