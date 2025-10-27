# Multiple Bookings Scenarios - How It Works

## Overview

This document explains how the updated conversation logic handles multiple bookings for the same tenant across different apartments.

## Key Principle

**One conversation per apartment per tenant** - Each apartment gets its own dedicated conversation thread, even for the same tenant.

## Scenario Analysis

### Scenario 1: Tenant Books Second Apartment

**Setup:**
- Tenant John (+1555123456) has Booking A for Apartment 101
- Existing group chat: CH_ABC123 (John + Manager + Assistant)
- John books Apartment 201 (Booking B)

**What Happens:**

#### When John Messages About Apartment 201:
1. **Webhook receives message** from +1555123456
2. **System finds current booking** → Booking B (Apartment 201) 
3. **Checks for existing conversation for Apartment 201** → None found
4. **Creates NEW group chat** → CH_XYZ789 for Apartment 201
5. **Links new conversation** to Booking B and Apartment 201

#### Result:
- ✅ **Two separate conversations:**
  - CH_ABC123: Linked to Booking A (Apartment 101)
  - CH_XYZ789: Linked to Booking B (Apartment 201)
- ✅ **Proper context separation**
- ✅ **Manager knows which apartment** conversation refers to

### Scenario 2: Tenant Returns to Same Apartment

**Setup:**
- Tenant John had Booking A for Apartment 101 (ended)
- Existing conversation: CH_ABC123 (linked to old Booking A)
- John books Apartment 101 again (Booking C)

**What Happens:**

#### When John Messages About Apartment 101:
1. **Webhook receives message** from +1555123456
2. **System finds current booking** → Booking C (Apartment 101)
3. **Checks for existing conversation for Apartment 101** → Found CH_ABC123
4. **Reuses existing conversation** CH_ABC123
5. **Updates conversation link** to new Booking C

#### Result:
- ✅ **Same conversation reused** for same apartment
- ✅ **Updated booking context** to most recent booking
- ✅ **Conversation history preserved**

### Scenario 3: Overlapping Bookings

**Setup:**
- Tenant John has Booking A for Apartment 101 (current)
- Tenant John books Apartment 201 for next month (Booking B)
- Both bookings are active/future

**What Happens:**

#### When John Messages:
1. **System finds most recent/current booking** (based on timing logic)
2. **Creates/uses conversation** for that specific apartment
3. **Each apartment maintains separate conversation**

#### Timing Logic:
- **Current date within Booking A period** → Use Apartment 101 conversation
- **Current date within Booking B period** → Use Apartment 201 conversation
- **Ambiguous timing** → Use most recent booking's apartment

## Technical Implementation

### Smart Apartment-Specific Checking

```python
def check_author_in_group_conversations_for_apartment(author_phone, apartment_id=None):
    # If apartment_id provided, check database first
    if apartment_id:
        existing_conversation = TwilioConversation.objects.filter(
            apartment_id=apartment_id,
            messages__author=validated_phone
        ).first()
        
        if existing_conversation:
            return True  # Found conversation for this apartment
    
    # Fallback to general group check
    return check_author_in_group_conversations(author_phone)
```

### Context-Aware Booking Linking

```python
def _should_link_conversation_to_booking(self, conversation):
    # Check timing context
    booking_start_buffer = self.start_date - timedelta(days=30)
    booking_end_buffer = self.end_date + timedelta(days=7)
    
    conversation_start = earliest_message.message_timestamp.date()
    
    # Link if conversation started in booking timeframe
    if booking_start_buffer <= conversation_start <= booking_end_buffer:
        return True
    
    # Link if conversation is very recent (last 3 days)
    if (date.today() - conversation_start).days <= 3:
        return True
    
    return False
```

## Benefits

### 1. **Clear Context Separation**
- Each apartment gets its own conversation thread
- Manager always knows which property is being discussed
- No confusion between different bookings

### 2. **Efficient Resource Usage**
- Reuses conversations for same apartment
- Creates new conversations only when needed
- Maintains conversation history across bookings for same apartment

### 3. **Proper Data Linking**
- Conversations linked to contextually relevant bookings
- Automatic updates when new bookings are created
- Historical conversations remain properly linked

### 4. **Scalable for Complex Scenarios**
- Handles any number of apartments per tenant
- Works with overlapping bookings
- Manages sequential bookings correctly

## Database Structure

### Example Data After Multiple Bookings:

```
TwilioConversation Table:
┌─────────────────────┬─────────────┬─────────────┬────────────────┐
│ conversation_sid    │ booking_id  │ apartment_id│ friendly_name  │
├─────────────────────┼─────────────┼─────────────┼────────────────┤
│ CH_ABC123          │ Booking A   │ Apt 101     │ John Chat 101  │
│ CH_XYZ789          │ Booking B   │ Apt 201     │ John Chat 201  │
│ CH_DEF456          │ Booking C   │ Apt 101     │ John Chat 101  │ ← Updated link
└─────────────────────┴─────────────┴─────────────┴────────────────┘

Note: CH_ABC123 and CH_DEF456 are the same conversation (CH_ABC123), 
just with updated booking link when Booking C was created.
```

## Edge Cases Handled

### 1. **Booking Created After Conversation**
- New booking triggers `update_conversation_links()`
- System finds unlinked conversations with tenant's phone
- Links based on timing and context analysis

### 2. **No Current Booking**
- Customer messages without active booking
- System creates conversation without apartment link
- Future booking creation will retroactively link

### 3. **Multiple Active Bookings**
- System uses "most recent" or "most contextually relevant" booking
- Timing analysis determines best apartment match
- Each apartment still gets separate conversations

### 4. **Conversation History Preservation**
- Old messages remain in original conversations
- New messages go to appropriate apartment conversation
- No data loss or context mixing

## Monitoring Multiple Bookings

### Check Status by Tenant
```bash
# View all conversations for a specific phone number
python3 manage.py shell -c "
from mysite.models import TwilioConversation, TwilioMessage;
phone = '+1555123456';
conversations = TwilioConversation.objects.filter(messages__author=phone).distinct();
for conv in conversations:
    print(f'{conv.conversation_sid}: {conv.apartment} (Booking: {conv.booking})')
"
```

### Update Links for Specific Tenant
```bash
python3 manage.py update_conversation_links --phone +1555123456 --dry-run
```

## Summary

The updated logic ensures that:

1. **Each apartment gets its own conversation** - No mixing of contexts
2. **Conversations are reused intelligently** - Same apartment reuses conversation
3. **Timing context matters** - Recent conversations link to current bookings
4. **Data integrity is maintained** - No orphaned or incorrectly linked conversations
5. **Manager experience is optimal** - Clear context for each conversation

This approach handles the complex scenario of multiple bookings while maintaining conversation clarity and data consistency.
