# 🚀 Quick Start: Fix Error Handling NOW

## ⚡ The Problem (In 30 Seconds)

You're not getting Telegram notifications about errors because:
1. **TELEGRAM_TOKEN is not set** → Notifications are disabled
2. **42+ exception handlers don't log to Telegram** → They catch errors silently

## ⚡ The Solution (In 2 Minutes)

### Step 1: Set TELEGRAM_TOKEN (1 minute)

```bash
# Find your systemd service file
sudo nano /etc/systemd/system/site.service

# Add this line in the [Service] section:
Environment="TELEGRAM_TOKEN=YOUR_TOKEN_HERE"

# Save and restart
sudo systemctl daemon-reload
sudo systemctl restart site
```

**Don't have a token?** Message `@BotFather` on Telegram → `/newbot` → Get token

### Step 2: Test It Works (30 seconds)

```bash
cd /home/superuser/site
python manage.py test_centralized_errors
```

**Expected:** You should get test error notifications in Telegram!

### Step 3: Use in Your Code (30 seconds)

**Instead of this:**
```python
except Exception as e:
    print(f"Error: {e}")
    return error_response
```

**Do this:**
```python
from mysite.error_logger import log_exception

except Exception as e:
    log_exception(e, "Description of what failed", {'extra': 'context'})
    return error_response
```

## 📊 What Gets Logged?

Every error will include:
- ✅ Error type and message
- ✅ Full stack trace
- ✅ User information (name, role, ID)
- ✅ Request details (path, method, IP)
- ✅ Custom context you provide
- ✅ Timestamp

## 🎯 Files Already Updated

These files now log to Telegram:
- ✅ `mysite/views/utils.py` (used everywhere!)
- ✅ `mysite/views/chat.py`
- ✅ `mysite/views/handmade_calendar.py`

## 📝 Files You Need to Update (38+ left)

High priority:
1. `mysite/views/messaging.py` (20+ exception handlers)
2. `mysite/views/docusign.py`
3. `mysite/views/generate_invoice.py`
4. `mysite/views/payments_report.py`
5. `mysite/views/booking_report.py`
6. `mysite/views/apartments_report.py`
7. `mysite/views/payment_sync.py`
8. `mysite/views/one_link_contract.py`

**Search for:** `except Exception as` in each file and add `log_exception()` call

## 🔍 How to Find Exception Handlers

```bash
# Find all exception handlers in views
cd /home/superuser/site
grep -r "except Exception as" mysite/views/ -n

# Update each one to use log_exception()
```

## 📚 Need More Info?

- **Complete guide:** `CENTRALIZED_ERROR_HANDLING_GUIDE.md`
- **Analysis:** `ERROR_HANDLING_ANALYSIS_SUMMARY.md`
- **Setup script:** `./setup_error_handling.sh`
- **Test command:** `python manage.py test_centralized_errors`

## ✅ Verification Checklist

After setting TELEGRAM_TOKEN:
- [ ] Run test command
- [ ] Receive test notification in Telegram
- [ ] Cause a real error (e.g., invalid form submission)
- [ ] Receive error notification in Telegram with user context

## 🎉 Done!

Once TELEGRAM_TOKEN is set, errors will flow to Telegram automatically.

**Current coverage:** ~10% (3 views updated)
**Goal:** 100% (all 42+ exception handlers)

Start with the high-priority files listed above!

