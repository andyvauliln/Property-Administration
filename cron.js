const { exec } = require('child_process');
const cron = require('node-cron');
const fs = require('fs');

// JSON file path
const logFilePath = 'logs/cron_logs.json';

//Schedule task to run every day at 08:00
cron.schedule('0 8 * * *', function () {
    console.log('Running Django telegram notification cron...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py telegram_notifications', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        // Log execution details
        const logEntry = {
            timestamp: new Date().toISOString(),
            command: 'Django telegram notification cron',
            error: error ? error.toString() : null,
            stdout: stdout,
            stderr: stderr
        };
        fs.appendFileSync(logFilePath, JSON.stringify(logEntry) + '\n');

        if (error) {
            console.error(`Error executing task: ${error}`);
            return;
        }
        console.log(`stdout: ${stdout}`);
        console.log(`stderr: ${stderr}`);
    });
    console.log(`stderr: finished cron`);
});
cron.schedule('0 8 * * *', function () {
    console.log('Running Django telegram notification cron FOR MANAGERS...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py telegram_notifications_manager', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        // Log execution details
        const logEntry = {
            timestamp: new Date().toISOString(),
            command: 'Django telegram notification cron FOR MANAGERS',
            error: error ? error.toString() : null,
            stdout: stdout,
            stderr: stderr
        };
        fs.appendFileSync(logFilePath, JSON.stringify(logEntry) + '\n');

        if (error) {
            console.error(`Error executing task: ${error}`);
            return;
        }
        console.log(`stdout: ${stdout}`);
        console.log(`stderr: ${stderr}`);
    });
    console.log(`stderr: finished cron`);
});
cron.schedule('0 8 * * *', function () {
    console.log('Running Django telegram notification cron FOR CLEANERS...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py telegram_notifications_cleaning', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        // Log execution details
        const logEntry = {
            timestamp: new Date().toISOString(),
            command: 'Django telegram notification cron FOR CLEANERS',
            error: error ? error.toString() : null,
            stdout: stdout,
            stderr: stderr
        };
        fs.appendFileSync(logFilePath, JSON.stringify(logEntry) + '\n');

        if (error) {
            console.error(`Error executing task: ${error}`);
            return;
        }
        console.log(`stdout: ${stdout}`);
        console.log(`stderr: ${stderr}`);
    });
    console.log(`stderr: finished cron`);
});
