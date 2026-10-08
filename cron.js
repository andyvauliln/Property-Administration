const { exec } = require('child_process');
const cron = require('node-cron');
const fs = require('fs');
const axios = require('axios');

// JSON file path
const logFilePath = 'logs/cron_logs.json';

// Telegram configuration
const TELEGRAM_TOKEN = process.env.TELEGRAM_TOKEN;
const TELEGRAM_ERROR_CHAT_ID = process.env.TELEGRAM_ERROR_CHAT_ID || '288566859';

// Function to send error to Telegram
async function sendTelegramError(commandName, error, stderr) {
    if (!TELEGRAM_TOKEN) {
        console.error('TELEGRAM_TOKEN not set, cannot send error notification');
        return;
    }

    const timestamp = new Date().toISOString();
    let message = `🚨 <b>CRON JOB ERROR</b> 🚨\n\n`;
    message += `⏰ <b>Time:</b> ${timestamp}\n`;
    message += `📍 <b>Command:</b> ${commandName}\n\n`;
    message += `❌ <b>Error:</b>\n<pre>${error.toString().substring(0, 500)}</pre>\n`;
    
    if (stderr) {
        message += `\n📋 <b>Stderr:</b>\n<pre>${stderr.substring(0, 500)}</pre>`;
    }

    try {
        await axios.post(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`, {
            chat_id: TELEGRAM_ERROR_CHAT_ID,
            text: message,
            parse_mode: 'HTML'
        });
        console.log('Error notification sent to Telegram');
    } catch (telegramError) {
        console.error('Failed to send error to Telegram:', telegramError.message);
    }
}

// Generic function to execute cron command with error handling
function executeCronCommand(commandName, command, cwd) {
    console.log(`Running ${commandName}...`);
    exec(command, { cwd: cwd }, async (error, stdout, stderr) => {
        // Log execution details
        const logEntry = {
            timestamp: new Date().toISOString(),
            command: commandName,
            error: error ? error.toString() : null,
            stdout: stdout,
            stderr: stderr
        };
        fs.appendFileSync(logFilePath, JSON.stringify(logEntry) + '\n');

        if (error) {
            console.error(`Error executing ${commandName}: ${error}`);
            // Send error to Telegram
            await sendTelegramError(commandName, error, stderr);
            return;
        }
        
        if (stderr) {
            console.log(`stderr: ${stderr}`);
        }
        
        console.log(`stdout: ${stdout}`);
        console.log(`Finished ${commandName}`);
    });
}

//Schedule task to run every day at 08:00
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Django telegram notification cron',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_notifications',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Django telegram notification cron FOR MANAGERS',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_notifications_manager',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Django telegram notification cron FOR CLEANERS',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_notifications_cleaning',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });

cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Telegram Group: Cleaning',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_group_cleaning',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Telegram Group: Checkout',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_group_checkout',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Telegram Group: Checkin',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_group_checkin',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Telegram Group: Payment',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_group_payment',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Telegram Group: Tenant Reviews',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_group_tenant_reviews',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });

cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'SMS Notifications',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py sms_notifications',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });

// Flushes tenant SMS held by send_tenant_sms_gated for the 08:00-21:00 Florida notification window
// (AI answers, welcome/contract-link messages sent outside that window). No-op outside the window itself.
// Not logged to cron_logs.json on success, same reasoning as the watchdog below.
cron.schedule('*/5 * * * *', function () {
    exec('/home/superuser/site/venv/bin/python /home/superuser/site/manage.py flush_pending_sms',
        { cwd: '/home/superuser/site/' }, async (error, stdout, stderr) => {
            if (error) {
                await sendTelegramError('Flush Pending SMS', error, stderr);
            }
        });
}, { timezone: 'America/New_York' });

// Schedule data integrity check to run daily at 9 PM
cron.schedule('0 21 * * *', function () {
    executeCronCommand(
        'Data Integrity Check',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py check_data_integrity',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });

cron.schedule('0 9 * * *', function () {
    executeCronCommand(
        'Twilio Balance Check',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py check_twilio_balance',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });

// AI agent watchdog: every 5 minutes, alerts the AI Telegram chat when messages are stuck in the queue.
// Not logged to cron_logs.json on success (it would add 288 entries a day).
cron.schedule('*/5 * * * *', function () {
    exec('/home/superuser/site/venv/bin/python /home/superuser/site/manage.py ai_agent_watchdog',
        { cwd: '/home/superuser/site/' }, async (error, stdout, stderr) => {
            if (error) {
                await sendTelegramError('AI Agent Watchdog', error, stderr);
            }
        });
}, { timezone: 'America/New_York' });

// Schedule daily manager activity report at 9 PM
cron.schedule('0 21 * * *', function () {
    executeCronCommand(
        'Daily Manager Activity Report',
        '/home/superuser/site/venv/bin/python /home/superuser/site/manage.py telegram_manager_activity',
        '/home/superuser/site/'
    );
}, { timezone: 'America/New_York' });