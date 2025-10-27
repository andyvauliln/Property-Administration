# Chat Interface Documentation

## Overview

A comprehensive chat interface for managing Twilio conversations with customers. The interface provides a modern, responsive UI for viewing conversation history and sending messages directly through the Twilio platform.

## Features

### ✅ **Complete Chat Interface**
- **Two-panel layout**: Sidebar with conversation list + main chat area
- **Real-time messaging**: Send messages directly to Twilio conversations
- **Message history**: View all conversation messages with pagination
- **Responsive design**: Works on desktop and mobile devices

### ✅ **Smart Conversation Display**
- **Tenant information**: Shows tenant name, apartment, and booking dates
- **Fallback display**: Shows phone number when booking info unavailable
- **Booking status**: Visual indicators for linked vs unlinked conversations
- **Last activity**: Conversations sorted by most recent message

### ✅ **Advanced Search & Navigation**
- **Real-time search**: Find conversations by name, phone, or apartment
- **Auto-complete**: Search suggestions as you type
- **Quick navigation**: Click any conversation to view details
- **Breadcrumb navigation**: Easy return to conversation list

## URL Structure

```
/chat/                              # Conversation list
/chat/<conversation_sid>/           # Specific conversation view
/chat/<conversation_sid>/send/      # Send message (AJAX endpoint)
/chat/<conversation_sid>/load-more/ # Load more messages (AJAX)
/chat/search/                       # Search conversations
```

## User Interface

### **Conversation List (`/chat/`)**

**Layout:**
- Header with search bar
- Grid of conversation cards
- Each card shows:
  - Avatar with tenant's first initial
  - Tenant name (or phone number)
  - Apartment name (if linked)
  - Booking dates (if available)
  - Latest message preview
  - Message count and timestamp
  - Linking status badge

**Search Functionality:**
- Searches across: tenant names, phone numbers, apartment names, conversation names
- Auto-submit after 3+ characters or when cleared
- Maintains search state in URL

### **Chat Detail View (`/chat/<conversation_sid>/`)**

**Left Sidebar (320px):**
- **Header**: "Conversations" with back link
- **Conversation list**: All conversations with status indicators
- **Current highlight**: Active conversation highlighted in blue
- **Quick switching**: Click any conversation to switch

**Main Chat Area:**
- **Header**: Tenant info, apartment, booking dates, linking status
- **Messages**: Chronological message history with pagination
- **Input area**: Text area + send button
- **Real-time**: Messages appear immediately after sending

## Message Display

### **Message Layout**
- **Outbound messages**: Right-aligned, blue background
- **Inbound messages**: Left-aligned, white/gray background
- **Message bubbles**: Rounded corners, shadow for depth
- **Metadata**: Author name, timestamp below each message

### **Author Display**
- **Outbound**: Shows "Assistant" for system messages
- **Inbound**: Shows actual phone number or tenant name
- **Timestamps**: Formatted as "Jan 15, 2024 14:30"

## Smart Display Logic

### **Tenant Information Priority**

1. **Linked Booking** (Highest Priority)
   ```
   ✅ Tenant Name: John Smith
   📍 Apartment: 780-109
   📅 Dates: Jan 1 - Jan 15, 2024
   🟢 Status: Linked
   ```

2. **Phone Number Only** (Fallback)
   ```
   📞 Phone: +15551234567
   🟡 Status: Unlinked
   ```

3. **Conversation Name** (Last Resort)
   ```
   💬 Name: Conversation CH123...
   🟡 Status: Unlinked
   ```

### **Sorting & Filtering**

**Conversation List Sorting:**
- Primary: Last message timestamp (newest first)
- Secondary: Conversation creation date
- Filters out conversations with no messages

**Search Results:**
- Matches tenant names, phone numbers, apartments
- Maintains same sorting as main list
- Shows match context in results

## Technical Implementation

### **Backend Views**

#### **`chat_list(request)`**
- Fetches all conversations with messages
- Annotates with latest message timestamp
- Prepares display information for each conversation
- Handles search queries

#### **`chat_detail(request, conversation_sid)`**
- Fetches specific conversation and messages
- Paginated message loading (50 messages per page)
- Builds sidebar conversation list
- Generates display context

#### **`send_message(request, conversation_sid)`** 
- AJAX endpoint for sending messages
- Validates message content
- Sends via Twilio API using ASSISTANT identity
- Returns JSON response for UI feedback

#### **`get_conversation_display_info(conversation)`**
- Core logic for determining display information
- Checks booking links first, falls back to phone numbers
- Handles missing data gracefully
- Returns structured display data

### **Frontend JavaScript**

#### **Message Sending**
```javascript
// Form submission handling
messageForm.addEventListener('submit', async function(e) {
    // Prevent default form submission
    // Validate message content
    // Send AJAX request to send_message endpoint
    // Update UI optimistically
    // Handle errors gracefully
});
```

#### **Keyboard Shortcuts**
- **Enter**: Send message
- **Ctrl+Enter**: New line in message
- **Auto-focus**: Message input stays focused

#### **UI Updates**
- **Optimistic updates**: Messages appear immediately
- **Loading states**: Disable form while sending
- **Error handling**: Show alerts for failed sends
- **Auto-scroll**: Scroll to bottom on new messages

## Security & Permissions

### **Authentication Required**
- All views require `@login_required`
- Only authenticated users can access chat interface

### **Role-Based Access**
- Available to: `Admin` and `Manager` roles
- Controlled via navigation sidebar permissions
- Future: Could add role-specific conversation filtering

### **CSRF Protection**
- Send message endpoint uses `@csrf_exempt` for AJAX
- JSON-based request/response for modern API interaction

## Integration with Twilio

### **Message Sending**
- Uses existing `send_messsage_by_sid()` function
- Sends as `ASSISTANT` identity for consistency
- Leverages established error handling and retry logic

### **Conversation Data**
- Reads from `TwilioConversation` and `TwilioMessage` models
- No direct Twilio API calls in chat interface
- Relies on webhook system for real-time message ingestion

## Performance Considerations

### **Database Optimization**
- Efficient queries with annotations and select_related
- Pagination for large conversation histories
- Indexes on timestamp and conversation fields

### **Frontend Performance**
- Minimal JavaScript for fast loading
- Optimistic UI updates for responsiveness
- Auto-scroll throttling for smooth experience

### **Scalability**
- Paginated message loading prevents memory issues
- Efficient conversation filtering and search
- Cached display information calculations

## Mobile Responsiveness

### **Responsive Design**
- **Desktop**: Full two-panel layout
- **Tablet**: Collapsible sidebar
- **Mobile**: Single-panel with navigation drawer

### **Touch-Friendly**
- Large tap targets for conversation selection
- Optimized text input area for mobile keyboards
- Swipe gestures for navigation (future enhancement)

## Future Enhancements

### **Real-Time Features**
- **WebSocket integration**: Live message updates
- **Typing indicators**: Show when tenant is typing
- **Read receipts**: Message delivery status
- **Push notifications**: New message alerts

### **Advanced Features**
- **Message templates**: Quick response buttons
- **File attachments**: Image and document support
- **Conversation notes**: Internal notes for staff
- **Message search**: Full-text search within conversations

### **Reporting & Analytics**
- **Response times**: Average time to respond
- **Conversation metrics**: Message counts, resolution rates
- **Tenant satisfaction**: Built-in feedback system

## Usage Examples

### **Daily Manager Workflow**

1. **Check new messages**: Navigate to `/chat/`
2. **Review conversations**: Scan for unread/urgent messages
3. **Respond to tenants**: Click conversation → type response → send
4. **Search for specific tenant**: Use search bar to find conversation
5. **Switch between conversations**: Use sidebar for quick navigation

### **Customer Support Scenarios**

**Scenario 1: Booking Question**
```
Tenant: "What time is check-in?"
Manager: "Check-in is from 3 PM onwards. I'll send you the access details closer to your arrival date."
```

**Scenario 2: Maintenance Request**
```
Tenant: "The AC isn't working properly"
Manager: "I'll send a technician tomorrow morning. What time works best for you?"
```

## Installation & Setup

### **Prerequisites**
- Django application with Twilio integration
- TwilioConversation and TwilioMessage models
- User authentication system
- Tailwind CSS for styling

### **Configuration**
1. **URLs**: Add chat URLs to `urls.py`
2. **Views**: Import chat views in `views/__init__.py`
3. **Templates**: Ensure chat templates are in `templates/chat/`
4. **Navigation**: Add chat link to sidebar navigation
5. **Permissions**: Configure role-based access in templates

### **Testing**
```bash
# Check Django configuration
python3 manage.py check

# Test with existing conversation data
python3 manage.py check_twilio_status

# Access chat interface
# Navigate to: http://localhost:8000/chat/
```

## Troubleshooting

### **Common Issues**

1. **No conversations showing**
   - Check if TwilioConversation records exist
   - Verify conversations have messages
   - Run sync command: `python3 manage.py sync_twilio_history`

2. **Messages not sending**
   - Verify Twilio credentials are configured
   - Check conversation_sid is valid
   - Review error logs for API issues

3. **Display information missing**
   - Check booking links: `python3 manage.py update_conversation_links`
   - Verify tenant phone number formatting
   - Review get_conversation_display_info logic

4. **Permission denied**
   - Ensure user has Admin or Manager role
   - Check authentication middleware
   - Verify login_required decorators

### **Debug Commands**
```bash
# Check conversation data
python3 manage.py shell -c "
from mysite.models import TwilioConversation;
print(f'Conversations: {TwilioConversation.objects.count()}')
"

# Test display info
python3 manage.py shell -c "
from mysite.views.chat import get_conversation_display_info;
from mysite.models import TwilioConversation;
conv = TwilioConversation.objects.first();
print(get_conversation_display_info(conv))
"
```

The chat interface provides a complete, production-ready solution for managing customer conversations through a modern web interface while leveraging your existing Twilio integration.
