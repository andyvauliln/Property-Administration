# Setup Guide: Error Logging System

This guide will help you set up the new error logging system with Telegram notifications.

## Quick Start

### 1. Install Node Dependencies

```bash
npm install
```

This will install the `axios` package needed for Telegram notifications from cron jobs.

### 2. Set Environment Variables

Add these to your `.env` file (or set them in your environment):

```bash
# Get this from @BotFather on Telegram
TELEGRAM_TOKEN=your_telegram_bot_token_here

# This is already set to 288566859 by default, but you can override it
TELEGRAM_ERROR_CHAT_ID=288566859
```

### 3. Get Your Telegram Bot Token

1. Open Telegram and search for `@BotFather`
2. Send the command: `/newbot`
3. Follow the instructions to create a bot
4. Copy the token provided
5. Add it to your `.env` file

### 4. Verify Chat ID

To send messages to chat ID `288566859`:
1. Make sure the user with that chat ID has started a conversation with your bot
2. They need to send at least one message to the bot first

### 5. Test the System

Test that everything works:

```bash
# Test exception error logging
python manage.py test_error_logging --test-type=exception

# Test critical message logging
python manage.py test_error_logging --test-type=critical
```

You should receive Telegram notifications for both tests.

## What's Been Implemented

### ✅ Web Application Error Handling

- All unhandled exceptions in views and middleware are automatically caught
- Error details sent to Telegram including:
  - Request method and path
  - **Comprehensive user information:**
    - Username, User ID, Full name
    - Email, Phone, Role
    - Authentication and permission status
  - Client IP address
  - GET parameters
  - POST data (sensitive fields filtered)
  - Full traceback

### ✅ Cron Job Error Handling

- All cron jobs now have automatic error handling
- Errors are logged to `cron_logs.json`
- Errors are sent to Telegram
- Updated commands:
  - Django telegram notification cron
  - Django telegram notification cron FOR MANAGERS
  - Django telegram notification cron FOR CLEANERS
  - 12H Contract notification

### ✅ Management Command Error Handling

- New base class `BaseCommandWithErrorHandling` for all commands
- Already implemented in:
  - `telegram_notifications`
  - `telegram_notifications_manager`
  - `telegram_notifications_cleaning`

### ✅ Test Command

- `test_error_logging` command to verify the system works

## Files Created/Modified

### New Files:
1. `mysite/telegram_logger.py` - Core error logging utility
2. `mysite/management/commands/base_command.py` - Base class for commands
3. `mysite/management/commands/test_error_logging.py` - Test command
4. `ERROR_LOGGING_SYSTEM.md` - Detailed documentation
5. `SETUP_ERROR_LOGGING.md` - This file

### Modified Files:
1. `mysite/error_handling_middleware.py` - Enhanced with Telegram logging
2. `cron.js` - Added automatic error handling
3. `package.json` - Added axios dependency
4. `env.sample` - Added Telegram configuration
5. `mysite/management/commands/telegram_notifications.py` - Uses new base class
6. `mysite/management/commands/telegram_notifications_manager.py` - Uses new base class
7. `mysite/management/commands/telegram_notifications_cleaning.py` - Uses new base class

## Usage Examples

### Manual Error Logging

```python
from mysite.telegram_logger import log_error, log_critical

# In your code, when you catch an exception:
try:
    risky_operation()
except Exception as e:
    log_error(
        error=e,
        context="Processing payment",
        additional_info={
            'payment_id': payment.id,
            'amount': payment.amount
        }
    )
```

### Creating New Management Commands

```python
from mysite.management.commands.base_command import BaseCommandWithErrorHandling

class Command(BaseCommandWithErrorHandling):
    help = 'Your command description'
    
    def execute_command(self, *args, **options):
        # Your command logic
        # Any unhandled exceptions will be sent to Telegram
        pass
```

### Adding New Cron Jobs

```javascript
// In cron.js
cron.schedule('0 * * * *', function () {
    executeCronCommand(
        'Your Command Name',
        '/usr/bin/python3 /home/superuser/site/manage.py your_command',
        '/home/superuser/site/'
    );
});
```

## Troubleshooting

### Not Receiving Telegram Messages?

1. **Check environment variable:**
   ```bash
   printenv | grep TELEGRAM_TOKEN
   ```

2. **Test bot token:**
   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"
   ```

3. **Make sure chat is initialized:**
   - Open Telegram
   - Search for your bot
   - Click "Start" or send any message

4. **Check logs:**
   ```bash
   tail -f common.log
   ```

### Command Not Found?

Make sure you're in the project directory:
```bash
cd /home/superuser/site
```

### Import Errors?

Make sure you're using the virtual environment:
```bash
source myenv/bin/activate
python manage.py test_error_logging
```

## Production Deployment

When deploying to production:

1. **Set environment variables** on your server
2. **Restart cron service** to pick up new cron.js changes
3. **Restart Django** to pick up middleware changes
4. **Test** with the test command

```bash
# Restart PM2 if using it
pm2 restart all

# Or restart your systemd service
sudo systemctl restart your-django-service
```

## Monitoring

- Check Telegram chat ID `288566859` for error notifications
- Review `cron_logs.json` for historical cron job logs
- Monitor `common.log` for general application logs

## Next Steps

1. Set up your Telegram bot and get the token
2. Add the token to your `.env` file
3. Run the test command to verify everything works
4. Monitor the Telegram chat for error notifications
5. Update any other management commands to use `BaseCommandWithErrorHandling`

## Support

If you encounter any issues:
1. Check the detailed documentation in `ERROR_LOGGING_SYSTEM.md`
2. Review the test command output
3. Check application logs
4. Verify Telegram bot configuration

---

**Important:** Never commit your `.env` file with the actual Telegram token to version control!

