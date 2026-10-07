"""
The test cases: structured part in testbed/sandbox_cases.yaml, example text in simple_telegram_alerts.md (the
document stays the only source of the examples: the section "### A1. ..." is read from it at run time).
"""
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from django.conf import settings

from mysite.ai_agent import config

DOC_DIR = Path(settings.BASE_DIR) / 'claude_code_integration_doc'
CASES_PATH = DOC_DIR / 'testbed' / 'sandbox_cases.yaml'
DOC_PATH = DOC_DIR / 'simple_telegram_alerts.md'
RESULTS_DIR = Path(settings.BASE_DIR) / 'logs' / 'sandbox_tests'
LAST_RESULTS = RESULTS_DIR / 'last_results.json'

# What a case takes from its `base` case; the base's trigger becomes the first prelude step
INHERITED = ('time', 'mode', 'clickup', 'tenant', 'apartment', 'setup', 'history')
_ID = re.compile(r'^([A-Z])(\d+)([a-z]?)$')
_SECTION = re.compile(r'^#{3,4}\s+([A-Z]\d+[a-z]?)\.\s+(.*)$')
_HEADING = re.compile(r'^#{2,4}\s+')


class CatalogError(Exception):
    pass


def sort_key(case_id):
    match = _ID.match(case_id)
    return (match.group(1), int(match.group(2)), match.group(3)) if match else ('~', 0, case_id)


def _resolve(case_id, raw, seen=()):
    if case_id in seen:
        raise CatalogError(f"{case_id}: `base` goes in a circle ({' -> '.join(seen)})")
    case = dict(raw[case_id])
    base_id = case.get('base')
    if not base_id:
        return case
    if base_id not in raw:
        raise CatalogError(f"{case_id}: base case {base_id} does not exist")
    base = _resolve(base_id, raw, seen + (case_id,))
    merged = {key: base[key] for key in INHERITED if key in base}
    merged.update(case)
    merged['prelude'] = list(base.get('prelude') or []) + [base['trigger']] + list(case.get('prelude') or [])
    return merged


def _plain_keys(value):
    """YAML reads the bare key `on` (press / reply: on: first) as the boolean True: back to the word."""
    if isinstance(value, dict):
        return {{True: 'on', False: 'off'}.get(k, k) if isinstance(k, bool) else k: _plain_keys(v) for k, v in value.items()}
    return [_plain_keys(v) for v in value] if isinstance(value, list) else value


STORY_KEY = 'story'   # the top-level block of the case file that is not a case: the rows the story starts with


def _raw():
    if not CASES_PATH.exists():
        raise CatalogError(f"{CASES_PATH} does not exist")
    return _plain_keys(yaml.safe_load(CASES_PATH.read_text(encoding='utf-8')) or {})


def story_block():
    """The `story:` block: apartment knowledge, tenant, booking, payments, parking, contract (see story.py)."""
    block = _raw().get(STORY_KEY) or {}
    if not isinstance(block, dict):
        raise CatalogError("story: must be a mapping")
    return block


def load_cases():
    """{case id: case} in STORY order (the order of the file), `base` already merged in. The chapters' times may not
    go backwards: the story is one timeline."""
    raw = _raw()
    cases = {}
    for case_id, body in raw.items():
        if str(case_id) == STORY_KEY:
            continue
        if not isinstance(body, dict):
            raise CatalogError(f"{case_id}: must be a mapping")
        cases[str(case_id)] = dict(body, id=str(case_id))
    resolved, last = {}, None
    defaults = (raw.get(STORY_KEY) or {}).get('defaults') or {}   # story-wide mode / clickup, a chapter may override
    for case_id in cases:
        case = _resolve(case_id, cases)
        for key, value in defaults.items():
            case.setdefault(key, value)
        if 'trigger' not in case:
            raise CatalogError(f"{case_id}: no trigger")
        if case.get('time'):
            moment = parse_time(case['time'])
            if last and moment < last[1]:
                raise CatalogError(f"{case_id}: its time {case['time']} is before {last[0]} ({last[1]:%Y-%m-%d %H:%M}) - "
                                   f"the chapters of the story must be in time order")
            last = (case_id, moment)
        resolved[case_id] = case
    return resolved


def story_order():
    """The chapters in story order."""
    return list(load_cases().values())


def select(cases, ids=None, group=None, everything=False):
    if everything:
        return list(cases.values())
    if group:
        letters = [g.strip().upper() for g in group.split(',') if g.strip()]
        return [case for case_id, case in cases.items() if case_id[0] in letters]
    wanted = [i.strip() for i in (ids or '').split(',') if i.strip()]
    missing = [i for i in wanted if i not in cases]
    if missing:
        raise CatalogError(f"unknown case(s): {', '.join(missing)} (see --list)")
    return [cases[i] for i in wanted]


def parse_time(value):
    """'2026-10-06 14:34' (ET) -> aware datetime in the team timezone."""
    tz = ZoneInfo(config.TEAM_TIMEZONE)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value).strip()[:16], fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    raise CatalogError(f"can not read the time {value!r} (use YYYY-MM-DD HH:MM)")


def parse_delta(value):
    """'2h', '30min', '1d', '90m' -> timedelta."""
    from datetime import timedelta
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(min|m|h|d)\w*\s*', str(value))
    if not match:
        raise CatalogError(f"can not read the time span {value!r} (use 30min, 2h, 1d)")
    amount = float(match.group(1))
    return {'min': timedelta(minutes=amount), 'm': timedelta(minutes=amount), 'h': timedelta(hours=amount),
            'd': timedelta(days=amount)}[match.group(2)]


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

def doc_sections():
    """{'A1': {'title': ..., 'text': the whole section}} for every '### A1. ...' / '#### G1. ...' heading."""
    if not DOC_PATH.exists():
        return {}
    sections, current, in_code = {}, None, False
    for line in DOC_PATH.read_text(encoding='utf-8').splitlines():
        if line.strip().startswith('```'):
            in_code = not in_code
        if not in_code and (_HEADING.match(line) or line.strip() == '---'):
            match = _SECTION.match(line)
            current = match.group(1) if match else None
            if current:
                sections[current] = {'title': match.group(2).strip(), 'lines': []}
            continue
        if current:
            sections[current]['lines'].append(line)
    return {key: {'title': value['title'], 'text': "\n".join(value['lines']).strip()} for key, value in sections.items()}


def doc_section(case):
    return doc_sections().get(case.get('doc') or case['id']) or doc_sections().get(case['id'])


def doc_rules():
    """Parts 1 and 2 of the document: the rules every alert must follow (real-conversation mode has no example)."""
    if not DOC_PATH.exists():
        return ''
    text = DOC_PATH.read_text(encoding='utf-8')
    start, end = text.find('## 1. Rules'), text.find('## 3. Example catalog')
    return text[start:end].strip() if start >= 0 and end > start else ''


def replace_doc_example(case, new_example):
    """Puts new text into the first code block of the case's section. Returns True when the document changed."""
    wanted = case.get('doc') or case['id']
    lines = DOC_PATH.read_text(encoding='utf-8').split("\n")
    inside = False
    for index, line in enumerate(lines):
        match = _SECTION.match(line)
        if match:
            inside = match.group(1) == wanted
        elif inside and line.strip().startswith('```'):
            end = next((i for i in range(index + 1, len(lines)) if lines[i].strip().startswith('```')), None)
            if end is None:
                return False
            lines[index + 1:end] = new_example.strip("\n").split("\n")
            DOC_PATH.write_text("\n".join(lines), encoding='utf-8')
            return True
    return False


def case_yaml(case_id):
    """The case's block of the YAML file as written (comments kept), or ''."""
    match = re.search(rf'^{re.escape(case_id)}:[^\n]*\n(?:(?:[ \t#][^\n]*)?\n)*', CASES_PATH.read_text(encoding='utf-8'), re.M)
    return match.group(0).rstrip() + "\n" if match else ''


def replace_case_yaml(case_id, new_block):
    """Replaces the case's block; the file is restored when the result no longer loads. Returns an error text or None."""
    old_text = CASES_PATH.read_text(encoding='utf-8')
    old_block = case_yaml(case_id)
    new_block = new_block.strip("\n") + "\n"
    if not old_block or not new_block.startswith(f"{case_id}:"):
        return f"the new block must start with '{case_id}:'"
    CASES_PATH.write_text(old_text.replace(old_block, new_block + "\n", 1), encoding='utf-8')
    try:
        load_cases()
    except Exception as e:
        CASES_PATH.write_text(old_text, encoding='utf-8')
        return f"the changed file does not load ({e}) - nothing was changed"
    return None


def pick(spec):
    """'A5,B' -> the case A5 and every B case, in catalog order ("run A5,B" typed in the test group)."""
    cases = load_cases()
    wanted = [w.strip() for w in (spec or '').split(',') if w.strip()]
    ids = {w[0].upper() + w[1:] for w in wanted if len(w) > 1}
    letters = {w.upper() for w in wanted if len(w) == 1}
    missing = sorted(i for i in ids if i not in cases)
    if missing:
        raise CatalogError(f"unknown case(s): {', '.join(missing)}")
    return [case for case_id, case in cases.items() if case_id in ids or case_id[0] in letters]


def next_case_id(letter):
    """The next free number of a group: 'A' -> 'A22' when A21 is the last one in the case file and the document."""
    numbers = [int(_ID.match(i).group(2)) for i in list(load_cases()) + list(doc_sections()) if _ID.match(i) and i[0] == letter]
    return f"{letter}{max(numbers or [0]) + 1}"


def add_case(case_id, yaml_block, title, doc_example, note=''):
    """A new case: its block goes after the last case of its group in the case file, its example after the last
    section of the group in the document. Both files are restored when the result does not load. Returns an error or None."""
    old_yaml, old_doc = CASES_PATH.read_text(encoding='utf-8'), DOC_PATH.read_text(encoding='utf-8')
    block = yaml_block.strip("\n") + "\n"
    if not block.startswith(f"{case_id}:") or case_id in load_cases():
        return f"the new block must start with '{case_id}:' and {case_id} must not exist yet"
    # A new chapter goes into the story at its time (the file order is the story order): after the last chapter that is
    # not later than it, else at the end
    new_time = re.search(r"^\s+time:\s*['\"]?(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", block, re.M)
    after = ''
    if new_time:
        moment = parse_time(new_time.group(1))
        for cid, case in load_cases().items():
            if case.get('time') and parse_time(case['time']) <= moment:
                after = case_yaml(cid)
    CASES_PATH.write_text(old_yaml.replace(after, after + "\n" + block, 1) if after else old_yaml.rstrip("\n") + "\n\n" + block,
                          encoding='utf-8')
    section = f"### {case_id}. {title}\n\n```\n{doc_example.strip(chr(10))}\n```\n\n{note}\n\n"
    lines, in_code, last, insert = old_doc.split("\n"), False, None, None
    for index, line in enumerate(lines):
        if line.strip().startswith('```'):
            in_code = not in_code
        if in_code or not (_HEADING.match(line) or line.strip() == '---'):
            continue
        match = _SECTION.match(line)
        if last is not None and insert is None:
            insert = index   # the first heading after the group's last section
        if match and match.group(1)[0] == case_id[0]:
            last, insert = index, None
    if last is None:
        CASES_PATH.write_text(old_yaml, encoding='utf-8')
        return f"the document has no section of group {case_id[0]} to put the example after"
    insert = len(lines) if insert is None else insert
    DOC_PATH.write_text("\n".join(lines[:insert] + section.split("\n") + lines[insert:]), encoding='utf-8')
    try:
        cases = load_cases()
        if case_id not in cases or case_id not in doc_sections():
            raise CatalogError("the new case was not found after writing")
    except Exception as e:
        CASES_PATH.write_text(old_yaml, encoding='utf-8')
        DOC_PATH.write_text(old_doc, encoding='utf-8')
        return f"the changed files do not load ({e}) - nothing was changed"
    return None


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def last_results():
    try:
        return json.loads(LAST_RESULTS.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_result(case_id, verdict, reason, run_name):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results = last_results()
    results[case_id] = {'verdict': verdict, 'reason': reason, 'run': run_name,
                        'at': datetime.now(ZoneInfo(config.TEAM_TIMEZONE)).strftime('%Y-%m-%d %H:%M')}
    LAST_RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding='utf-8')
