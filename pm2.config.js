module.exports = {
    apps: [
        {
            name: 'telegram-notifications',
            script: 'cron.js',
        },
        {
            name: 'mcp-server',
            script: '/home/superuser/site/venv/bin/python',
            args: '/home/superuser/site/mcp_server.py',
            cwd: '/home/superuser/site',
            restart_delay: 5000,
        },
        {
            // Claude agent worker for tenant group chats (drains the AIEvent queue)
            name: 'ai-agent',
            script: '/home/superuser/site/venv/bin/python',
            args: '/home/superuser/site/manage.py run_ai_agent',
            cwd: '/home/superuser/site',
            env: { PYTHONUNBUFFERED: '1' },  // so `pm2 logs ai-agent` shows lines immediately
            restart_delay: 5000,
        },
    ],
};