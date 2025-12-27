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
        '/usr/bin/python3 /home/superuser/site/manage.py telegram_notifications',
        '/home/superuser/site/'
    );
});
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Django telegram notification cron FOR MANAGERS',
        '/usr/bin/python3 /home/superuser/site/manage.py telegram_notifications_manager',
        '/home/superuser/site/'
    );
});
cron.schedule('0 8 * * *', function () {
    executeCronCommand(
        'Django telegram notification cron FOR CLEANERS',
        '/usr/bin/python3 /home/superuser/site/manage.py telegram_notifications_cleaning',
        '/home/superuser/site/'
    );
});

// Schedule data integrity check to run daily at 9 PM
cron.schedule('0 21 * * *', function () {
    executeCronCommand(
        'Data Integrity Check',
        '/usr/bin/python3 /home/superuser/site/manage.py check_data_integrity',
        '/home/superuser/site/'
    );
});

cron.schedule('0 9 * * *', function () {
    executeCronCommand(
        'Twilio Balance Check',
        '/usr/bin/python3 /home/superuser/site/manage.py check_twilio_balance',
        '/home/superuser/site/'
    );
});

// Schedule daily manager activity report at 9 PM
cron.schedule('0 21 * * *', function () {
    executeCronCommand(
        'Daily Manager Activity Report',
        '/usr/bin/python3 /home/superuser/site/manage.py telegram_manager_activity',
        '/home/superuser/site/'
    );
});