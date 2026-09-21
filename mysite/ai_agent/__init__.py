"""
AI agent for tenant group chats (Claude Code CLI backend).

Flow: webhook/chat UI -> AIEvent (queue) -> run_ai_agent worker -> claude -p -> AIRun + run report
-> send to Twilio only when Apartment.ai_group_chat_enabled.

See claude_code_integration_doc/migration_plan.md.
"""
