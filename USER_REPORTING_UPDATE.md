# Enhanced User Reporting in Error Logs

## What's New

The error logging system now captures and reports **comprehensive user information** for every error that occurs in the web application.

## User Information Captured

When an error occurs, the following user details are automatically captured and sent to Telegram:

### For Authenticated Users:
- ✅ **Username** - The user's login username
- ✅ **User ID** - The database ID of the user
- ✅ **Full Name** - The user's full name (if available)
- ✅ **Email** - The user's email address
- ✅ **Role** - User's role (Manager, Tenant, Cleaner, etc.)
- ✅ **Phone** - User's phone number
- ✅ **Authentication Status** - Confirms user is authenticated
- ✅ **Staff Status** - Whether user has staff privileges
- ✅ **Superuser Status** - Whether user has superuser privileges

### For Anonymous Users:
- ❌ **Authenticated: No**
- 🔍 **Username: Anonymous**
- 📍 **IP Address** - Still tracked

## Example Error Message

When an error occurs, you'll receive a Telegram message like this:

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
  • Staff: Yes

❌ Error Type: ValueError
💬 Message: Invalid booking date

📊 Request Details:
  • Method: POST
  • Path: /api/bookings
  • IP: 192.168.1.100
  • GET Params: {'debug': 'true'}
  • POST Data: {'booking_date': '2025-13-45'}

📋 Traceback:
File "/path/to/file.py", line 123, in function_name
  raise ValueError("Invalid booking date")
ValueError: Invalid booking date
```

## Benefits

### 1. **Immediate User Identification**
You can instantly see which user encountered the error without having to check logs.

### 2. **Better Context for Debugging**
Knowing the user's role and permissions helps understand why the error occurred.

### 3. **Quick User Communication**
You have the user's email and phone readily available to follow up if needed.

### 4. **Security Monitoring**
Track if errors are coming from staff/superuser accounts vs regular users.

### 5. **User Behavior Patterns**
Identify if specific users or roles are experiencing recurring issues.

## Privacy & Security

### Sensitive Data Protection
The following fields are **automatically filtered** from error reports:
- `password`
- `token`
- `secret`
- `api_key`
- `csrfmiddlewaretoken`

### What's Safe to Log
- User identification info (username, ID, email, phone) is logged
- This helps with debugging and user support
- All data is sent securely to your private Telegram chat

### Compliance Note
If your application handles sensitive user data subject to regulations (GDPR, HIPAA, etc.), review what user information you're comfortable logging and adjust the `get_user_info()` method in `mysite/error_handling_middleware.py` accordingly.

## How It Works

### 1. Middleware Enhancement
The `GlobalErrorHandlingMiddleware` now has a `get_user_info()` method that extracts all available user details from the request.

### 2. Formatted Display
The `TelegramErrorLogger` formats user information in a dedicated section at the top of the error message for easy visibility.

### 3. Context String
The error context now includes the username and ID: `"Web Request by john_doe (ID: 42): POST /api/bookings"`

## Customization

### Add More User Fields

If your User model has additional fields you want to track, edit `mysite/error_handling_middleware.py`:

```python
def get_user_info(self, request):
    # ... existing code ...
    
    # Add custom fields
    if hasattr(user, 'department') and user.department:
        user_info['Department'] = user.department
    
    if hasattr(user, 'last_login') and user.last_login:
        user_info['Last Login'] = str(user.last_login)
    
    return user_info
```

### Remove Sensitive Fields

If you don't want to log certain fields (e.g., email, phone), simply comment them out:

```python
def get_user_info(self, request):
    # ... existing code ...
    
    # Don't log email
    # if hasattr(user, 'email') and user.email:
    #     user_info['Email'] = user.email
    
    return user_info
```

## Testing

Test the enhanced user reporting:

```bash
# While logged in as a user, run:
python manage.py test_error_logging

# Check your Telegram chat to see the user information displayed
```

Or trigger an intentional error in the web interface while logged in as different users to see how their information is reported.

## Files Modified

1. **`mysite/error_handling_middleware.py`**
   - Added `get_user_info()` method
   - Enhanced `process_exception()` to capture user details
   - Added GET parameters logging

2. **`mysite/telegram_logger.py`**
   - Updated `format_error_message()` to display user info prominently
   - Separated user info from request details

3. **Documentation updated:**
   - `ERROR_LOGGING_SYSTEM.md`
   - `SETUP_ERROR_LOGGING.md`

## Questions?

- **Q: Will this slow down error handling?**
  - A: No, extracting user info is very fast (microseconds)

- **Q: What if user info is not available?**
  - A: It gracefully handles missing fields and shows "Anonymous" for unauthenticated users

- **Q: Can I see historical errors?**
  - A: Errors are logged in `common.log` and sent to Telegram. Consider saving important Telegram messages or implementing a database logger for historical tracking.

---

**The enhanced user reporting is now active!** All errors will include detailed user information automatically.

