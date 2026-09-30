"""
Writes one folder per agent run under logs/ai_runs/<date>/ so every run can be investigated:
what went in, what the agent did step by step, what came out, tokens and cost.
"""
import csv
import json
import re
from datetime import datetime

from mysite.ai_agent import config

STRUCTURED_OUTPUT_TOOL = 'StructuredOutput'

INDEX_COLUMNS = [
    'time', 'run_id', 'apartment', 'conversation_sid', 'event', 'mode', 'model', 'result',
    'actions', 'tool_calls', 'turns', 'input_tokens', 'output_tokens', 'cache_read_tokens',
    'cache_write_tokens', 'cost_usd', 'duration_ms', 'sent_to_chat', 'error', 'report_dir',
]


def _slug(value, fallback='na'):
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', str(value or '')).strip('-')
    return value or fallback


def apartment_label(apartment):
    if not apartment:
        return 'no-apartment'
    # The CRM name is what staff know ("630-429", "Test_Apart2", "815 Flamingo"); the building/unit
    # numbers are only a fallback ("555-111" told nobody that it was the test apartment).
    name = (getattr(apartment, 'name', '') or '').strip()
    if name:
        return name
    building = (getattr(apartment, 'building_n', '') or '').strip()
    unit = (getattr(apartment, 'apartment_n', '') or '').strip()
    return f"{building}-{unit}" if building and unit else f"apartment-{apartment.id}"


def new_run_dir(conversation_sid, apartment, event_type):
    now = datetime.now()
    name = "_".join([
        now.strftime('%H%M%S'),
        _slug(apartment_label(apartment)),
        _slug(event_type),
        _slug(conversation_sid)[-10:],
    ])
    run_dir = config.RUNS_DIR / now.strftime('%Y-%m-%d') / name
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = run_dir.with_name(f"{name}_{suffix}")
    run_dir.mkdir(parents=True)
    return run_dir


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding='utf-8')


def summarize_run(run):
    """Totals, per-turn usage and tool calls extracted from the CLI stream."""
    result_event = run.get('result_event') or {}
    usage = result_event.get('usage') or {}

    tool_inputs = {}
    tool_calls = []
    warnings = []
    for event in run.get('events') or []:
        if event.get('type') == 'system' and event.get('subtype') == 'init':
            for server in event.get('mcp_servers') or []:
                if server.get('status') != 'connected':
                    warnings.append(f"MCP server '{server.get('name')}' is {server.get('status')} - its tools were not available")
        content = (event.get('message') or {}).get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                call = {'id': block.get('id'), 'tool': block.get('name'), 'input': block.get('input'), 'output': None}
                tool_inputs[block.get('id')] = call
                tool_calls.append(call)
            elif block.get('type') == 'tool_result' and block.get('tool_use_id') in tool_inputs:
                output = block.get('content')
                if isinstance(output, list):
                    output = "\n".join(
                        part.get('text', '') if isinstance(part, dict) else str(part) for part in output
                    )
                tool_inputs[block['tool_use_id']]['output'] = output
                tool_inputs[block['tool_use_id']]['is_error'] = bool(block.get('is_error'))

    turns = [
        {
            'turn': index,
            'input_tokens': item.get('input_tokens', 0),
            'output_tokens': item.get('output_tokens', 0),
            'cache_read_tokens': item.get('cache_read_input_tokens', 0),
            'cache_write_tokens': item.get('cache_creation_input_tokens', 0),
        }
        for index, item in enumerate(usage.get('iterations') or [], start=1)
    ]

    return {
        'input_tokens': usage.get('input_tokens', 0) or 0,
        'output_tokens': usage.get('output_tokens', 0) or 0,
        'thinking_tokens': (usage.get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0,
        'cache_read_tokens': usage.get('cache_read_input_tokens', 0) or 0,
        'cache_write_tokens': usage.get('cache_creation_input_tokens', 0) or 0,
        'cost_usd': result_event.get('total_cost_usd', 0) or 0,
        'num_turns': result_event.get('num_turns', 0) or 0,
        'duration_ms': run.get('duration_ms', 0) or 0,
        'duration_api_ms': result_event.get('duration_api_ms', 0) or 0,
        'session_id': result_event.get('session_id'),
        'turns': turns,
        'tool_calls': [c for c in tool_calls if c['tool'] != STRUCTURED_OUTPUT_TOOL],
        'all_tool_calls': tool_calls,
        'model_usage': result_event.get('modelUsage') or {},
        'permission_denials': result_event.get('permission_denials') or [],
        'warnings': warnings,
    }


def _steps_markdown(run):
    lines = ["# Steps", ""]
    step = 0
    for event in run.get('events') or []:
        kind = event.get('type')
        if kind == 'system' and event.get('subtype') == 'init':
            servers = ", ".join(
                f"{s.get('name')} ({s.get('status')})" for s in event.get('mcp_servers') or []
            ) or 'none'
            lines += [
                "## Session start",
                f"- model: `{event.get('model')}`",
                f"- claude code version: `{event.get('claude_code_version')}`",
                f"- auth: `{event.get('apiKeySource')}`",
                f"- tools available: {', '.join(event.get('tools') or []) or 'none'}",
                f"- MCP servers: {servers}",
                "",
            ]
        elif kind in ('assistant', 'user'):
            content = (event.get('message') or {}).get('content')
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get('type')
                if block_type == 'thinking':
                    text = (block.get('thinking') or '').strip()
                    step += 1
                    lines += [f"## {step}. Thinking", text or "_(thinking text is not returned by the API for this model)_", ""]
                elif block_type == 'text' and kind == 'assistant':
                    step += 1
                    lines += [f"## {step}. Assistant text", block.get('text') or '', ""]
                elif block_type == 'tool_use':
                    step += 1
                    lines += [
                        f"## {step}. Tool call: `{block.get('name')}`",
                        "```json",
                        json.dumps(block.get('input'), indent=2, ensure_ascii=False),
                        "```",
                        "",
                    ]
                elif block_type == 'tool_result':
                    output = block.get('content')
                    if isinstance(output, list):
                        output = "\n".join(
                            part.get('text', '') if isinstance(part, dict) else str(part) for part in output
                        )
                    step += 1
                    lines += [
                        f"## {step}. Tool result{' (ERROR)' if block.get('is_error') else ''}",
                        "```",
                        str(output or ''),
                        "```",
                        "",
                    ]
        elif kind == 'result':
            lines += [
                "## Finished",
                f"- status: `{event.get('subtype')}` (is_error={event.get('is_error')})",
                f"- stop reason: `{event.get('stop_reason')}`",
                f"- turns: {event.get('num_turns')}",
                "",
            ]
    if step == 0:
        lines.append("_No assistant steps were recorded (the CLI did not produce a transcript)._")
    return "\n".join(lines)


def _table(headers, rows):
    if not rows:
        return "_none_"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell).replace("\n", " ").replace("|", "\\|") for cell in row) + " |")
    return "\n".join(out)


def _short(value, limit=140):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text or ''
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _report_markdown(meta, summary, parsed, actions, delivery, run, new_messages_text):
    answer = parsed.get('answer') if parsed else None
    lines = [
        f"# AI run — {meta.get('apartment')} — {meta.get('event_type')}",
        "",
        f"- time: {meta.get('started_at')}",
        f"- mode: **{meta.get('mode')}**",
        f"- conversation: `{meta.get('conversation_sid')}`  booking: `{meta.get('booking_id')}`  tenant: {meta.get('tenant')}",
        f"- model: `{run.get('model')}`  backend: `{meta.get('backend')}`",
        f"- system prompt source: `{meta.get('system_prompt_source')}`",
        "",
        "## Message(s) handled",
        "```",
        new_messages_text or '',
        "```",
        "",
        "## Answer",
        "```",
        "NO_ANSWER" if (parsed and parsed.get('no_answer')) else (answer or '(none)'),
        "```",
        "",
        *(["## Review answer (staff answered first - NEVER sent, for managers only)", "```", parsed['review_answer'], "```", ""]
          if parsed and parsed.get('review_answer') else []),
        "## Why",
        (parsed or {}).get('why') or '(none)',
        "",
        "## Delivery",
        f"- sent to group chat: **{bool(delivery.get('sent_to_chat'))}**",
        f"- note: {delivery.get('note') or ''}",
        "",
        "## Actions",
        _table(
            ['#', 'type', 'status', 'detail', 'action'],
            [
                [i, a.get('action', {}).get('type'), a.get('status'), a.get('detail') or '', _short(a.get('action'), 200)]
                for i, a in enumerate(actions or [], start=1)
            ],
        ),
        "",
        "## Tool calls",
        _table(
            ['#', 'tool', 'input', 'output chars', 'error'],
            [
                [i, c['tool'], _short(c['input']), len(c.get('output') or ''), 'yes' if c.get('is_error') else '']
                for i, c in enumerate(summary['tool_calls'], start=1)
            ],
        ),
        "",
        "## Tokens",
        _table(
            ['turn', 'input', 'output', 'cache read', 'cache write'],
            [
                [t['turn'], t['input_tokens'], t['output_tokens'], t['cache_read_tokens'], t['cache_write_tokens']]
                for t in summary['turns']
            ] + [[
                '**total**', summary['input_tokens'], summary['output_tokens'],
                summary['cache_read_tokens'], summary['cache_write_tokens'],
            ]],
        ),
        "",
        f"- thinking tokens (included in output): {summary['thinking_tokens']}",
        f"- cost: **${summary['cost_usd']:.6f}**",
        f"- turns: {summary['num_turns']}",
        f"- duration: {summary['duration_ms']} ms total, {summary['duration_api_ms']} ms API",
        "",
        "## Errors",
        run.get('error') or '_none_',
        "",
        "## Warnings",
        "\n".join(f"- {w}" for w in summary['warnings']) or '_none_',
    ]
    if summary['permission_denials']:
        lines += ["", "### Permission denials", "```json", json.dumps(summary['permission_denials'], indent=2), "```"]
    lines += [
        "",
        "## Files",
        "- `01_system_prompt.md` exact system prompt · `02_input.md` exact input · `03_command.txt` CLI call",
        "- `04_transcript.jsonl` raw stream (source of truth) · `05_steps.md` readable steps",
        "- `06_output.json` parsed output · `07_actions.json` actions · `08_delivery.json` delivery",
    ]
    return "\n".join(lines)


def _append_index(row):
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = config.RUNS_DIR / 'index.csv'
    is_new = not index_path.exists()
    with index_path.open('a', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_COLUMNS, extrasaction='ignore')
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _photos_note(meta):
    ids = ((meta or {}).get('context_sources') or {}).get('agent_images') or []
    return f" + {len(ids)} photo(s) as stream-json image blocks (media ids {ids})" if ids else ''


def write_report(run_dir, meta, user_input, run, parsed, actions, delivery, new_messages_text=''):
    """
    run: dict returned by runner.run_claude (01_system_prompt.md and mcp_config.json already exist).
    parsed: {'answer', 'why', 'no_answer', 'raw'} or None.  actions: list of action results.
    Returns the summary dict (tokens, cost, tool calls).
    """
    summary = summarize_run(run)

    _write_json(run_dir / '00_meta.json', meta)
    (run_dir / '02_input.md').write_text(user_input or '', encoding='utf-8')
    (run_dir / '03_command.txt').write_text(
        f"cwd: {config.WORK_DIR}\nstdin: 02_input.md{_photos_note(meta)}\n\n{run.get('command') or ''}\n\n"
        f"MCP config (mcp_config.json):\n{json.dumps(run.get('mcp_config'), indent=2)}\n",
        encoding='utf-8',
    )
    (run_dir / '04_transcript.jsonl').write_text(run.get('stdout') or '', encoding='utf-8')
    (run_dir / '05_steps.md').write_text(_steps_markdown(run), encoding='utf-8')
    _write_json(run_dir / '06_output.json', {
        'structured_output': run.get('output'),
        'parsed': parsed,
        'valid': bool(run.get('ok')),
        'error': run.get('error'),
    })
    _write_json(run_dir / '07_actions.json', actions or [])
    _write_json(run_dir / '08_delivery.json', delivery or {})
    if (run.get('stderr') or '').strip():
        (run_dir / 'stderr.txt').write_text(run['stderr'], encoding='utf-8')
    (run_dir / 'report.md').write_text(
        _report_markdown(meta, summary, parsed, actions, delivery or {}, run, new_messages_text),
        encoding='utf-8',
    )

    if parsed and parsed.get('no_answer'):
        result = 'NO_ANSWER'
    elif parsed and parsed.get('answer'):
        result = 'ANSWER'
    else:
        result = 'ERROR'
    _append_index({
        'time': meta.get('started_at'),
        'run_id': meta.get('run_id') or '',
        'apartment': meta.get('apartment'),
        'conversation_sid': meta.get('conversation_sid'),
        'event': meta.get('event_type'),
        'mode': meta.get('mode'),
        'model': run.get('model'),
        'result': result,
        'actions': len(actions or []),
        'tool_calls': len(summary['tool_calls']),
        'turns': summary['num_turns'],
        'input_tokens': summary['input_tokens'],
        'output_tokens': summary['output_tokens'],
        'cache_read_tokens': summary['cache_read_tokens'],
        'cache_write_tokens': summary['cache_write_tokens'],
        'cost_usd': summary['cost_usd'],
        'duration_ms': summary['duration_ms'],
        'sent_to_chat': bool((delivery or {}).get('sent_to_chat')),
        'error': (run.get('error') or "; ".join(f"WARN: {w}" for w in summary['warnings']))[:300],
        'report_dir': str(run_dir),
    })
    return summary
