from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Max, Q
from django.utils import timezone
from django.core.paginator import Paginator
from mysite.models import Apartment, TwilioConversation, TwilioMessage, User, ChatMessageTemplate
from mysite.views.messaging import (
    send_messsage_by_sid,
    save_message_to_db,
    delete_conversation as twilio_delete_conversation,
    delete_message as twilio_delete_message,
    delete_all_messages as twilio_delete_all_messages,
    _notify_manager_chat_delivery_failed,
    get_global_knowledge_base_text,
    save_global_knowledge_base_text,
    list_conversation_kb_eligible,
    generate_conversation_kb_step,
    generate_customer_ai_answer_for_message,
    generate_all_customer_ai_answers,
    get_ai_prompt_template,
    save_ai_prompt_template,
    is_customer_message,
    get_customer_message_body,
    generate_answer_rule_text,
    append_rule_to_ai_answer_system,
    get_answer_rule_modal_context,
    is_answer_rule_eligible_message,
    get_answer_rule_message_body,
    get_kb_rule_modal_context,
    is_kb_rule_eligible_message,
    get_kb_manager_message_body,
    generate_kb_rule_text,
    append_rule_to_kb_extract_check,
    should_run_kb_extraction_for_message,
    _is_ai_assistant_globally_enabled,
    _should_send_ai_to_group,
    _enqueue_for_ai_agent,
    _enqueue_staff_for_ai_agent,
    KB_EXTRACT_APARTMENT_CHECK_KEY,
    KB_EXTRACT_APARTMENT_MERGE_KEY,
    KB_EXTRACT_GLOBAL_CHECK_KEY,
    KB_EXTRACT_GLOBAL_MERGE_KEY,
    KB_EXTRACT_PROMPT_KEYS,
    AI_ANSWER_SYSTEM_KEY,
    AI_ANSWER_USER_KEY,
    AI_ANSWER_RULE_GENERATE_KEY,
    AI_KB_RULE_GENERATE_KEY,
    CHAT_EDITABLE_PROMPT_KEYS,
)
from mysite.unified_logger import log_error, log_info, logger
from mysite.ai_agent.config import get_ai_backend
from mysite.views.ai_agent_views import get_ai_activity
from mysite.error_logger import log_exception
import json
from uuid import uuid4


MANAGER_PHONE = "+15612205252"
MANAGER_PHONE_2 = "+17282001917"
MANAGER_PHONE_3 = "+15614603904"
MANAGER_PHONE_4 = "+15618438867"
MANAGER_PHONE_NAMES = {
    MANAGER_PHONE_4: "Janna",
}
ASSISTANT_IDENTITY = "ASSISTANT"
# This is the projected address used for the assistant participant in Twilio Conversations.
ASSISTANT_PROJECTED_PHONE = "+13153524379"
# Other system phones that can appear as authors.
SYSTEM_PHONES = {"+13153524379", "+17282001917", MANAGER_PHONE, MANAGER_PHONE_2, MANAGER_PHONE_3, MANAGER_PHONE_4}


def _is_e164(value: str) -> bool:
    if not value:
        return False
    value = str(value).strip()
    return value.startswith("+") and value[1:].isdigit()


def _format_number_name(number: str, name: str) -> str:
    number = (number or "").strip()
    name = (name or "").strip() or "Unknown"
    return f"{number} ({name})" if number else f"Unknown ({name})"


def _dedupe_preserve_order(items):
    seen = set()
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _build_conversation_participants(conversation):
    """
    Build a participant list based on booking + message authors, enriched with user names.
    Each item includes `formatted` in the requested: number (name).
    """
    raw_candidates = []

    # Booking tenant is the best "customer" participant.
    tenant_phone = None
    tenant_name = None
    if conversation.booking and conversation.booking.tenant:
        tenant = conversation.booking.tenant
        tenant_phone = (tenant.phone or "").strip()
        tenant_name = (tenant.full_name or "").strip() or None
        if tenant_phone:
            raw_candidates.append(tenant_phone)

    # Add any author values we have stored for this conversation.
    try:
        raw_candidates.extend(
            [a for a in conversation.messages.values_list("author", flat=True).distinct() if a]
        )
    except Exception:
        pass

    # Ensure we always include manager + assistant.
    raw_candidates.extend([MANAGER_PHONE, MANAGER_PHONE_2, MANAGER_PHONE_3, MANAGER_PHONE_4, ASSISTANT_IDENTITY, "Virtual Assistant"])

    raw_candidates = _dedupe_preserve_order([str(x).strip() for x in raw_candidates if str(x).strip()])

    # Gather phone numbers we can enrich via User table.
    phones = [x for x in raw_candidates if _is_e164(x)]
    phones = _dedupe_preserve_order(phones)

    users_by_phone = {}
    if phones:
        for u in User.objects.filter(phone__in=phones).only("phone", "full_name", "role"):
            users_by_phone[(u.phone or "").strip()] = u

    participants = []
    assistant_added = False
    for raw in raw_candidates:
        # Assistant participant: show projected phone (Assistant)
        if raw in (ASSISTANT_IDENTITY, "Virtual Assistant") and not assistant_added:
            participants.append(
                {
                    "raw_keys": [ASSISTANT_IDENTITY, ASSISTANT_PROJECTED_PHONE, "Virtual Assistant"],
                    "number": ASSISTANT_PROJECTED_PHONE,
                    "name": "Assistant",
                    "formatted": _format_number_name(ASSISTANT_PROJECTED_PHONE, "Assistant"),
                }
            )
            assistant_added = True
            continue

        # Phone participant
        if _is_e164(raw):
            name = None
            if tenant_phone and raw == tenant_phone and tenant_name:
                name = tenant_name
            elif raw in (MANAGER_PHONE, MANAGER_PHONE_2, MANAGER_PHONE_3, MANAGER_PHONE_4):
                # Prefer a real user name if present, fallback to "Manager"
                u = users_by_phone.get(raw)
                name = (u.full_name or "").strip() if u and u.full_name else MANAGER_PHONE_NAMES.get(raw, "Manager")
            else:
                u = users_by_phone.get(raw)
                if u and u.full_name:
                    name = u.full_name.strip()

            participants.append(
                {
                    "raw_keys": [raw],
                    "number": raw,
                    "name": name or "Unknown",
                    "formatted": _format_number_name(raw, name or "Unknown"),
                }
            )
            continue

        # Other identities / unknown formats: best-effort display.
        participants.append(
            {
                "raw_keys": [raw],
                "number": raw,
                "name": raw,
                "formatted": _format_number_name(raw, raw),
            }
        )

    # Dedupe participants by their formatted output while preserving order.
    deduped = []
    seen_fmt = set()
    for p in participants:
        if p["formatted"] in seen_fmt:
            continue
        seen_fmt.add(p["formatted"])
        deduped.append(p)
    return deduped


def _build_author_display_map(participants):
    author_map = {}
    for p in participants:
        for key in p.get("raw_keys", []) or []:
            author_map[key] = p.get("formatted")
    return author_map


@login_required
def chat_list(request):
    """
    Display list of conversations sorted by last activity
    """
    # Get search query
    search_query = request.GET.get('q', '').strip()
    
    conversations_with_messages = TwilioConversation.objects.annotate(
        last_message_time=Max('messages__message_timestamp')
    ).filter(
        last_message_time__isnull=False
    )
    total_all_conversations = conversations_with_messages.count()

    if search_query:
        conversations = TwilioConversation.objects.annotate(
            last_message_time=Max('messages__message_timestamp')
        )
    else:
        conversations = conversations_with_messages
    
    # Apply search filter if provided
    if search_query:
        search_filters = (
            Q(booking__tenant__full_name__icontains=search_query) |
            Q(booking__tenant__phone__icontains=search_query) |
            Q(apartment__name__icontains=search_query) |
            Q(friendly_name__icontains=search_query) |
            Q(conversation_sid__icontains=search_query) |
            Q(messages__author__icontains=search_query)
        )
        if search_query.isdigit():
            search_filters |= Q(id=int(search_query))
        conversations = conversations.filter(search_filters).distinct()
    
    conversations = conversations.order_by('-last_message_time')

    total_conversations = conversations.count()
    
    # Prepare conversation data with display information
    conversation_data = []
    for conv in conversations:
        # Get tenant info from conversation messages
        display_info = get_conversation_display_info(conv)
        
        # Get latest message preview
        latest_message = conv.messages.order_by('-message_timestamp').first()
        
        conversation_data.append({
            'conversation': conv,
            'display_info': display_info,
            'latest_message': latest_message,
            'message_count': conv.messages.count(),
        })
    
    return render(request, 'chat/chat_list.html', {
        'title': 'Chat Interface',
        'conversations': conversation_data,
        'search_query': search_query,
        'total_conversations': total_conversations,
        'total_all_conversations': total_all_conversations,
    })


@login_required
def export_group_chats_md(request):
    """Download group chats as Markdown for the current list (or current search)."""
    from mysite.group_chat_md_export import GroupChatMarkdownExporter

    search_query = request.GET.get("q", "").strip()
    exporter = GroupChatMarkdownExporter()
    conversations = exporter.get_list_conversations(search_query)
    markdown = exporter.render_markdown(conversations, search_query=search_query)

    filename = "group_chats_search.md" if search_query else "group_chats.md"
    response = HttpResponse(markdown, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def chat_detail(request, conversation_sid):
    """
    Display specific conversation with messages
    """
    conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)

    chat_messages = list(conversation.messages.order_by('message_timestamp'))
    
    # Get conversation display info
    display_info = get_conversation_display_info(conversation)
    author_display_map = _build_author_display_map(display_info.get("participants") or [])

    for m in chat_messages:
        raw_author = (m.author or "").strip()
        m.author_display = author_display_map.get(raw_author, _format_number_name(raw_author, "Unknown"))
        m.is_customer_message = is_customer_message(m)
        m.customer_message_body = get_customer_message_body(m) if m.is_customer_message else ''
        m.is_kb_eligible = should_run_kb_extraction_for_message(m)
    
    chat_templates = list(
        ChatMessageTemplate.objects.order_by("name", "-created_at").values("id", "name", "body")
    )
    apartment = conversation.apartment if conversation.apartment_id else None
    ai_assistant_enabled = _is_ai_assistant_globally_enabled()
    apartment_ai_group_chat_enabled = bool(apartment and apartment.ai_group_chat_enabled)
    ai_can_send_to_group = _should_send_ai_to_group(apartment)

    # Get all conversations for sidebar
    all_conversations = TwilioConversation.objects.annotate(
        last_message_time=Max('messages__message_timestamp')
    ).filter(
        last_message_time__isnull=False
    ).order_by('-last_message_time')
    
    sidebar_conversations = []
    for conv in all_conversations:
        sidebar_info = get_conversation_display_info(conv)
        latest_message = conv.messages.order_by('-message_timestamp').first()
        sidebar_conversations.append({
            'conversation': conv,
            'display_info': sidebar_info,
            'latest_message': latest_message,
            'is_active': conv.conversation_sid == conversation_sid,
        })
    
    return render(request, 'chat/chat_detail.html', {
        'title': f'Chat - {display_info["name"]}',
        'conversation': conversation,
        'chat_messages': chat_messages,
        'display_info': display_info,
        'sidebar_conversations': sidebar_conversations,
        'outbound_author_display': author_display_map.get(ASSISTANT_IDENTITY, _format_number_name(ASSISTANT_PROJECTED_PHONE, "Assistant")),
        'chat_templates': chat_templates,
        'message_count': len(chat_messages),
        'ai_ready': bool(conversation.apartment_id and conversation.booking_id),
        'ai_assistant_enabled': ai_assistant_enabled,
        'apartment_ai_group_chat_enabled': apartment_ai_group_chat_enabled,
        'ai_backend': get_ai_backend(),
        'ai_activity': get_ai_activity(conversation.conversation_sid),
        'ai_can_send_to_group': ai_can_send_to_group,
        'global_knowledge_base': get_global_knowledge_base_text(),
    })


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def send_message(request, conversation_sid):
    """
    Send a message to a conversation
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        
        # Get message content and sender_type from request
        data = json.loads(request.body)
        original_message = data.get('message', '').strip()
        sender_type = data.get('sender_type', 'manager').lower()
        send_to_group_chat = data.get('send_to_group_chat', True)
        if isinstance(send_to_group_chat, str):
            send_to_group_chat = send_to_group_chat.lower() == 'true'
        
        if not original_message:
            return JsonResponse({'error': 'Message content is required'}, status=400)
        
        message_content = original_message
        if sender_type == 'client':
            message_content = f"{original_message} (+++)"
        
        manager_phone = "+15612205252"
        sent_message = None
        
        try:
            if send_to_group_chat:
                try:
                    send_messsage_by_sid(
                        conversation_sid=conversation_sid,
                        author='ASSISTANT',
                        message=message_content,
                        sender_phone=manager_phone,
                        receiver_phone=None
                    )
                except Exception:
                    tenant_name = "N/A"
                    tenant_phone = "N/A"
                    if conversation.booking_id:
                        from mysite.models import Booking
                        b = Booking.objects.filter(id=conversation.booking_id).select_related('tenant').first()
                        if b and b.tenant:
                            tenant_name = b.tenant.full_name or "N/A"
                            tenant_phone = b.tenant.phone or "N/A"
                    _notify_manager_chat_delivery_failed(
                        tenant_name, tenant_phone, message_content, conversation_sid
                    )
                    raise
            else:
                local_message_sid = f"LOCAL-{uuid4().hex}"
                sent_message = save_message_to_db(
                    message_sid=local_message_sid,
                    conversation_sid=conversation_sid,
                    author='ASSISTANT',
                    body=message_content,
                    direction='outbound'
                )
                if sent_message is None:
                    raise Exception("Failed to save local message to DB")
            
            log_info(f"Message sent from chat interface to conversation {conversation_sid}")
            try:
                from mysite.group_chat_logger import log_message_received
                log_message_received(
                    conversation_sid=conversation_sid,
                    author='ASSISTANT',
                    body=message_content,
                    event_type='ui_send' if send_to_group_chat else 'ui_send_local',
                    message_sid=sent_message.message_sid if sent_message else None,
                    direction='outbound',
                )
            except Exception:
                pass
            
            # When sent as client: process AI synchronously (webhook may not fire for API-created messages)
            ai_result = None
            if (
                sender_type == 'client' and conversation.apartment_id and conversation.booking_id and sent_message
                and _enqueue_for_ai_agent(
                    conversation_sid, sent_message.message_sid, original_message,
                    send_allowed=bool(send_to_group_chat), reply_author='ASSISTANT',
                    sender_phone=manager_phone, source='chat_ui',
                )
            ):
                # Claude agent backend: the ai-agent worker answers, the page polls ai-agent-status
                ai_result = {'ai_queued': True, 'message_id': sent_message.id}
            elif sender_type == 'client' and conversation.apartment_id and conversation.booking_id:
                try:
                    from mysite.models import Booking, TwilioMessage
                    from mysite.views.messaging import ai_answer_customer_detailed, _persist_customer_ai_result
                    from mysite.group_chat_logger import log_ai_customer_start, log_ai_customer_sent
                    apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=conversation.apartment_id)
                    booking = Booking.objects.select_related('tenant').get(id=conversation.booking_id)
                    if len(original_message.strip()) > 3:
                        log_ai_customer_start(conversation_sid, 'ASSISTANT', original_message, conversation.apartment_id, conversation.booking_id)
                        ai_detail = ai_answer_customer_detailed(conversation_sid, original_message, apartment, booking)
                        ai_resp = ai_detail.get("answer")
                        if ai_resp:
                            ai_sent_to_chat = bool(send_to_group_chat and _should_send_ai_to_group(apartment))
                            if ai_sent_to_chat:
                                try:
                                    send_messsage_by_sid(conversation_sid, 'ASSISTANT', ai_resp, manager_phone, None)
                                except Exception:
                                    _notify_manager_chat_delivery_failed(
                                        booking.tenant.full_name or "N/A",
                                        booking.tenant.phone or "N/A",
                                        ai_resp,
                                        conversation_sid,
                                    )
                                    raise

                            target_message = sent_message
                            if not target_message:
                                target_message = TwilioMessage.objects.filter(
                                    conversation_sid=conversation_sid,
                                    body=message_content,
                                ).order_by('-message_timestamp').first()

                            if target_message:
                                target_message.ai_response = ai_resp
                                target_message.ai_response_why = ai_detail.get("why")
                                target_message.ai_sent_to_chat = ai_sent_to_chat
                                target_message.save(update_fields=['ai_response', 'ai_response_why', 'ai_sent_to_chat', 'updated_at'])
                            elif sent_message:
                                _persist_customer_ai_result(sent_message.message_sid, ai_detail, sent_to_chat=ai_sent_to_chat)

                            ai_result = {
                                'ai_response': ai_resp,
                                'ai_response_why': ai_detail.get("why"),
                                'ai_sent_to_chat': ai_sent_to_chat,
                                'message_id': target_message.id if target_message else None,
                            }
                            log_ai_customer_sent(conversation_sid, ai_resp)
                        elif ai_detail.get("why") or ai_detail.get("no_answer"):
                            target_message = sent_message
                            if target_message:
                                _persist_customer_ai_result(target_message.message_sid, ai_detail, sent_to_chat=False)
                            ai_result = {
                                'ai_response': None,
                                'ai_response_why': ai_detail.get("why"),
                                'ai_sent_to_chat': False,
                                'message_id': target_message.id if target_message else None,
                            }
                except Exception as e:
                    log_exception(error=e, context="Chat - AI answer (client)", additional_info={'conversation_sid': conversation_sid})
            elif sender_type == 'client' and not conversation.booking_id:
                ai_result = {
                    'ai_skipped_reason': 'This conversation has no linked booking — AI test mode requires both apartment and booking.',
                }
            elif sender_type == 'manager' and conversation.apartment_id:
                if sent_message and conversation.booking_id and send_to_group_chat:
                    # Claude agent: a manager's message updates issues / follow-ups (no-op on the legacy backend)
                    _enqueue_staff_for_ai_agent(conversation_sid, sent_message.message_sid, original_message, source='chat_ui')
                try:
                    from mysite.models import Apartment, TwilioMessage
                    from mysite.views.messaging import ai_extract_knowledge, KB_SUFFIX, _extract_marked_body, _is_skippable_message
                    from mysite.group_chat_logger import log_ai_manager_start

                    # UI manager messages should behave like manager-originated messages in webhook:
                    # extract from full body, while still accepting explicit (+) marker.
                    body_for_extract = _extract_marked_body(original_message, KB_SUFFIX) or original_message
                    if body_for_extract and not _is_skippable_message(body_for_extract):
                        apartment = Apartment.objects.get(id=conversation.apartment_id)
                        log_ai_manager_start(conversation_sid, 'ASSISTANT', body_for_extract, conversation.apartment_id)
                        history_msg = sent_message
                        if not history_msg:
                            history_msg = TwilioMessage.objects.filter(
                                conversation_sid=conversation_sid,
                                body=message_content,
                            ).order_by('-message_timestamp', '-id').first()
                        ai_extract_knowledge(
                            conversation_sid, body_for_extract, apartment,
                            conversation=conversation, history_before=history_msg,
                        )
                except Exception as e:
                    log_exception(error=e, context="Chat - AI extract (manager)", additional_info={'conversation_sid': conversation_sid})
            
            response_payload = {
                'success': True,
                'message': 'Message sent successfully',
            }
            if ai_result:
                response_payload.update(ai_result)
            message_id = response_payload.get('message_id')
            if not message_id and sent_message is not None:
                message_id = sent_message.id
            if not message_id:
                latest = conversation.messages.filter(
                    body=message_content, direction='outbound'
                ).order_by('-message_timestamp').first()
                if latest:
                    message_id = latest.id
            if message_id:
                response_payload['message_id'] = message_id
            return JsonResponse(response_payload)
            
        except Exception as e:
            log_info(f"Error sending message from chat interface: {e}")
            log_exception(
                error=e,
                context="Chat - Send Message to Twilio",
                additional_info={
                    'conversation_sid': conversation_sid,
                    'message_length': len(message_content) if message_content else 0
                }
            )
            return JsonResponse({
                'error': f'Failed to send message: {str(e)}'
            }, status=500)
            
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        log_exception(
            error=e,
            context="Chat - Send Message View",
            additional_info={'conversation_sid': conversation_sid}
        )
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def delete_all_chat_messages(request, conversation_sid):
    """
    Delete all messages from Twilio and DB. Keeps the conversation.
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        sid = conversation.conversation_sid
        try:
            twilio_delete_all_messages(sid)
        except Exception as e:
            log_info(f"Twilio delete all messages failed: {e}", category='sms')
        conversation.messages.all().delete()
        log_info(f"Deleted all messages from conversation {sid}")
        return redirect('chat_detail', conversation_sid=conversation_sid)
    except Exception as e:
        log_exception(error=e, context="Chat - Delete All Messages", additional_info={'conversation_sid': conversation_sid})
        return redirect('chat_detail', conversation_sid=conversation_sid)


@login_required
@require_http_methods(["POST"])
def delete_chat_conversation(request, conversation_sid):
    """
    Delete conversation from Twilio and DB (messages cascade).
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        sid = conversation.conversation_sid
        try:
            twilio_delete_conversation(sid)
        except Exception as e:
            log_info(f"Twilio delete failed (may already be deleted): {e}", category='sms')
        conversation.delete()
        log_info(f"Deleted chat conversation {sid} from DB")
        return redirect('chat_list')
    except Exception as e:
        log_exception(error=e, context="Chat - Delete Conversation", additional_info={'conversation_sid': conversation_sid})
        return redirect('chat_detail', conversation_sid=conversation_sid)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def update_chat_knowledge_base(request, conversation_sid):
    """
    Update apartment and global knowledge bases from chat detail modal.
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        if not conversation.apartment_id:
            return JsonResponse({'error': 'Conversation must be linked to an apartment.'}, status=400)

        payload = {}
        if request.body:
            try:
                payload = json.loads(request.body)
            except json.JSONDecodeError:
                payload = {}
        if not payload:
            payload = request.POST.dict()

        apartment_kb = payload.get('apartment_kb')
        if apartment_kb is None:
            apartment_kb = payload.get('knowledge_base')
        global_kb = payload.get('global_kb')

        if apartment_kb is not None:
            conversation.apartment.knowledge_base = str(apartment_kb).strip() or None
            conversation.apartment.save(update_fields=['knowledge_base', 'updated_at'])

        if global_kb is not None:
            save_global_knowledge_base_text(str(global_kb).strip(), conversation_sid)

        log_info(f'Updated knowledge bases from chat for conversation {conversation_sid}')
        return JsonResponse({
            'success': True,
            'apartment_kb': conversation.apartment.knowledge_base or '',
            'global_kb': get_global_knowledge_base_text(),
        })
    except Exception as e:
        log_exception(error=e, context='Chat - Update Knowledge Base', additional_info={'conversation_sid': conversation_sid})
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET", "POST"])
@csrf_exempt
def knowledge_base_prompt(request, prompt_key):
    """
    Load or save bulk KB generation prompts used by the chat modal.
    """
    if prompt_key not in CHAT_EDITABLE_PROMPT_KEYS:
        return JsonResponse({'error': 'Unknown prompt key.'}, status=404)
    try:
        if request.method == 'GET':
            content, from_db, description = get_ai_prompt_template(prompt_key)
            if content is None:
                return JsonResponse({'error': 'Prompt not found.'}, status=404)
            labels = {
                KB_EXTRACT_APARTMENT_CHECK_KEY: 'Apartment: when to save (prompt)',
                KB_EXTRACT_APARTMENT_MERGE_KEY: 'Apartment: how to update (prompt)',
                KB_EXTRACT_GLOBAL_CHECK_KEY: 'Global: when to save (prompt)',
                KB_EXTRACT_GLOBAL_MERGE_KEY: 'Global: how to update (prompt)',
                AI_ANSWER_SYSTEM_KEY: 'Answer instructions prompt',
                AI_ANSWER_USER_KEY: 'Answer request prompt',
                AI_ANSWER_RULE_GENERATE_KEY: 'Teach-answer-rule prompt',
                AI_KB_RULE_GENERATE_KEY: 'Teach-KB-rule prompt',
            }
            return JsonResponse({
                'success': True,
                'prompt_key': prompt_key,
                'label': labels.get(prompt_key, prompt_key),
                'content': content,
                'description': description,
                'from_db': from_db,
            })

        payload = {}
        if request.body:
            try:
                payload = json.loads(request.body)
            except json.JSONDecodeError:
                payload = {}
        if not payload:
            payload = request.POST.dict()
        content = payload.get('content')
        if content is None:
            return JsonResponse({'error': 'content is required.'}, status=400)
        description = payload.get('description')
        entry = save_ai_prompt_template(prompt_key, content, description=description)
        return JsonResponse({
            'success': True,
            'prompt_key': prompt_key,
            'content': entry.content or '',
            'description': entry.description or '',
            'from_db': True,
        })
    except Exception as e:
        log_exception(error=e, context='Chat - Knowledge Base Prompt', additional_info={'prompt_key': prompt_key})
        return JsonResponse({'error': str(e)}, status=500)


def _parse_json_body(request):
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


@login_required
@require_http_methods(["GET"])
def answer_rule_context(request, conversation_sid, message_id):
    """Load Add Rule modal context for a customer message (tenant AI answers)."""
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        context = get_answer_rule_modal_context(message)
        if not context:
            return JsonResponse({'error': 'Not a customer message.'}, status=400)
        return JsonResponse({'success': True, **context})
    except Exception as e:
        log_exception(
            error=e,
            context='Chat - Answer Rule Context',
            additional_info={'conversation_sid': conversation_sid, 'message_id': message_id},
        )
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def generate_answer_rule(request, conversation_sid, message_id):
    """Generate a system-prompt rule from client message + correct answer."""
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        if not is_answer_rule_eligible_message(message):
            return JsonResponse({'success': False, 'error': 'Not a customer message.'}, status=400)

        payload = _parse_json_body(request)
        correct_answer = (payload.get('correct_answer') or '').strip()
        generate_prompt = payload.get('generate_prompt')
        ai_response = payload.get('ai_response')
        if ai_response is None:
            ai_response = message.ai_response

        result = generate_answer_rule_text(
            get_answer_rule_message_body(message),
            correct_answer,
            ai_response=ai_response,
            generate_prompt=generate_prompt,
        )
        status = 200 if result.get('success') else 400
        return JsonResponse(result, status=status)
    except Exception as e:
        log_exception(
            error=e,
            context='Chat - Generate Answer Rule',
            additional_info={'conversation_sid': conversation_sid, 'message_id': message_id},
        )
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def save_answer_rule(request, conversation_sid, message_id):
    """Append rule text to ai_answer_system prompt in DB."""
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        if not is_answer_rule_eligible_message(message):
            return JsonResponse({'success': False, 'error': 'Not a customer message.'}, status=400)

        payload = _parse_json_body(request)
        rule = (payload.get('rule') or '').strip()
        if not rule:
            return JsonResponse({'success': False, 'error': 'Rule is required.'}, status=400)

        updated_prompt = append_rule_to_ai_answer_system(rule)
        return JsonResponse({
            'success': True,
            'rule': rule if rule.startswith('-') else f'- {rule}',
            'system_prompt_length': len(updated_prompt or ''),
        })
    except Exception as e:
        log_exception(
            error=e,
            context='Chat - Save Answer Rule',
            additional_info={'conversation_sid': conversation_sid, 'message_id': message_id},
        )
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def kb_rule_context(request, conversation_sid, message_id):
    """Load Add KB Rule modal context for a manager KB source message."""
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        context = get_kb_rule_modal_context(message)
        if not context:
            return JsonResponse({'error': 'Not a KB-eligible manager message.'}, status=400)
        return JsonResponse({'success': True, **context})
    except Exception as e:
        log_exception(
            error=e,
            context='Chat - KB Rule Context',
            additional_info={'conversation_sid': conversation_sid, 'message_id': message_id},
        )
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def generate_kb_rule(request, conversation_sid, message_id):
    """Generate a KB extract check rule from manager message + guidance."""
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        if not is_kb_rule_eligible_message(message):
            return JsonResponse({'success': False, 'error': 'Not a KB-eligible manager message.'}, status=400)

        payload = _parse_json_body(request)
        guidance = (payload.get('guidance') or '').strip()
        scope = payload.get('scope')
        rule_intent = payload.get('rule_intent')
        generate_prompt = payload.get('generate_prompt')
        kb_changes = payload.get('kb_changes')
        if kb_changes is None:
            kb_changes = message.ai_kb_changes

        result = generate_kb_rule_text(
            get_kb_manager_message_body(message),
            guidance,
            scope=scope,
            rule_intent=rule_intent,
            kb_changes=kb_changes,
            generate_prompt=generate_prompt,
        )
        status = 200 if result.get('success') else 400
        return JsonResponse(result, status=status)
    except Exception as e:
        log_exception(
            error=e,
            context='Chat - Generate KB Rule',
            additional_info={'conversation_sid': conversation_sid, 'message_id': message_id},
        )
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def save_kb_rule(request, conversation_sid, message_id):
    """Append rule text to apartment or global KB check prompt in DB."""
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        if not is_kb_rule_eligible_message(message):
            return JsonResponse({'success': False, 'error': 'Not a KB-eligible manager message.'}, status=400)

        payload = _parse_json_body(request)
        rule = (payload.get('rule') or '').strip()
        scope = payload.get('scope') or 'apartment'
        if not rule:
            return JsonResponse({'success': False, 'error': 'Rule is required.'}, status=400)

        updated_prompt, prompt_key = append_rule_to_kb_extract_check(rule, scope=scope)
        return JsonResponse({
            'success': True,
            'rule': rule if rule.startswith('-') else f'- {rule}',
            'scope': scope,
            'prompt_key': prompt_key,
            'check_prompt_length': len(updated_prompt or ''),
        })
    except Exception as e:
        log_exception(
            error=e,
            context='Chat - Save KB Rule',
            additional_info={'conversation_sid': conversation_sid, 'message_id': message_id},
        )
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def generate_message_ai_answer(request, conversation_sid, message_id):
    """Generate or regenerate AI tenant answer for one customer message (DB only)."""
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        result = generate_customer_ai_answer_for_message(message, conversation=conversation, save=True)
        status = 200 if result.get('success') else 400
        return JsonResponse(result, status=status)
    except Exception as e:
        log_exception(
            error=e,
            context='Chat - Generate Message AI Answer',
            additional_info={'conversation_sid': conversation_sid, 'message_id': message_id},
        )
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def generate_all_customer_ai_answers_view(request, conversation_sid):
    """Generate or regenerate AI answers for all customer messages (DB only)."""
    try:
        result = generate_all_customer_ai_answers(conversation_sid)
        status = 200 if result.get('success') else 400
        return JsonResponse(result, status=status)
    except Exception as e:
        log_exception(
            error=e,
            context='Chat - Generate All Customer AI Answers',
            additional_info={'conversation_sid': conversation_sid},
        )
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def generate_chat_knowledge_base(request, conversation_sid):
    """
    KB modal generate API.
    POST {} — list eligible manager message ids (fast, no AI).
    POST {message_id, apartment_kb, global_kb} — analyze one message and return updated drafts.
    """
    try:
        payload = _parse_json_body(request)
        message_id = payload.get('message_id')
        if message_id:
            result = generate_conversation_kb_step(
                conversation_sid,
                message_id,
                apartment_kb_draft=payload.get('apartment_kb'),
                global_kb_draft=payload.get('global_kb'),
            )
        else:
            result = list_conversation_kb_eligible(conversation_sid)
        status = 200 if result.get('success') else 400
        return JsonResponse(result, status=status)
    except Exception as e:
        log_exception(error=e, context='Chat - Generate Knowledge Base', additional_info={'conversation_sid': conversation_sid})
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def update_chat_apartment_kb(request, conversation_sid):
    """Backward-compatible alias for update_chat_knowledge_base."""
    return update_chat_knowledge_base(request, conversation_sid)


def _parse_notes_payload(request):
    notes = None
    kind = 'message'
    if request.body:
        try:
            payload = json.loads(request.body)
            if isinstance(payload, dict):
                if 'notes' in payload:
                    notes = payload.get('notes')
                kind = (payload.get('kind') or kind or 'message').strip().lower()
        except json.JSONDecodeError:
            pass
    if notes is None:
        notes = request.POST.get('notes', '')
        kind = (request.POST.get('kind') or kind or 'message').strip().lower()
    if notes is None:
        notes = ''
    notes = str(notes).strip()
    if kind not in ('message', 'ai'):
        kind = 'message'
    return notes or None, kind


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def update_conversation_notes(request, conversation_sid):
    """
    Set or clear notes on a Twilio conversation. Empty string clears.
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        notes, _kind = _parse_notes_payload(request)
        conversation.notes = notes
        conversation.save(update_fields=['notes', 'updated_at'])
        return JsonResponse({
            'success': True,
            'notes': conversation.notes or '',
            'has_notes': bool(conversation.notes),
        })
    except Exception as e:
        log_exception(
            error=e,
            context="Chat - Update Conversation Notes",
            additional_info={'conversation_sid': conversation_sid},
        )
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def update_message_notes(request, conversation_sid, message_id):
    """
    Set or clear notes on a Twilio message. Empty string clears.
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        notes, kind = _parse_notes_payload(request)
        if kind == 'ai':
            message.ai_notes = notes
            message.save(update_fields=['ai_notes', 'updated_at'])
            stored = message.ai_notes or ''
        else:
            message.notes = notes
            message.save(update_fields=['notes', 'updated_at'])
            stored = message.notes or ''
        return JsonResponse({
            'success': True,
            'notes': stored,
            'has_notes': bool(stored),
            'message_id': message.id,
            'kind': kind,
        })
    except Exception as e:
        log_exception(
            error=e,
            context="Chat - Update Message Notes",
            additional_info={'conversation_sid': conversation_sid, 'message_id': message_id},
        )
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def delete_chat_message(request, conversation_sid, message_id):
    """
    Delete a single message from Twilio and DB.
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        message = get_object_or_404(TwilioMessage, id=message_id, conversation=conversation)
        msg_sid = message.message_sid
        try:
            twilio_delete_message(conversation.conversation_sid, msg_sid)
        except Exception as e:
            log_info(f"Twilio delete message failed: {e}", category='sms')
        message.delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return redirect('chat_detail', conversation_sid=conversation_sid)
    except Exception as e:
        log_exception(error=e, context="Chat - Delete Message", additional_info={'message_id': message_id})
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': str(e)}, status=500)
        return redirect('chat_detail', conversation_sid=conversation_sid)


@login_required
def load_more_messages(request, conversation_sid):
    """
    Load more messages for infinite scroll (AJAX endpoint)
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        
        # Get pagination parameters
        page = int(request.GET.get('page', 1))
        
        # Get messages
        chat_messages = conversation.messages.order_by('message_timestamp')
        paginator = Paginator(chat_messages, 50)
        page_messages = paginator.get_page(page)
        
        # Prepare message data for JSON response
        display_info = get_conversation_display_info(conversation)
        author_display_map = _build_author_display_map(display_info.get("participants") or [])
        messages_data = []
        for message in page_messages:
            raw_author = (message.author or "").strip()
            messages_data.append({
                'id': message.id,
                'author': message.author,
                'author_display': author_display_map.get(raw_author, _format_number_name(raw_author, "Unknown")),
                'body': message.body,
                'direction': message.direction,
                'timestamp': message.message_timestamp.isoformat(),
                'formatted_time': message.message_timestamp.strftime('%b %d, %Y at %I:%M %p'),
                'notes': message.notes or '',
                'ai_notes': message.ai_notes or '',
                'ai_response': message.ai_response,
                'ai_response_why': message.ai_response_why,
                'ai_sent_to_chat': message.ai_sent_to_chat,
                'ai_kb_updated': message.ai_kb_updated,
                'ai_kb_changes': message.ai_kb_changes,
                'forwarded_to_group_sid': message.forwarded_to_group_sid,
            })
        
        return JsonResponse({
            'messages': messages_data,
            'has_next': page_messages.has_next(),
            'has_previous': page_messages.has_previous(),
            'current_page': page,
            'total_pages': paginator.num_pages,
        })
        
    except Exception as e:
        log_exception(
            error=e,
            context="Chat - Load More Messages",
            additional_info={'conversation_sid': conversation_sid}
        )
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def chat_template_list(request):
    templates = list(
        ChatMessageTemplate.objects.order_by("name", "-created_at").values("id", "name", "body")
    )
    return JsonResponse({"templates": templates})


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def chat_template_create(request):
    try:
        payload = {}
        if request.body:
            payload = json.loads(request.body)
        else:
            payload = request.POST.dict()

        name = (payload.get("name") or "").strip()
        body = (payload.get("body") or "").strip()

        if not name:
            return JsonResponse({"error": "Template name is required"}, status=400)
        if not body:
            return JsonResponse({"error": "Template body is required"}, status=400)

        tpl = ChatMessageTemplate.objects.create(
            name=name,
            body=body,
            created_by_user=getattr(request, "user", None),
        )

        return JsonResponse(
            {
                "template": {"id": tpl.id, "name": tpl.name, "body": tpl.body},
                "success": True,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON data"}, status=400)
    except Exception as e:
        log_exception(error=e, context="Chat - Create Template")
        return JsonResponse({"error": str(e)}, status=500)


def get_conversation_display_info(conversation):
    """
    Get display information for a conversation sidebar
    Returns dict with name, apartment, booking dates, and phone
    """
    display_info = {
        'name': 'Unknown',
        'apartment': None,
        'booking_dates': None,
        'phone': None,
        'has_booking': False,
        'participants': [],
        'participants_text': '',
    }
    
    try:
        # Try to get info from linked booking first
        if conversation.booking and conversation.booking.tenant:
            tenant = conversation.booking.tenant
            display_info['name'] = tenant.full_name or 'Unknown Tenant'
            display_info['phone'] = tenant.phone
            display_info['has_booking'] = True
            
            if conversation.apartment:
                display_info['apartment'] = conversation.apartment.name
            
            if conversation.booking.start_date and conversation.booking.end_date:
                display_info['booking_dates'] = {
                    'start': conversation.booking.start_date,
                    'end': conversation.booking.end_date,
                }
        
        # If no booking info, try to get tenant phone from messages
        if not display_info['has_booking']:
            # Get customer phone numbers (excluding system phones)
            system_phones = list(SYSTEM_PHONES)
            system_identities = ["ASSISTANT", "Virtual Assistant"]
            
            customer_messages = conversation.messages.exclude(
                author__in=system_phones + system_identities
            ).values_list('author', flat=True).distinct()
            
            if customer_messages:
                # Use first customer phone found
                customer_phone = customer_messages[0]
                display_info['phone'] = customer_phone
                
                # Try to find tenant by phone
                try:
                    tenant = User.objects.filter(phone=customer_phone, role='Tenant').first()
                    if tenant:
                        display_info['name'] = tenant.full_name or customer_phone
                    else:
                        display_info['name'] = customer_phone
                except:
                    display_info['name'] = customer_phone
            else:
                # Fallback to conversation friendly name
                display_info['name'] = conversation.friendly_name or f"Conversation {conversation.conversation_sid[:8]}"
        
        # Always build participants (used in list + header + message author labels)
        participants = _build_conversation_participants(conversation)
        display_info['participants'] = participants
        display_info['participants_text'] = ", ".join([p["formatted"] for p in participants])

        return display_info
        
    except Exception as e:
        display_info['name'] = conversation.friendly_name or f"Conversation {conversation.conversation_sid[:8]}"
        participants = _build_conversation_participants(conversation)
        display_info['participants'] = participants
        display_info['participants_text'] = ", ".join([p["formatted"] for p in participants])
        return display_info



