"""
The tenant's contract for the agent's get_contract tool (user request 2026-09-25): legal questions are answered
from the contract and always wait for a manager's confirmation (answer_review.py).

Source is DocuSeal (Booking.contract_id = submission id):
  - status and the filled-in values (what the tenant signed / was asked to sign) from GET /submissions/{id}
  - clause text from the signed PDF, or from the template PDF while the tenant has not signed yet
PDF text is cached in logs/ai_runs/_contracts (a signed PDF never changes; a template by its updated_at).
Bank details and every link are removed: the text must never put account numbers or signing links in an answer.
"""
import hashlib
import io
import os
import re
from datetime import datetime

import requests

from mysite.ai_agent import config

API_BASE = 'https://api.docuseal.co'
CACHE_DIR = config.RUNS_DIR / '_contracts'
MAX_TEXT_CHARS = 40000

_URL = re.compile(r'https?://\S+')
# Page 1 "Payment information" block: Zelle number, bank account, ABA / routing numbers, SWIFT
_BANK_LINE = re.compile(r'zelle|wires?:|bank of america|account\s*#|\baba\b|swift|routing|direct deposit', re.I)
_NOT_A_TERM = re.compile(r'sign|initial|image|attach|photo|\bid\b', re.I)


def _headers():
    return {'X-Auth-Token': os.environ.get('DOCUSEAL_API_KEY') or ''}


def _get(path):
    response = requests.get(f"{API_BASE}{path}", headers=_headers(), timeout=20)
    response.raise_for_status()
    return response.json()


def _pdf_text(url, cache_key):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{cache_key}.txt"
    if cached.exists():
        return cached.read_text(encoding='utf-8')
    from pypdf import PdfReader
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    reader = PdfReader(io.BytesIO(response.content))
    text = "\n".join(page.extract_text() or '' for page in reader.pages)
    cached.write_text(text, encoding='utf-8')
    return text


def clean_text(text):
    """Drops bank / payment-account lines and links, and squeezes blank lines."""
    lines = []
    removed = False
    for line in (text or '').splitlines():
        if _BANK_LINE.search(line):
            removed = True
            continue
        line = _URL.sub('[link removed]', line).rstrip()
        if line.strip() or (lines and lines[-1]):
            lines.append(line if line.strip() else '')
    if removed:
        lines.insert(0, "[bank / payment account details removed - route payment-method questions to Janna]")
    return "\n".join(lines).strip()


def _value_lines(submitter):
    lines = []
    for item in submitter.get('values') or []:
        field, value = str(item.get('field') or ''), item.get('value')
        if isinstance(value, (list, dict)) or value in (None, '') or _NOT_A_TERM.search(field):
            continue
        value = str(value)
        if _URL.search(value):
            continue
        lines.append(f"- {field}: {' '.join(value.split())}")
    return lines


def _when(value):
    if not value:
        return ''
    try:
        from zoneinfo import ZoneInfo
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return moment.astimezone(ZoneInfo(config.TEAM_TIMEZONE)).strftime('%Y-%m-%d %H:%M') + f" {config.TIMEZONE_LABEL}"
    except ValueError:
        return value


def contract_for_booking(booking):
    """Text block for the agent: status, filled-in terms and the contract text. Never raises."""
    if booking is None:
        return "No booking is linked to this chat - there is no contract to read."
    if not booking.contract_id:
        return (f"No contract on file for this booking (contract status in the CRM: {booking.contract_send_status or 'none'}). "
                "Do not state any contract terms.")
    if booking.contract_id == 'SANDBOX-CONTRACT':
        # The sandbox test story (mysite/ai_agent/sandbox_test/story.py): its contract is a text file, not a DocuSeal submission.
        # The tool runs in its own process, so this is read here and not patched by the runner.
        from mysite.ai_agent.sandbox_test import story
        try:
            return story.CONTRACT_FILE.read_text(encoding='utf-8')
        except OSError:
            return "No contract on file for this booking (sandbox: the story has no contract). Do not state any contract terms."
    try:
        submission = _get(f"/submissions/{booking.contract_id}")
    except Exception as e:
        return (f"The contract (DocuSeal #{booking.contract_id}) could not be loaded right now ({type(e).__name__}). "
                "Do not guess contract terms - say so in contract_basis.")

    template = submission.get('template') or {}
    submitter = (submission.get('submitters') or [{}])[0]
    signed = submission.get('status') == 'completed'
    lines = [f"CONTRACT: {template.get('name') or 'contract'} (DocuSeal submission #{booking.contract_id}, "
             f"template #{template.get('id')})"]
    if signed:
        lines.append(f"STATUS: SIGNED by the tenant on {_when(submission.get('completed_at') or submitter.get('completed_at'))}")
    else:
        lines.append(f"STATUS: NOT SIGNED yet (tenant status: {submitter.get('status') or 'unknown'}; "
                     f"sent {_when(submission.get('created_at'))})")
    values = _value_lines(submitter)
    lines.append("FILLED-IN TERMS (" + ("as signed" if signed else "as sent to the tenant") + "):")
    lines += values or ["- (none)"]

    text, source = '', ''
    try:
        documents = submission.get('documents') or []
        if signed and documents:
            text = _pdf_text(documents[0]['url'], f"submission-{booking.contract_id}")
            source = 'the signed contract PDF'
        elif template.get('id'):
            full = _get(f"/templates/{template['id']}")
            docs = full.get('documents') or []
            if docs:
                stamp = hashlib.sha1(str(full.get('updated_at')).encode()).hexdigest()[:10]
                text = _pdf_text(docs[0]['url'], f"template-{template['id']}-{stamp}")
                source = 'the contract template (the blanks are the FILLED-IN TERMS above)'
    except Exception as e:
        lines.append(f"CONTRACT TEXT: could not be read ({type(e).__name__}) - use only the terms above.")
    if text:
        text = clean_text(text)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n[... cut: the contract is longer ...]"
        lines += ["", f"CONTRACT TEXT (from {source}; the PDF layout can put values away from their labels - trust "
                      f"FILLED-IN TERMS for names, dates and amounts):", text]
    return "\n".join(lines)
