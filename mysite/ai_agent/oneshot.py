"""
One-shot text completions through the Claude CLI, for the chat-page AI helpers that used to call OpenRouter
(teach-answer / KB rules, knowledge base drafts, short explanations). No tools, no MCP, no session.
The agent itself runs through runner.py.

complete(prompt, system=None, model=None) -> {'text', 'model', 'usage', 'cost_usd', 'duration_ms'}. `model` is used
only when it is a Claude model id; anything else falls back to config.oneshot_model().
"""
import json
import os
import subprocess
import tempfile

from mysite.ai_agent import config

# Seed default of the 'ai_oneshot_system' prompt (the live text is in AIManagement).
DEFAULT_SYSTEM_PROMPT = (
    "You help the staff of a property management company inside their CRM. Follow the instructions in the "
    "user message exactly and reply with only the text they ask for - no preamble, no markdown fences."
)


def _default_system():
    from mysite.ai_agent import prompt_library
    return prompt_library.get('ai_oneshot_system')


class ClaudeTextError(Exception):
    pass


def _model_for(requested):
    requested = (requested or '').strip()
    return requested if requested.startswith('claude-') else config.oneshot_model()


def complete(prompt, system=None, model=None, timeout=None):
    """Runs `claude -p` once. Returns dict: text, model, usage, cost_usd, duration_ms. Raises ClaudeTextError."""
    from mysite.ai_agent import runner

    model = _model_for(model)
    timeout = timeout or config.oneshot_timeout_seconds()
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if os.environ.get('AI_AGENT_CLAUDE_CONFIG_DIR'):
        env['CLAUDE_CONFIG_DIR'] = os.path.expanduser(os.environ['AI_AGENT_CLAUDE_CONFIG_DIR'])

    with tempfile.NamedTemporaryFile('w', suffix='.md', dir=config.WORK_DIR, encoding='utf-8') as system_file:
        system_file.write((system or '').strip() or _default_system())
        system_file.flush()
        command = [
            runner._claude_binary(), '-p', '--model', model,
            '--system-prompt-file', system_file.name,
            '--tools', '', '--strict-mcp-config',
            '--max-budget-usd', str(config.oneshot_max_budget_usd()),
            '--no-session-persistence', '--output-format', 'json',
        ]
        if os.environ.get('AI_AGENT_CLI_BARE', '').lower() == 'true':
            command.insert(2, '--bare')
        try:
            process = subprocess.run(
                command, input=prompt, capture_output=True, text=True, timeout=timeout,
                cwd=str(config.WORK_DIR), env=env,
            )
        except subprocess.TimeoutExpired:
            raise ClaudeTextError(f"claude CLI timed out after {timeout}s")
        except OSError as e:
            raise ClaudeTextError(f"claude CLI could not be started: {e}")

    try:
        result = json.loads(process.stdout or '')
    except ValueError:
        raise ClaudeTextError(
            f"claude CLI returned no result (exit code {process.returncode}): {(process.stderr or process.stdout or '')[:300]}"
        )
    if result.get('is_error'):
        raise ClaudeTextError(f"claude CLI error: {result.get('subtype')} {str(result.get('result') or '')[:300]}")
    usage = result.get('usage') or {}
    return {
        'text': (result.get('result') or '').strip(),
        'model': model,
        'usage': usage,
        'cost_usd': result.get('total_cost_usd'),
        'duration_ms': result.get('duration_ms'),
    }


