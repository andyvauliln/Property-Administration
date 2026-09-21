"""
Runs one headless Claude Code session (`claude -p`) and returns everything it produced.

This is the only place that knows about the CLI. Moving to the Agent SDK means replacing
run_claude() and keeping the returned dict shape.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings

from mysite.ai_agent import config


def _claude_binary():
    configured = config.claude_binary()
    found = shutil.which(configured)
    if found:
        return found
    fallback = Path.home() / '.local' / 'bin' / 'claude'
    return str(fallback) if fallback.exists() else configured


def build_mcp_config(conversation_sid, until_message_id=None):
    env = {'AI_AGENT_CONVERSATION_SID': conversation_sid or ''}
    if until_message_id:
        env['AI_AGENT_UNTIL_MESSAGE_ID'] = str(until_message_id)
    return {
        'mcpServers': {
            config.MCP_SERVER_NAME: {
                'command': sys.executable,
                'args': [str(config.MCP_TOOLS_PATH)],
                'env': env,
            }
        }
    }


def build_command(model, system_prompt_file, mcp_config_file):
    schema = json.dumps(json.loads(config.SCHEMA_PATH.read_text(encoding='utf-8')), separators=(',', ':'))
    command = [
        _claude_binary(), '-p',
        '--model', model,
        '--system-prompt-file', str(system_prompt_file),
        '--json-schema', schema,
        # No built-in tools at all (no Bash / files / web): tenant text is untrusted input
        '--restricted',
        '--tools', '',
        '--strict-mcp-config',
        '--mcp-config', str(mcp_config_file),
        '--allowedTools', ','.join(config.ALLOWED_MCP_TOOLS),
        '--max-budget-usd', str(config.max_budget_usd()),
        '--no-session-persistence',
        '--output-format', 'stream-json',
        '--verbose',
    ]
    if os.environ.get('AI_AGENT_CLI_BARE', '').lower() == 'true':
        # Requires ANTHROPIC_API_KEY (bare mode never reads the OAuth login)
        command.insert(2, '--bare')
    return command


def _parse_stream(stdout_text):
    events = []
    for line in (stdout_text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            events.append({'type': 'unparsed', 'raw': line})
    return events


def _final_result(events):
    for event in reversed(events):
        if event.get('type') == 'result':
            return event
    return None


def _structured_output(result_event):
    if not result_event:
        return None
    output = result_event.get('structured_output')
    if isinstance(output, dict):
        return output
    text = result_event.get('result')
    if isinstance(text, str) and text.strip().startswith('{'):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            return None
    return None


def run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None):
    """
    run_dir: folder of this run; the exact files given to the CLI are written there.
    Returns dict: ok, error, output, events, result_event, stdout, stderr, command, mcp_config,
                  model, exit_code, duration_ms, timed_out.
    """
    model = model or config.get_agent_model()
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)

    system_prompt_file = run_dir / '01_system_prompt.md'
    system_prompt_file.write_text(system_prompt, encoding='utf-8')
    mcp_config = build_mcp_config(conversation_sid, until_message_id)
    mcp_config_file = run_dir / 'mcp_config.json'
    mcp_config_file.write_text(json.dumps(mcp_config, indent=2), encoding='utf-8')

    command = build_command(model, system_prompt_file, mcp_config_file)
    env = os.environ.copy()
    if os.environ.get('AI_AGENT_CLAUDE_CONFIG_DIR'):
        env['CLAUDE_CONFIG_DIR'] = os.path.expanduser(os.environ['AI_AGENT_CLAUDE_CONFIG_DIR'])
    env.setdefault('PYTHONPATH', str(settings.BASE_DIR))

    started = time.monotonic()
    stdout_text, stderr_text, exit_code, timed_out, error = '', '', None, False, None
    try:
        process = subprocess.Popen(
            command, cwd=str(config.WORK_DIR), env=env, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            stdout_text, stderr_text = process.communicate(user_input, timeout=config.run_timeout_seconds())
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout_text, stderr_text = process.communicate()
            error = f"claude CLI timed out after {config.run_timeout_seconds()}s"
        exit_code = process.returncode
    except OSError as e:
        error = f"claude CLI could not be started: {e}"
    duration_ms = int((time.monotonic() - started) * 1000)

    events = _parse_stream(stdout_text)
    result_event = _final_result(events)
    output = _structured_output(result_event)

    if not error:
        if result_event is None:
            error = f"claude CLI returned no result event (exit code {exit_code})"
        elif result_event.get('is_error'):
            error = f"claude CLI error: {result_event.get('subtype')} {str(result_event.get('result') or '')[:300]}"
        elif output is None:
            error = "claude CLI returned no structured output"

    return {
        'ok': error is None,
        'error': error,
        'output': output,
        'events': events,
        'result_event': result_event,
        'stdout': stdout_text or '',
        'stderr': stderr_text or '',
        'command': ' '.join(shlex.quote(part) for part in command),
        'mcp_config': mcp_config,
        'model': model,
        'exit_code': exit_code,
        'duration_ms': duration_ms,
        'timed_out': timed_out,
    }
