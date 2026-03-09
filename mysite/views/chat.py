from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Max, Q
from django.utils import timezone
from django.core.paginator import Paginator
from mysite.models import TwilioConversation, TwilioMessage, User, ChatMessageTemplate
from mysite.views.messaging import (
    send_messsage_by_sid,
    save_message_to_db,
    delete_conversation as twilio_delete_conversation,
    delete_message as twilio_delete_message,
    delete_all_messages as twilio_delete_all_messages,
)
from mysite.unified_logger import log_error, log_info, logger
from mysite.error_logger import log_exception
import json
from uuid import uuid4


MANAGER_PHONE = "+15612205252"
MANAGER_PHONE_2 = "+17282001917"
MANAGER_PHONE_3 = "+15614603904"
ASSISTANT_IDENTITY = "ASSISTANT"
# This is the projected address used for the assistant participant in Twilio Conversations.
ASSISTANT_PROJECTED_PHONE = "+13153524379"
# Other system phones that can appear as authors.
SYSTEM_PHONES = {"+13153524379", "+17282001917", MANAGER_PHONE, MANAGER_PHONE_2, MANAGER_PHONE_3}


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
    raw_candidates.extend([MANAGER_PHONE, MANAGER_PHONE_2, MANAGER_PHONE_3, ASSISTANT_IDENTITY, "Virtual Assistant"])

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
            elif raw in (MANAGER_PHONE, MANAGER_PHONE_2, MANAGER_PHONE_3):
                # Prefer a real user name if present, fallback to "Manager"
                u = users_by_phone.get(raw)
                name = (u.full_name or "").strip() if u and u.full_name else "Manager"
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
    
    # Get all conversations with their latest message timestamp
    base_conversations = TwilioConversation.objects.annotate(
        last_message_time=Max('messages__message_timestamp')
    ).filter(
        last_message_time__isnull=False  # Only conversations with messages
    )

    total_all_conversations = base_conversations.count()

    conversations = base_conversations
    
    # Apply search filter if provided
    if search_query:
        conversations = conversations.filter(
            Q(booking__tenant__full_name__icontains=search_query) |
            Q(booking__tenant__phone__icontains=search_query) |
            Q(apartment__name__icontains=search_query) |
            Q(friendly_name__icontains=search_query) |
            Q(messages__author__icontains=search_query)
        ).distinct()
    
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
def chat_detail(request, conversation_sid):
    """
    Display specific conversation with messages
    """
    conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
    
    # Get messages with pagination
    chat_messages = conversation.messages.order_by('message_timestamp')
    paginator = Paginator(chat_messages, 50)  # Show 50 messages per page

    # Default to the latest page so the UI opens at the bottom (most recent messages).
    page_number = request.GET.get('page')
    if not page_number:
        page_number = paginator.num_pages or 1

    page_messages = paginator.get_page(page_number)
    
    # Get conversation display info
    display_info = get_conversation_display_info(conversation)
    author_display_map = _build_author_display_map(display_info.get("participants") or [])

    # Add display author to messages for the template
    for m in page_messages:
        raw_author = (m.author or "").strip()
        m.author_display = author_display_map.get(raw_author, _format_number_name(raw_author, "Unknown"))
    
    chat_templates = list(
        ChatMessageTemplate.objects.order_by("name", "-created_at").values("id", "name", "body")
    )

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
        'chat_messages': page_messages,
        'display_info': display_info,
        'sidebar_conversations': sidebar_conversations,
        'outbound_author_display': author_display_map.get(ASSISTANT_IDENTITY, _format_number_name(ASSISTANT_PROJECTED_PHONE, "Assistant")),
        'chat_templates': chat_templates,
        'messages_page_number': page_messages.number,
        'messages_has_previous': page_messages.has_previous(),
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
                send_messsage_by_sid(
                    conversation_sid=conversation_sid,
                    author='ASSISTANT',
                    message=message_content,
                    sender_phone=manager_phone,
                    receiver_phone=None
                )
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
            if sender_type == 'client' and conversation.apartment_id and conversation.booking_id:
                try:
                    from mysite.models import Apartment, Booking
                    from mysite.views.messaging import ai_answer_customer
                    from mysite.group_chat_logger import log_ai_customer_start, log_ai_customer_sent
                    apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=conversation.apartment_id)
                    booking = Booking.objects.select_related('tenant').get(id=conversation.booking_id)
                    if len(original_message.strip()) > 3:
                        log_ai_customer_start(conversation_sid, 'ASSISTANT', original_message, conversation.apartment_id, conversation.booking_id)
                        ai_resp = ai_answer_customer(conversation_sid, original_message, apartment, booking)
                        if ai_resp:
                            if send_to_group_chat:
                                send_messsage_by_sid(conversation_sid, 'ASSISTANT', ai_resp, manager_phone, None)
                            elif sent_message:
                                sent_message.ai_response = ai_resp
                                sent_message.ai_sent_to_chat = False
                                sent_message.save(update_fields=['ai_response', 'ai_sent_to_chat', 'updated_at'])
                            log_ai_customer_sent(conversation_sid, ai_resp)
                except Exception as e:
                    log_exception(error=e, context="Chat - AI answer (client)", additional_info={'conversation_sid': conversation_sid})
            elif sender_type == 'manager' and conversation.apartment_id:
                try:
                    from mysite.models import Apartment
                    from mysite.views.messaging import ai_extract_knowledge, KB_SUFFIX, _extract_marked_body, _is_skippable_message
                    from mysite.group_chat_logger import log_ai_manager_start

                    # UI manager messages should behave like manager-originated messages in webhook:
                    # extract from full body, while still accepting explicit (+) marker.
                    body_for_extract = _extract_marked_body(original_message, KB_SUFFIX) or original_message
                    if body_for_extract and not _is_skippable_message(body_for_extract):
                        apartment = Apartment.objects.get(id=conversation.apartment_id)
                        log_ai_manager_start(conversation_sid, 'ASSISTANT', body_for_extract, conversation.apartment_id)
                        ai_extract_knowledge(conversation_sid, body_for_extract, apartment)
                except Exception as e:
                    log_exception(error=e, context="Chat - AI extract (manager)", additional_info={'conversation_sid': conversation_sid})
            
            return JsonResponse({
                'success': True,
                'message': 'Message sent successfully'
            })
            
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
def update_chat_apartment_kb(request, conversation_sid):
    """
    Update apartment knowledge base directly from chat detail modal.
    """
    try:
        conversation = get_object_or_404(TwilioConversation, conversation_sid=conversation_sid)
        if not conversation.apartment_id:
            return redirect('chat_detail', conversation_sid=conversation_sid)

        kb_text = (request.POST.get('knowledge_base') or '').strip()
        conversation.apartment.knowledge_base = kb_text
        conversation.apartment.save()

        log_info(f"Updated apartment KB from chat for conversation {conversation_sid}")
        return redirect('chat_detail', conversation_sid=conversation_sid)
    except Exception as e:
        log_exception(error=e, context="Chat - Update Apartment KB", additional_info={'conversation_sid': conversation_sid})
        return redirect('chat_detail', conversation_sid=conversation_sid)


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
                'ai_response': message.ai_response,
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



