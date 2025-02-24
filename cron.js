const { exec } = require('child_process');
const cron = require('node-cron');
const fs = require('fs');

// JSON file path
const logFilePath = 'cron_logs.json';

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
    console.log('Running Django telegram notification cron...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py telegram_notifications_manager', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
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

// Schedule task to run every day at 12:00 (noon)
cron.schedule('0 12 * * *', function () {
    console.log('Running Django SMS cron...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py sms_notifications', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        // Log execution details
        const logEntry = {
            timestamp: new Date().toISOString(),
            command: 'Django SMS cron',
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
    console.log(`stderr: finished SMS cron`);
});
// Schedule task to run every 13h
cron.schedule('0 */13 * * *', function () {
    console.log('Running 12H Contract notification...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py contract_notification', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        // Log execution details
        const logEntry = {
            timestamp: new Date().toISOString(),
            command: '12H Contract notification',
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
    console.log(`stderr: finished SMS cron`);
});

// Schedule task to run every hour
// cron.schedule('*/10 * * * *', function () {
//     console.log('Running Django Contract Checker cron...');
//     exec('/usr/bin/python3 /home/superuser/site/manage.py contract_cheker', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
//         // Log execution details
//         const logEntry = {
//             timestamp: new Date().toISOString(),
//             command: 'Django Contract Checker cron',
//             error: error ? error.toString() : null,
//             stdout: stdout,
//             stderr: stderr
//         };
//         fs.appendFileSync(logFilePath, JSON.stringify(logEntry) + '\n');

//         if (error) {
//             console.error(`Error executing task: ${error}`);
//             return;
//         }
//         console.log(`stdout: ${stdout}`);
//         console.log(`stderr: ${stderr}`);
//     });
//     console.log(`stderr: finished Contract Checker cron`);
// });