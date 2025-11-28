#!/bin/bash

# Setup Script for Centralized Error Handling
# This script helps configure and test your error handling setup

echo "========================================="
echo "Centralized Error Handling Setup"
echo "========================================="
echo ""

# Check if TELEGRAM_TOKEN is set
echo "1. Checking TELEGRAM_TOKEN..."
if [ -z "$TELEGRAM_TOKEN" ]; then
    echo "   ❌ TELEGRAM_TOKEN is NOT set"
    echo ""
    echo "   You need to set this environment variable!"
    echo ""
    echo "   Options:"
    echo "   a) Add to systemd service file:"
    echo "      Edit: /etc/systemd/system/site.service"
    echo "      Add: Environment=\"TELEGRAM_TOKEN=your_token_here\""
    echo "      Then: sudo systemctl daemon-reload && sudo systemctl restart site"
    echo ""
    echo "   b) Add to .env file (if using one)"
    echo "   c) Export in shell: export TELEGRAM_TOKEN=your_token_here"
    echo ""
    echo "   To get a token:"
    echo "   - Open Telegram and search for @BotFather"
    echo "   - Send: /newbot"
    echo "   - Follow instructions to get your token"
    echo ""
else
    echo "   ✅ TELEGRAM_TOKEN is set: ${TELEGRAM_TOKEN:0:10}..."
fi

# Check TELEGRAM_ERROR_CHAT_ID
echo ""
echo "2. Checking TELEGRAM_ERROR_CHAT_ID..."
if [ -z "$TELEGRAM_ERROR_CHAT_ID" ]; then
    echo "   ⚠️  TELEGRAM_ERROR_CHAT_ID not set (will use default: 288566859)"
else
    echo "   ✅ TELEGRAM_ERROR_CHAT_ID is set: $TELEGRAM_ERROR_CHAT_ID"
fi

# Check if Django app is accessible
echo ""
echo "3. Checking Django application..."
if [ -f "manage.py" ]; then
    echo "   ✅ manage.py found"
else
    echo "   ❌ manage.py not found - are you in the project root?"
    exit 1
fi

# Test the error handling
echo ""
echo "4. Testing error logging..."
echo ""
read -p "   Do you want to test error logging now? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -z "$TELEGRAM_TOKEN" ]; then
        echo "   ⚠️  Skipping test - TELEGRAM_TOKEN not set"
    else
        echo "   Running test command..."
        python manage.py test_error_logging
        echo ""
        echo "   Check your Telegram for a test error message!"
    fi
else
    echo "   Skipped. Run 'python manage.py test_error_logging' to test later"
fi

echo ""
echo "========================================="
echo "Setup Summary"
echo "========================================="
echo ""
echo "Files created/updated:"
echo "  ✅ mysite/error_logger.py - Universal error logger"
echo "  ✅ CENTRALIZED_ERROR_HANDLING_GUIDE.md - Complete guide"
echo "  ✅ Updated views: utils.py, chat.py, handmade_calendar.py"
echo ""
echo "Next steps:"
if [ -z "$TELEGRAM_TOKEN" ]; then
    echo "  1. SET TELEGRAM_TOKEN environment variable (see above)"
    echo "  2. Restart your application"
    echo "  3. Run: python manage.py test_error_logging"
else
    echo "  1. Review CENTRALIZED_ERROR_HANDLING_GUIDE.md"
    echo "  2. Update remaining 38+ exception handlers"
    echo "  3. Monitor errors in Telegram"
fi
echo ""
echo "========================================="

