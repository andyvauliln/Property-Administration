# Updated Webhook Conversation Logic

## Overview

The webhook conversation saving logic has been updated to handle different scenarios based on the message author and conversation context. This ensures proper database tracking while avoiding unnecessary booking lookups for system messages.

## How It Works

### 1. **Message Processing Flow**

When a webhook receives a message (`onMessageAdded` event):

1. **Save Message to Database**: All messages are saved with proper direction detection
2. **Smart Conversation Linking**: Conversations are created/updated with intelligent booking/apartment linking
3. **Group Chat Logic**: New customer messages trigger group chat creation if needed

### 2. **Smart Conversation Linking Logic**

The system now uses smart linking based on the message author:

#### **For Customer Messages** (New tenant SMS)
- ✅ **Try to link booking/apartment** if available
- ✅ **Create group chat** with manager and assistant
- ✅ **Save conversation** with customer context
- ✅ **Allow future updates** when booking is created later

#### **For Manager/Assistant Messages**
- ❌ **Skip booking lookup** (no need to search)
- ✅ **Save message normally**
- ✅ **Maintain existing conversation links**

### 3. **System Phone Numbers**

These are considered system users (not customers):
- `+13153524379` - Twilio phone
- `+15612205252` - Manager phone  
- `ASSISTANT` - Assistant identity

## Code Flow

### Webhook Message Processing

```python
# 1. Determine message direction
direction = 'inbound' if author not in [twilio_phone, 'ASSISTANT', manager_phone] else 'outbound'

# 2. Ensure conversation exists with smart linking
save_conversation_to_db(
    conversation_sid=conversation_sid,
    friendly_name=f"Conversation {conversation_sid}",
    author=author  # Used for smart linking logic
)

# 3. Save message to database
save_message_to_db(...)
```

### Smart Conversation Linking

```python
def save_conversation_to_db(conversation_sid, friendly_name, author=None):
    # Create or get conversation
    conversation, created = TwilioConversation.objects.get_or_create(...)
    
    if created and author:
        # Only try to link if author is a customer (not system phones)
        if author not in [twilio_phone, 'ASSISTANT', manager_phone]:
            booking = get_booking_from_phone(author)
            if booking:
                conversation.booking = booking
                conversation.apartment = booking.apartment
                conversation.save()
```

## Scenarios

### Scenario 1: New Customer Message
**When**: First-time customer sends SMS to Twilio number

**Process**:
1. ✅ Save message as 'inbound'
2. ✅ Create conversation with customer phone lookup
3. ✅ Link to booking/apartment if found
4. ✅ Create group chat (customer + manager + assistant)
5. ✅ Forward message to group chat

**Result**: Customer conversation properly linked (if booking exists)

### Scenario 2: Manager Reply
**When**: Manager responds in existing conversation

**Process**:
1. ✅ Save message as 'outbound'
2. ✅ Use existing conversation (no new linking attempts)
3. ❌ Skip booking lookup (manager is system user)

**Result**: Message saved, no unnecessary booking searches

### Scenario 3: Assistant Message
**When**: System sends automated message

**Process**:
1. ✅ Save message as 'outbound'
2. ✅ Use existing conversation
3. ❌ Skip booking lookup (assistant is system identity)

**Result**: System message properly tracked

### Scenario 4: Booking Created Later
**When**: Booking is created after conversation started

**Process**:
1. ✅ Booking save triggers `update_conversation_links()`
2. ✅ Find unlinked conversations with tenant's phone
3. ✅ Link conversations to new booking/apartment

**Result**: Historical conversations get linked retroactively

## Future Booking Updates

### Automatic Linking

When a booking is saved, it automatically:

```python
def save(self, *args, **kwargs):
    # ... existing save logic ...
    
    # Update any existing conversation links for this tenant
    if self.tenant and self.tenant.phone:
        self.update_conversation_links()
```

### Manual Linking

You can also manually update conversation links:

```bash
# Update all unlinked conversations
python3 manage.py update_conversation_links

# Update specific phone number
python3 manage.py update_conversation_links --phone +15551234567

# Update specific conversation
python3 manage.py update_conversation_links --conversation-sid CH123...

# Dry run to preview changes
python3 manage.py update_conversation_links --dry-run
```

## Benefits

### 1. **Performance Optimized**
- ❌ No unnecessary API calls for system messages
- ✅ Only customer messages trigger booking lookups
- ✅ Efficient database queries

### 2. **Context Aware**
- ✅ Distinguishes between customer and system messages
- ✅ Proper direction detection
- ✅ Smart linking based on message author

### 3. **Future Proof**
- ✅ Conversations can be linked later when bookings are created
- ✅ Automatic retroactive linking
- ✅ Manual tools for data maintenance

### 4. **Data Integrity**
- ✅ All messages saved regardless of linking status
- ✅ No duplicate conversations
- ✅ Consistent relationship tracking

## Monitoring

### Check Status
```bash
python3 manage.py check_twilio_status --detailed
```

### Example Output
```
=== Linkage Status ===
Conversations linked to bookings: 2/3 (66.7%)
Conversations linked to apartments: 2/3 (66.7%)

=== Message Direction ===
Inbound messages: 4 (44.4%)
Outbound messages: 5 (55.6%)
```

## Troubleshooting

### Common Issues

1. **Conversation not linked to booking**
   - Check if tenant phone number matches exactly
   - Run manual linking command
   - Verify booking exists within 90-day window

2. **Wrong message direction**
   - Verify system phone numbers are correct
   - Check author field in webhook data

3. **Missing conversation in database**
   - Ensure webhook is properly saving conversations
   - Check for any webhook processing errors

### Debug Commands

```bash
# Check specific conversation
python3 manage.py update_conversation_links --conversation-sid CH123... --dry-run

# Check specific phone
python3 manage.py update_conversation_links --phone +15551234567 --dry-run

# View conversation details
python3 manage.py shell -c "from mysite.models import TwilioConversation; conv = TwilioConversation.objects.get(conversation_sid='CH123...'); print(f'Booking: {conv.booking}'); print(f'Messages: {conv.messages.count()}')"
```

This updated logic ensures efficient, context-aware conversation tracking while maintaining the ability to link conversations to bookings both immediately and retroactively.
