# Twilio Credentials Setup Guide

## Option 1: Use Auth Token (Recommended)

### Step 1: Get Your Auth Token
1. Go to: https://console.twilio.com/
2. Navigate to: **Account** → **API keys & tokens**
3. Find: **"Auth token (primary)"**
4. Click: **"Show"** to reveal the token
5. Copy the entire 32-character token

### Step 2: Update .env File
```bash
nano /home/superuser/site/.env
```

Update these lines:
```env
TWILIO_ACCOUNT_SID=your_account_sid_here
TWILIO_AUTH_TOKEN=your_32_character_token_here
```

**Important:**
- No spaces around the `=` sign
- No quotes around the values
- Token must be exactly 32 characters

### Step 3: Test
```bash
cd /home/superuser/site
python3 diagnose_twilio.py
```

### Step 4: Restart Service
```bash
./start.sh
```

---

## Option 2: Use API Key (If Auth Token Doesn't Work)

If your Auth Token keeps failing, you can create a new API Key:

### Step 1: Create API Key
1. Go to: https://console.twilio.com/
2. Navigate to: **Account** → **API keys & tokens**
3. Scroll to: **"API keys"** section
4. Click: **"Create API key"**
5. Give it a name (e.g., "Production Server")
6. Click: **"Create API key"**
7. **IMPORTANT:** Copy both:
   - **SID** (starts with SK...)
   - **Secret** (long string, shown only once!)

### Step 2: Update .env File
```bash
nano /home/superuser/site/.env
```

Update to use API Key instead:
```env
# Keep your Account SID the same
TWILIO_ACCOUNT_SID=your_account_sid_here

# Replace Auth Token with API Key SID
TWILIO_AUTH_TOKEN=SK...your_api_key_secret_here
```

**Note:** Yes, you put the API Key Secret in the `TWILIO_AUTH_TOKEN` field. Twilio accepts both.

### Step 3: Test & Restart
```bash
cd /home/superuser/site
python3 diagnose_twilio.py
./start.sh
```

---

## Troubleshooting

### Still Getting 401 Error?

1. **Check Account Status**
   - Go to your Twilio console
   - Look for any warnings about account suspension
   - Check if billing is current

2. **Check Account Type**
   - Trial accounts have limitations
   - Upgrade to paid if needed

3. **Check Token Carefully**
   - No leading/trailing spaces
   - Copy-paste directly (don't retype)
   - Token length is correct (32 chars for Auth Token)

4. **Try Browser Incognito Mode**
   - Sometimes browser caching shows old tokens
   - Open Twilio console in incognito
   - Get fresh credentials

### Need Help?

Run the diagnostic tool:
```bash
cd /home/superuser/site
python3 diagnose_twilio.py
```

This will give you specific error details.


