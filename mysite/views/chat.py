from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Max, Q
from django.utils import timezone
from django.core.paginator import Paginator
from mysite.models import TwilioConversation, TwilioMessage, User
from mysite.unified_logger import logger
from mysite.error_logger import log_exception
import json


@login_required
def chat_list(request):
    """
    Display list of conversations sorted by last activity
    """
    # Get search query
    search_query = request.GET.get('q', '').strip()
    
    # Get all conversations with their latest message timestamp
    conversations = TwilioConversation.objects.annotate(
        last_message_time=Max('messages__message_timestamp')
    ).filter(
        last_message_time__isnull=False  # Only conversations with messages
    )
    
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
    page_number = request.GET.get('page', 1)
    page_messages = paginator.get_page(page_number)
    
    # Get conversation display info
    display_info = get_conversation_display_info(conversation)
    
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
            
            print_info(f"Message sent from chat interface to conversation {conversation_sid}")
            
            return JsonResponse({
                'success': True,
                'message': 'Message sent successfully'
            })
            
        except Exception as e:
            print_info(f"Error sending message from chat interface: {e}")
            log_exception(
                error=e,
                context="Chat - Send Message to Twilio",
                additional_info={
                    'conversation_sid': conversation_sid,
                    'message_length': len(message_body) if message_body else 0
                }
            )
            return JsonResponse({
                'error': f'Failed to send message: {str(e)}'
            }, status=500)
            
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        print_info(f"Error in send_message view: {e}")
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
        messages_data = []
        for message in page_messages:
            messages_data.append({
                'id': message.id,
                'author': message.author,
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
        
        return display_info
        
    except Exception as e:
        print_info(f"Error getting conversation display info: {e}")
        display_info['name'] = conversation.friendly_name or f"Conversation {conversation.conversation_sid[:8]}"
        return display_info



