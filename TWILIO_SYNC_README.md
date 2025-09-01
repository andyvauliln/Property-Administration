# Twilio History Sync Command

This Django management command synchronizes all existing Twilio conversations and messages with your database. It's useful for importing historical data and ensuring your database is up-to-date with Twilio.

## Usage

The command is located at `mysite/management/commands/sync_twilio_history.py` and can be run using Django's management interface.

### Basic Usage

```bash
# Sync all conversations and messages from the last 90 days
python3 manage.py sync_twilio_history

# Sync with custom options
python3 manage.py sync_twilio_history --days 30 --limit 50
```

### Command Options

| Option | Description | Default |
|--------|-------------|---------|
| `--days` | Number of days back to sync | 90 |
| `--limit` | Limit number of conversations to process | None (all) |
| `--dry-run` | Show what would be synced without saving | False |
| `--conversation-sid` | Sync specific conversation by SID | None |

### Examples

#### 1. Dry Run (Preview Only)
```bash
# See what would be synced without actually importing
python3 manage.py sync_twilio_history --dry-run --days 7 --limit 5
```

#### 2. Sync Recent Conversations
```bash
# Sync conversations from the last 30 days, maximum 20 conversations
python3 manage.py sync_twilio_history --days 30 --limit 20
```

#### 3. Sync Specific Conversation
```bash
# Sync a specific conversation by its SID
python3 manage.py sync_twilio_history --conversation-sid CH40fe2b6f63c149128dca9ba0bb7c1d36
```

#### 4. Full Historical Sync
```bash
# Sync all conversations from the last year
python3 manage.py sync_twilio_history --days 365
```

## What Gets Synced

### Conversations
- **conversation_sid**: Unique Twilio identifier
- **friendly_name**: Human-readable name
- **booking**: Linked booking if found
- **apartment**: Linked apartment if found
- **created_at/updated_at**: Timestamps

### Messages
- **message_sid**: Unique Twilio identifier
- **conversation**: Link to conversation
- **author**: Phone number or identity (e.g., "ASSISTANT")
- **body**: Message content
- **direction**: 'inbound' or 'outbound'
- **message_timestamp**: When message was sent
- **webhook_sid**: Webhook identifier (if available)
- **messaging_binding_address**: Sender phone
- **messaging_binding_proxy_address**: Proxy phone

## Smart Linking

The command automatically tries to link conversations to existing bookings and apartments by:

1. **Analyzing conversation participants** to find customer phone numbers
2. **Looking up tenants** with matching phone numbers
3. **Finding recent bookings** for those tenants (within 90 days)
4. **Linking conversations** to the most recent booking and its apartment

## Output Examples

### Dry Run Output
```
Starting Twilio sync...
DRY RUN MODE - No data will be saved
Connected to Twilio account: ACbae1a1...
Fetching conversations...
Found 2 conversations to process
Processing conversation 1/2: CH40fe2b6f63c149128dca9ba0bb7c1d36
  Would create conversation: badreya alsawwafi Chat Apt: 780-109
    Would link to booking: 780-109
    Would link to apartment: 780-109
    Would sync 1 messages
Conversations processed: 2 synced, 0 skipped
Sync completed successfully!
```

### Actual Sync Output
```
Starting Twilio sync...
Connected to Twilio account: ACbae1a1...
Fetching conversations...
Found 3 conversations to process
Processing conversation 1/3: CH40fe2b6f63c149128dca9ba0bb7c1d36
  Created conversation: badreya alsawwafi Chat Apt: 780-109
    Messages: 1 synced, 0 skipped
Processing conversation 2/3: CHd10b8dcdd63f4cc7990689169a660d0d
  Created conversation: William Boyle Chat Apt: 815 Flamingo
    Messages: 3 synced, 0 skipped
Conversations processed: 3 synced, 0 skipped
Sync completed successfully!
```

## Error Handling

The command includes comprehensive error handling:

- **Connection errors**: Reports if it can't connect to Twilio
- **API errors**: Continues processing other conversations if one fails
- **Duplicate prevention**: Skips conversations/messages that already exist
- **Rate limiting**: Includes small delays to avoid API limits

## Performance Considerations

- **Large datasets**: The command processes conversations one by one to avoid memory issues
- **Rate limiting**: Includes 0.1-second delays between API calls
- **Pagination**: Handles Twilio's API pagination automatically
- **Memory efficient**: Doesn't load all data into memory at once

## Scheduling

To keep your database synchronized automatically, you can schedule this command using:

### Cron Job (Linux/Mac)
```bash
# Run daily at 2 AM to sync yesterday's conversations
0 2 * * * cd /home/superuser/site && python3 manage.py sync_twilio_history --days 2
```

### Django-Crontab
```python
CRONJOBS = [
    ('0 2 * * *', 'mysite.management.commands.sync_twilio_history', '--days 2'),
]
```

## Verification

After running the sync, you can verify the imported data:

```bash
# Check counts
python3 manage.py shell -c "from mysite.models import TwilioConversation, TwilioMessage; print(f'Conversations: {TwilioConversation.objects.count()}'); print(f'Messages: {TwilioMessage.objects.count()}')"

# View recent conversations
python3 manage.py shell -c "from mysite.models import TwilioConversation; [print(conv) for conv in TwilioConversation.objects.all()[:5]]"

# View recent messages
python3 manage.py shell -c "from mysite.models import TwilioMessage; [print(f'{msg.direction} | {msg.author} | {msg.body[:50]}...') for msg in TwilioMessage.objects.all()[:5]]"
```

## Troubleshooting

### Common Issues

1. **Missing environment variables**: Ensure `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` are set
2. **Permission errors**: Make sure the Twilio account has proper permissions
3. **Database errors**: Ensure migrations are applied: `python3 manage.py migrate`
4. **Memory issues**: Use `--limit` to process fewer conversations at once

### Debug Mode

Add more verbosity for debugging:
```bash
python3 manage.py sync_twilio_history --verbosity 2 --days 7 --limit 5
```
