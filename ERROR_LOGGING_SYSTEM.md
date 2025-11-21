# Error Logging System with Telegram Notifications

This document describes the error logging system implemented in the property management site.

## Overview

The system provides automatic error logging and Telegram notifications for:
- **Web application errors**: All unhandled exceptions in views, middleware, etc.
- **Cron job errors**: Errors occurring in scheduled tasks
- **Management command errors**: Errors in Django management commands

All errors are automatically sent to Telegram chat ID `288566859` (configurable via `TELEGRAM_ERROR_CHAT_ID`).

## Components

### 1. Centralized Telegram Logger (`mysite/telegram_logger.py`)

The core logging utility that handles formatting and sending error notifications to Telegram.

**Features:**
- Formats error messages with context, timestamp, and traceback
- Handles message truncation (Telegram 4096 character limit)
- Provides both error and critical alert functions
- Gracefully handles missing Telegram credentials

**Usage:**

```python
from mysite.telegram_logger import log_error, log_critical

# Log an exception
try:
    # your code
    pass
except Exception as e:
    log_error(
        error=e,
        context="Where the error occurred",
        additional_info={
            'user_id': user.id,
            'custom_field': 'value'
        }
    )

# Log a critical message (not an exception)
log_critical(
    message="Critical situation detected",
    context="Payment processing",
    additional_info={'payment_id': 123}
)
```

### 2. Global Error Handling Middleware (`mysite/error_handling_middleware.py`)

Catches all unhandled exceptions in web requests and sends them to Telegram.

**Captured Information:**
- Request method and path
- **Detailed user information:**
  - Username and User ID
  - Full name and email
  - Role (Manager, Tenant, Cleaner, etc.)
  - Phone number
  - Authentication status
  - Staff/Superuser status
- Client IP address
- GET parameters
- POST data (sensitive fields filtered: password, token, secret, api_key, csrfmiddlewaretoken)
- Full traceback

**Configuration:**

Already enabled in `settings.py`:

```python
MIDDLEWARE = [
    # ...
    'mysite.error_handling_middleware.GlobalErrorHandlingMiddleware',
]
```

### 3. Base Command with Error Handling (`mysite/management/commands/base_command.py`)

Base class for all management commands with automatic error logging.

**Usage:**

Instead of inheriting from `BaseCommand`, use `BaseCommandWithErrorHandling`:

```python
from mysite.management.commands.base_command import BaseCommandWithErrorHandling

class Command(BaseCommandWithErrorHandling):
    help = 'Your command description'
    
    def execute_command(self, *args, **options):
        # Your command logic here
        # Any exceptions will be automatically caught and sent to Telegram
        pass
```

**Updated Commands:**
- `telegram_notifications`
- `telegram_notifications_manager`
- `telegram_notifications_cleaning`

### 4. Cron Job Error Handling (`cron.js`)

The cron scheduler now automatically sends errors to Telegram when scheduled tasks fail.

**Features:**
- Centralized error handling function
- Automatic Telegram notifications on failure
- Detailed error logging with stderr output
- Simplified cron job definitions

**Example:**

```javascript
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Your Command Name',
        '/usr/bin/python3 /path/to/manage.py your_command',
        '/path/to/project/'
    );
});
```

## Environment Variables

Add these to your `.env` file:

```bash
# Required: Your Telegram bot token (get from @BotFather)
TELEGRAM_TOKEN=your_telegram_bot_token

# Optional: Override default error notification chat ID
TELEGRAM_ERROR_CHAT_ID=288566859
```

## Setup Instructions

### 1. Install Dependencies

```bash
# Install axios for Node.js Telegram integration
npm install
```

### 2. Configure Environment Variables

```bash
# Copy the sample file
cp env.sample .env

# Edit .env and add your Telegram credentials
nano .env
```

### 3. Get Telegram Bot Token

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow instructions
3. Copy the token provided
4. Add it to your `.env` file as `TELEGRAM_TOKEN`

### 4. Find Your Chat ID

1. Start a chat with your bot
2. Send any message
3. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Look for `"chat":{"id":...}` in the response
5. Use this ID for `TELEGRAM_ERROR_CHAT_ID`

## Testing

### Test Web Error Handling

Create a test view that raises an exception and verify you receive a Telegram notification.

### Test Management Command Error Handling

```bash
python manage.py test_error_logging
```

This will intentionally raise an error and send it to Telegram.

### Test Cron Job Error Handling

Modify a cron job to run a command that will fail and verify notification is sent.

## Error Message Format

### Web Errors

```
🚨 ERROR ALERT 🚨

⏰ Time: 2025-11-21 10:30:00
📍 Context: Web Request by john_doe (ID: 42): POST /api/bookings

👤 User Information:
  • Authenticated: Yes
  • Username: john_doe
  • User ID: 42
  • Full Name: John Doe
  • Email: john@example.com
  • Role: Manager
  • Phone: +15614603904

❌ Error Type: ValueError
💬 Message: Invalid booking date

📊 Request Details:
  • Method: POST
  • Path: /api/bookings
  • IP: 192.168.1.100
  • POST Data: {'booking_date': '2025-13-45', 'apartment_id': '5'}

📋 Traceback:
[traceback details...]
```

### Cron Job Errors

```
🚨 CRON JOB ERROR 🚨

⏰ Time: 2025-11-21 08:00:00
📍 Command: Django telegram notification cron

❌ Error:
[error details...]

📋 Stderr:
[stderr output...]
```

### Management Command Errors

```
🚨 ERROR ALERT 🚨

⏰ Time: 2025-11-21 08:00:00
📍 Context: Django Management Command: telegram_notifications

❌ Error Type: DatabaseError
💬 Message: Connection timeout

📊 Additional Info:
  • Command: telegram_notifications
  • Arguments: ()
  • Options: {}

📋 Traceback:
[traceback details...]
```

## Migrating Existing Commands

To add error logging to existing management commands:

1. **Change the import:**
   ```python
   # Old:
   from django.core.management.base import BaseCommand
   
   # New:
   from mysite.management.commands.base_command import BaseCommandWithErrorHandling
   ```

2. **Change the class inheritance:**
   ```python
   # Old:
   class Command(BaseCommand):
   
   # New:
   class Command(BaseCommandWithErrorHandling):
   ```

3. **Rename the handle method:**
   ```python
   # Old:
   def handle(self, *args, **options):
   
   # New:
   def execute_command(self, *args, **options):
   ```

4. **Remove manual success messages:**
   ```python
   # Remove this line (it's now automatic):
   self.stdout.write(self.style.SUCCESS('Successfully ran...'))
   ```

## Troubleshooting

### Telegram Notifications Not Sent

1. **Check environment variables:**
   ```bash
   echo $TELEGRAM_TOKEN
   echo $TELEGRAM_ERROR_CHAT_ID
   ```

2. **Check Django logs:**
   ```bash
   tail -f common.log
   ```

3. **Verify bot token:**
   ```bash
   curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe
   ```

4. **Check chat ID is correct:**
   - Bot must be started by the user first
   - Chat ID should be a number (e.g., 288566859)

### Errors Still Not Caught

1. **Verify middleware is enabled** in `settings.py`
2. **Check command inheritance** - must use `BaseCommandWithErrorHandling`
3. **Check cron.js** - must use `executeCronCommand` function

## Best Practices

1. **Don't log sensitive data** - The middleware filters common sensitive fields, but be cautious with custom logging
2. **Keep context descriptive** - Include relevant context when manually logging errors
3. **Use additional_info** - Add relevant debugging information to help diagnose issues
4. **Test error logging** - Periodically test that notifications are being sent
5. **Monitor chat** - Check the error chat regularly to catch and fix issues

## Maintenance

### Viewing Logs

- **Django logs**: `common.log`, `debug.log`
- **Cron logs**: `cron_logs.json`
- **SMS logs**: `sms_nofitications.log`, `sms_webhooks.log`

### Log Rotation

Consider implementing log rotation for the JSON log files:

```bash
# Add to crontab
0 0 * * 0 cd /home/superuser/site && [ -f cron_logs.json ] && mv cron_logs.json cron_logs.$(date +\%Y\%m\%d).json
```

## Future Enhancements

- [ ] Add log levels (ERROR, WARNING, INFO)
- [ ] Implement log aggregation and analysis
- [ ] Add email notifications as fallback
- [ ] Create admin dashboard for viewing errors
- [ ] Add error rate alerting
- [ ] Implement error grouping/deduplication

