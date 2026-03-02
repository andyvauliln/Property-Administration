"""
Group chat logging - writes all group chat messages and AI handling details
to logs/group_chat_log.log in a scannable format.
"""
import logging
from django.utils import timezone

_group_chat_logger = logging.getLogger('mysite.group_chat_log')

_SEP = "=" * 70
_THIN = "-" * 50


def _ts():
    return timezone.now().strftime("%Y-%m-%d %H:%M:%S")


def _block(title, content):
    if content is None:
        content = "(none)"
    return f"\n{_SEP}\n{title}\n{_THIN}\n{content}\n"


def log_message_received(conversation_sid, author, body, event_type, message_sid, direction):
    """Log incoming message from webhook."""
    parts = [
        f"\n\n{_SEP}",
        f"MESSAGE RECEIVED | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"MessageSid:  {message_sid or 'N/A'}",
        f"EventType:   {event_type}",
        f"Author:      {author}",
        f"Direction:   {direction}",
        f"Body:\n{body or '(empty)'}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_customer_start(conversation_sid, author, body, apartment_id, booking_id):
    """Log start of AI customer answer flow."""
    parts = [
        f"\n\n{_SEP}",
        f"AI CUSTOMER FLOW START | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Author:       {author}",
        f"Apartment:    {apartment_id}",
        f"Booking:      {booking_id}",
        f"Message:      {body}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_customer_skipped(conversation_sid, body, reason="skippable message"):
    """Log when customer message is skipped (e.g. short ack)."""
    parts = [
        f"\n\n{_SEP}",
        f"AI CUSTOMER SKIPPED | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Reason:       {reason}",
        f"Message:      {body}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_customer_full(
    conversation_sid,
    message_body,
    context,
    system_prompt,
    user_prompt,
    model,
    temperature,
    max_tokens,
    answer,
    usage=None,
    context_sources=None,
    system_prompt_source=None,
    user_prompt_source=None,
):
    """Log full AI customer answer: context, prompts, model, response, usage, and sources."""
    usage_str = ""
    if usage:
        usage_str = (
            f"Prompt tokens: {getattr(usage, 'prompt_tokens', 'N/A')}, "
            f"Completion tokens: {getattr(usage, 'completion_tokens', 'N/A')}, "
            f"Total: {getattr(usage, 'total_tokens', 'N/A')}"
        )
    else:
        usage_str = "N/A"

    parts = [
        f"\n\n{_SEP}",
        f"AI CUSTOMER ANSWER | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Model:        {model}",
        f"Temperature:  {temperature}",
        f"Max tokens:   {max_tokens}",
        f"Usage:        {usage_str}",
    ]
    if system_prompt_source or user_prompt_source:
        parts.append(f"System prompt: {system_prompt_source or 'N/A'}")
        parts.append(f"User prompt:  {user_prompt_source or 'N/A'}")
    if context_sources:
        sources_str = ", ".join(f"{k}={v}" for k, v in sorted(context_sources.items()))
        parts.append(f"Context used: {sources_str}")
    parts.extend([
        _block("TENANT MESSAGE", message_body),
        _block("CONTEXT (sent to AI)", context),
        _block("SYSTEM PROMPT", system_prompt),
        _block("USER PROMPT", user_prompt),
        _block("AI ANSWER", answer),
    ])
    _group_chat_logger.info("\n".join(parts))


def log_ai_customer_no_answer(conversation_sid, message_body, raw_answer=None):
    """Log when AI returns NO_ANSWER or empty."""
    parts = [
        f"\n\n{_SEP}",
        f"AI CUSTOMER NO_ANSWER | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Message:      {message_body}",
        f"Raw AI:       {raw_answer or '(empty)'}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_customer_sent(conversation_sid, answer):
    """Log when AI reply was sent to chat."""
    parts = [
        f"\n\n{_SEP}",
        f"AI REPLY SENT | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Reply:\n{answer}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_manager_start(conversation_sid, author, body, apartment_id):
    """Log start of AI manager knowledge extraction."""
    parts = [
        f"\n\n{_SEP}",
        f"AI MANAGER EXTRACT START | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Author:       {author}",
        f"Apartment:    {apartment_id}",
        f"Message:      {body}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_manager_check(conversation_sid, check_content, model, has_value, prompt_source=None):
    """Log step 1: check if message has extractable knowledge."""
    parts = [
        f"\n\n{_SEP}",
        f"AI MANAGER CHECK (YES/NO) | {_ts()}",
        _THIN,
        f"Conversation:   {conversation_sid}",
        f"Model:          {model}",
        f"Has value:      {has_value}",
        f"Prompt source:  {prompt_source or 'N/A'}",
        _block("CHECK PROMPT", check_content),
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_manager_merge(
    conversation_sid,
    apartment_id,
    knowledge_base_before,
    message_body,
    merge_content,
    model,
    updated_notes,
    saved,
    prompt_source=None,
):
    """Log step 2: merge knowledge into apartment notes."""
    parts = [
        f"\n\n{_SEP}",
        f"AI MANAGER MERGE | {_ts()}",
        _THIN,
        f"Conversation:    {conversation_sid}",
        f"Apartment:       {apartment_id}",
        f"Model:           {model}",
        f"Saved to DB:     {saved}",
        f"Prompt source:   {prompt_source or 'N/A'}",
        _block("KB BEFORE", knowledge_base_before or "(empty)"),
        _block("MANAGER MESSAGE", message_body),
        _block("MERGE PROMPT", merge_content),
        _block("UPDATED NOTES", updated_notes),
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_manager_no_extract(conversation_sid, body, reason="NO from check"):
    """Log when manager message has no extractable knowledge."""
    parts = [
        f"\n\n{_SEP}",
        f"AI MANAGER NO EXTRACT | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Reason:       {reason}",
        f"Message:      {body}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_disabled(conversation_sid, author, body):
    """Log when AI processing is disabled."""
    parts = [
        f"\n\n{_SEP}",
        f"AI DISABLED | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Author:       {author}",
        f"Message:      {body}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_no_conv_link(conversation_sid, author, body):
    """Log when conversation has no apartment/booking link."""
    parts = [
        f"\n\n{_SEP}",
        f"NO CONV LINK (skip AI) | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Author:       {author}",
        f"Message:      {body}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_new_group_created(conversation_sid, author, new_conversation_sid, participants):
    """Log when new group conversation is created."""
    parts = [
        f"\n\n{_SEP}",
        f"NEW GROUP CONVERSATION CREATED | {_ts()}",
        _THIN,
        f"Old conv:     {conversation_sid}",
        f"New conv:     {new_conversation_sid}",
        f"Author:       {author}",
        f"Participants: {participants}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_message_forwarded(conversation_sid, author, body, target_conversation_sid):
    """Log when message is forwarded to target conversation."""
    parts = [
        f"\n\n{_SEP}",
        f"MESSAGE FORWARDED | {_ts()}",
        _THIN,
        f"From conv:    {conversation_sid}",
        f"To conv:      {target_conversation_sid}",
        f"Author:       {author}",
        f"Body:         {body}",
    ]
    _group_chat_logger.info("\n".join(parts))


def log_ai_error(conversation_sid, stage, error):
    """Log AI-related errors."""
    parts = [
        f"\n\n{_SEP}",
        f"AI ERROR | {_ts()}",
        _THIN,
        f"Conversation: {conversation_sid}",
        f"Stage:        {stage}",
        f"Error:        {error}",
    ]
    _group_chat_logger.info("\n".join(parts))
