#!/bin/bash
# Starts cloudflared quick tunnel and writes the public URL to /home/superuser/site/tunnel_url.txt
LOGFILE=/home/superuser/site/tunnel.log
URLFILE=/home/superuser/site/tunnel_url.txt

/home/superuser/bin/cloudflared tunnel --url http://localhost:8001 --no-autoupdate 2>&1 | tee "$LOGFILE" | while IFS= read -r line; do
    echo "$line"
    if echo "$line" | grep -q 'trycloudflare.com'; then
        url=$(echo "$line" | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com')
        if [ -n "$url" ]; then
            echo "$url/mcp" > "$URLFILE"
            echo "==> MCP URL: $url/mcp" >> "$LOGFILE"
        fi
    fi
done
