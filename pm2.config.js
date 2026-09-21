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
            restart_delay: 5000,
        },
        {
            name: 'mcp-tunnel',
            script: '/home/superuser/site/cloudflared_start.sh',
            cwd: '/home/superuser/site',
            restart_delay: 5000,
        },
    ],
};