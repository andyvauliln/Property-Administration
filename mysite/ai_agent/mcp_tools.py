"""
Read-only MCP tools for one agent run (stdio, started by the claude CLI).

Scoped by environment to ONE conversation (plus the same tenant's other chats, merged - see
conversation_groups), so the tenant-facing agent can never read another tenant's data:
  AI_AGENT_CONVERSATION_SID    conversation the run belongs to (required)
  AI_AGENT_UNTIL_MESSAGE_ID    newest TwilioMessage.id the run may see (point-in-time replay)
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))
# claude starts this process from an empty run directory; settings.LOGGING uses relative paths
os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
# FastMCP runs handlers inside asyncio; all tools here are read-only ORM queries.
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

# stdout is the MCP channel: anything Django prints while loading must go to stderr
_real_stdout = sys.stdout
sys.stdout = sys.stderr
import django  # noqa: E402
django.setup()
sys.stdout = _real_stdout

from mcp.server.fastmcp import FastMCP  # noqa: E402

CONVERSATION_SID = os.environ.get("AI_AGENT_CONVERSATION_SID", "")
UNTIL_MESSAGE_ID = os.environ.get("AI_AGENT_UNTIL_MESSAGE_ID", "")

mcp = FastMCP("crm")


def _chats():
    from mysite.ai_agent.inputs import tenant_chats_block
    _block, others, sids = tenant_chats_block(CONVERSATION_SID)
    return others, sids


def _visible_messages():
    from mysite.ai_agent.inputs import _until
    from mysite.models import TwilioMessage

    if not CONVERSATION_SID:
        return TwilioMessage.objects.none()
    _others, sids = _chats()
    qs = TwilioMessage.objects.filter(conversation_sid__in=sids).exclude(message_sid__startswith='KB-UPDATE-')
    if UNTIL_MESSAGE_ID.isdigit():
        until = qs.filter(id=int(UNTIL_MESSAGE_ID)).first()
        if until:
            qs = _until(qs, until)
    return qs


def _render(messages):
    from mysite.ai_agent.inputs import _ai_answers, format_message_line

    others, sids = _chats()
    ai_answers = _ai_answers(sids)
    lines = [format_message_line(m, ai_answers, other_chats=others) for m in messages]
    return "\n".join(lines) if lines else "(no messages)"


@mcp.tool()
def get_chat_history(limit: int = 40, skip_newest: int = 0) -> str:
    """Older messages of this tenant's chat(s), oldest first ([other chat #N] = another chat of the same tenant). skip_newest pages further back."""
    limit = max(1, min(int(limit), 100))
    skip_newest = max(0, int(skip_newest))
    messages = list(_visible_messages().order_by('-message_timestamp', '-id')[skip_newest:skip_newest + limit])
    messages.reverse()
    return _render(messages)


@mcp.tool()
def search_chat_history(query: str, limit: int = 20) -> str:
    """Messages of this tenant's chat(s) that contain the text (case-insensitive), oldest first."""
    query = (query or '').strip()
    if not query:
        return "(empty query)"
    limit = max(1, min(int(limit), 50))
    messages = list(
        _visible_messages().filter(body__icontains=query).order_by('-message_timestamp', '-id')[:limit]
    )
    messages.reverse()
    return _render(messages)


@mcp.tool()
def get_contract() -> str:
    """This tenant's contract: signed or not, the filled-in terms (dates, rent, deposit, fees) and the full contract text. Use it for any legal / contract question."""
    from mysite.ai_agent.contract import contract_for_booking
    from mysite.models import Booking, TwilioConversation

    conversation = TwilioConversation.objects.filter(conversation_sid=CONVERSATION_SID).first()
    booking = None
    if conversation and conversation.booking_id:
        booking = Booking.objects.select_related('tenant').filter(id=conversation.booking_id).first()
    return contract_for_booking(booking)


if __name__ == "__main__":
    mcp.run(transport="stdio")
