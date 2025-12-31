from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Max, Q
from django.utils import timezone
from django.core.paginator import Paginator
from mysite.models import TwilioConversation, TwilioMessage, User, ChatMessageTemplate
from mysite.views.messaging import send_messsage_by_sid
from mysite.unified_logger import log_error, log_info, logger
from mysite.error_logger import log_exception
import json


MANAGER_PHONE = "+15612205252"
ASSISTANT_IDENTITY = "ASSISTANT"
# This is the projected address used for the assistant participant in Twilio Conversations.
ASSISTANT_PROJECTED_PHONE = "+13153524379"
# Other system phones that can appear as authors.
SYSTEM_PHONES = {"+13153524379", "+17282001917", MANAGER_PHONE}


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
    raw_candidates.extend([MANAGER_PHONE, ASSISTANT_IDENTITY])

    raw_candidates = _dedupe_preserve_order([str(x).strip() for x in raw_candidates if str(x).strip()])

    # Gather phone numbers we can enrich via User table.
    phones = [x for x in raw_candidates if _is_e164(x)]
    phones = _dedupe_preserve_order(phones)

    users_by_phone = {}
    if phones:
        for u in User.objects.filter(phone__in=phones).only("phone", "full_name", "role"):
            users_by_phone[(u.phone or "").strip()] = u

    participants = []
    for raw in raw_candidates:
        # Assistant participant: show projected phone (Assistant)
        if raw == ASSISTANT_IDENTITY:
            participants.append(
                {
                    "raw_keys": [ASSISTANT_IDENTITY, ASSISTANT_PROJECTED_PHONE],
                    "number": ASSISTANT_PROJECTED_PHONE,
                    "name": "Assistant",
                    "formatted": _format_number_name(ASSISTANT_PROJECTED_PHONE, "Assistant"),
                }
            )
            continue

        # Phone participant
        if _is_e164(raw):
            name = None
            if tenant_phone and raw == tenant_phone and tenant_name:
                name = tenant_name
            elif raw == MANAGER_PHONE:
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
        
        # Get message content from request
        data = json.loads(request.body)
        message_content = data.get('message', '').strip()
        
        if not message_content:
            return JsonResponse({'error': 'Message content is required'}, status=400)
        
        # Send message via Twilio
        # Using manager phone as sender since this is from the interface
        manager_phone = "+15612205252"
        
        # Send message using existing function
        try:
            send_messsage_by_sid(
                conversation_sid=conversation_sid,
                author='ASSISTANT',  # Use ASSISTANT identity for interface messages
                message=message_content,
                sender_phone=manager_phone,
                receiver_phone=None  # Not needed for conversation API
            )
            
            log_info(f"Message sent from chat interface to conversation {conversation_sid}")
            
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
            system_phones = ["+13153524379", "+17282001917"]
            system_identities = ["ASSISTANT"]
            
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



