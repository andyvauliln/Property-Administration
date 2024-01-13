const { exec } = require('child_process');
const cron = require('node-cron');

// Schedule task to run every day at 08:00
cron.schedule('0 8 * * *', function () {
    console.log('Running Django cron...');
    exec('/usr/bin/python3 /home/superuser/site/manage.py cron', { cwd: '/home/superuser/site/' }, (error, stdout, stderr) => {
        if (error) {
            console.error(`Error executing task: ${error}`);
            return;
        }
        console.log(`stdout: ${stdout}`);
        console.log(`stderr: ${stderr}`);
    });
    console.log(`stderr: finished cron`);
});