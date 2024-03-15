const { exec } = require('child_process');
const cron = require('node-cron');

// Schedule task to run every day at 08:00
cron.schedule('0 8 * * *', function () {
    console.log('Running Django telegram notification cron...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py telegram_notifications', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        if (error) {
            console.error(`Error executing task: ${error}`);
            return;
        }
        console.log(`stdout: ${stdout}`);
        console.log(`stderr: ${stderr}`);
    });
    console.log(`stderr: finished cron`);
});

// Schedule task to run every day at 08:00
cron.schedule('0 12 * * *', function () {
    console.log('Running Django sms cron...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py sms_notifications', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        if (error) {
            console.error(`Error executing task: ${error}`);
            return;
        }
        console.log(`stdout: ${stdout}`);
        console.log(`stderr: ${stderr}`);
    });
    console.log(`stderr: finished sms cron`);
});

// Schedule task to run every day at 08:00
cron.schedule('0 * * * *', function () {
    console.log('Running Django Contract Checker cron...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py contract_cheker', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        if (error) {
            console.error(`Error executing task: ${error}`);
            return;
        }
        console.log(`stdout: ${stdout}`);
        console.log(`stderr: ${stderr}`);
    });
    console.log(`stderr: finished sms cron`);
});