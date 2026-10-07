"""
Sandbox test runner for the Telegram alerts (claude_code_integration_doc/simple_telegram_alerts.md, part 11).

  catalog.py   the cases (testbed/sandbox_cases.yaml) + their example text read from the document, last results
  clock.py     the case time: the whole process believes it is e.g. Tue 14:34 ET
  world.py     the sandbox: clean start, set-up, and the layer that keeps a test inside the sandbox
               (no SMS, no call, no write to real knowledge / prompts / ClickUp lists)
  check.py     what the agent did -> expected vs got, the Claude judge, the test notes
  runner.py    one case after another, replies / button presses, commands (next, rerun, ...), summary

Entry point: python manage.py ai_agent_sandbox_test
"""
