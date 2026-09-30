"""
One-shot text completions through the Claude CLI, for the chat-page AI helpers that used to call OpenRouter
(teach-answer / KB rules, knowledge base drafts, short explanations). No tools, no MCP, no session.
The agent itself runs through runner.py.

ClaudeTextClient mimics the part of the OpenAI client those helpers use, so views/messaging.py only has to
pick the client (_get_ai_client):
    client.chat.completions.create(model=, messages=, temperature=, max_tokens=).choices[0].message.content
temperature / max_tokens have no CLI flag and are ignored. `model` is used only when it is a Claude model id;
OpenRouter slugs (openai/gpt-...) fall back to config.oneshot_model().
"""
import json
import os
import subprocess
import tempfile
from types import SimpleNamespace

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


def _split_messages(messages):
    """OpenAI-style messages -> (system_prompt, prompt_text)."""
    system = "\n\n".join(m.get('content') or '' for m in messages if m.get('role') == 'system').strip()
    rest = [m for m in messages if m.get('role') != 'system']
    if len(rest) == 1:
        return system, rest[0].get('content') or ''
    prompt = "\n\n".join(f"{(m.get('role') or 'user').upper()}:\n{m.get('content') or ''}" for m in rest)
    return system, prompt


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


class _Completions:
    def create(self, model=None, messages=None, temperature=None, max_tokens=None, **_ignored):
        system, prompt = _split_messages(messages or [])
        result = complete(prompt, system=system, model=model)
        usage = result['usage']
        return SimpleNamespace(
            model=result['model'],
            choices=[SimpleNamespace(message=SimpleNamespace(content=result['text']))],
            usage=SimpleNamespace(
                prompt_tokens=int(usage.get('input_tokens') or 0)
                + int(usage.get('cache_read_input_tokens') or 0) + int(usage.get('cache_creation_input_tokens') or 0),
                completion_tokens=int(usage.get('output_tokens') or 0),
                cost_usd=result['cost_usd'],
            ),
        )


class ClaudeTextClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_Completions())
