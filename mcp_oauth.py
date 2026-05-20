"""
Built-in OAuth for the Property Management MCP server (Claude Desktop connector compatible).

Uses username/password login + Dynamic Client Registration (DCR). Set credentials in .env:
  MCP_OAUTH_USERNAME, MCP_OAUTH_PASSWORD, MCP_PUBLIC_URL (HTTPS tunnel base, no /mcp suffix)
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from pydantic import AnyHttpUrl, AnyUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger(__name__)

MCP_SCOPE = "user"
TOKEN_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 30 * 24 * 3600
CLAUDE_AUTH_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
# Claude Desktop custom connectors often use a fixed client_id without calling /register first.
DEFAULT_CLAUDE_CLIENT_ID = "mcp"


class MCPOAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCP_OAUTH_")

    username: str = ""
    password: str = ""
    enabled: bool = True


def _auth_disabled() -> bool:
    flag = os.environ.get("MCP_AUTH_ENABLED", "true").lower()
    if flag in ("0", "false", "no"):
        return True
    settings = MCPOAuthSettings()
    if not settings.enabled:
        return True
    if not settings.username or not settings.password:
        logger.warning("MCP OAuth disabled: set MCP_OAUTH_USERNAME and MCP_OAUTH_PASSWORD in .env")
        return True
    return False


def get_public_base_url() -> str:
    explicit = os.environ.get("MCP_PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    tunnel_file = Path(__file__).resolve().parent / "tunnel_url.txt"
    if tunnel_file.exists():
        mcp_url = tunnel_file.read_text().strip().rstrip("/")
        if mcp_url.endswith("/mcp"):
            return mcp_url[:-4]
        return mcp_url
    return "http://localhost:8001"


def build_auth_settings(public_base: str) -> AuthSettings:
    base = public_base.rstrip("/")
    return AuthSettings(
        issuer_url=AnyHttpUrl(base),
        resource_server_url=AnyHttpUrl(f"{base}/mcp"),
        required_scopes=[MCP_SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[MCP_SCOPE, "offline_access"],
            default_scopes=[MCP_SCOPE],
        ),
    )


class PropertyOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, username: str, password: str, public_base: str):
        self._username = username
        self._password = password
        self.public_base = public_base.rstrip("/")
        self.login_url = f"{self.public_base}/login"
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}
        self._pending_state: dict[str, dict[str, Any]] = {}
        self._register_builtin_clients()

    def _register_builtin_clients(self) -> None:
        """Pre-register Claude Desktop's fixed OAuth client (client_id=mcp)."""
        client_id = os.environ.get("MCP_OAUTH_CLIENT_ID", DEFAULT_CLAUDE_CLIENT_ID)
        self.clients[client_id] = OAuthClientInformationFull(
            client_id=client_id,
            client_secret=None,
            redirect_uris=[AnyUrl(CLAUDE_AUTH_CALLBACK)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=f"{MCP_SCOPE} offline_access",
            client_name="Claude Desktop",
        )
        logger.info("Registered OAuth client %r for Claude Desktop", client_id)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("No client_id provided")
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        state = params.state or secrets.token_hex(16)
        self._pending_state[state] = {
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "client_id": client.client_id,
            "resource": params.resource,
        }
        return f"{self.login_url}?state={state}"

    async def get_login_page(self, state: str) -> HTMLResponse:
        if not state:
            raise HTTPException(400, "Missing state parameter")
        if state not in self._pending_state:
            html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Property MCP Sign In</title>
<style>
body { font-family: system-ui, sans-serif; max-width: 480px; margin: 2rem auto; padding: 0 1rem; }
</style></head><body>
<h1>Property Management MCP</h1>
<p>This sign-in link is only valid when opened from <strong>Claude Desktop</strong> during connector setup.</p>
<p>Add or reconnect your connector in Claude (<strong>Settings → Connectors</strong>), then complete login when Claude opens the browser.</p>
<p>Do not bookmark <code>/login</code> — the <code>state</code> parameter expires in a few minutes and is created by OAuth.</p>
</body></html>"""
            return HTMLResponse(content=html, status_code=400)
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Property MCP Sign In</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 2rem auto; padding: 0 1rem; }}
input {{ width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box; }}
button {{ margin-top: 1rem; padding: 10px 16px; background: #2563eb; color: #fff; border: none; cursor: pointer; }}
</style></head><body>
<h1>Property Management MCP</h1>
<p>Sign in to connect Claude to your property data.</p>
<form method="post" action="/login/callback">
<input type="hidden" name="state" value="{state}">
<label>Username<br><input name="username" autocomplete="username" required></label><br><br>
<label>Password<br><input name="password" type="password" autocomplete="current-password" required></label><br>
<button type="submit">Sign in</button>
</form>
</body></html>"""
        return HTMLResponse(content=html)

    async def handle_login_callback(self, request: Request) -> Response:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        state = form.get("state")
        if not isinstance(username, str) or not isinstance(password, str) or not isinstance(state, str):
            raise HTTPException(400, "Invalid form data")
        redirect_uri = await self._complete_login(username, password, state)
        return RedirectResponse(url=redirect_uri, status_code=302)

    async def _complete_login(self, username: str, password: str, state: str) -> str:
        pending = self._pending_state.get(state)
        if not pending:
            raise HTTPException(400, "Invalid or expired state")

        if username != self._username or password != self._password:
            raise HTTPException(
                401,
                "Invalid credentials. Use MCP_OAUTH_USERNAME and MCP_OAUTH_PASSWORD from your server .env file.",
            )

        redirect_uri = pending["redirect_uri"]
        code_challenge = pending["code_challenge"]
        client_id = pending["client_id"]
        resource = pending.get("resource")

        new_code = secrets.token_urlsafe(32)
        self.auth_codes[new_code] = AuthorizationCode(
            code=new_code,
            client_id=client_id,
            redirect_uri=AnyHttpUrl(redirect_uri),
            redirect_uri_provided_explicitly=pending["redirect_uri_provided_explicitly"],
            expires_at=time.time() + 300,
            scopes=[MCP_SCOPE],
            code_challenge=code_challenge,
            resource=resource,
        )
        del self._pending_state[state]
        return construct_redirect_uri(redirect_uri, code=new_code, state=state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if authorization_code.code not in self.auth_codes:
            raise TokenError("invalid_grant", "Invalid authorization code")
        if not client.client_id:
            raise TokenError("invalid_client", "No client_id")

        access = self._issue_access_token(client.client_id, authorization_code.scopes, authorization_code.resource)
        refresh_value = secrets.token_urlsafe(32)
        self.refresh_tokens[refresh_value] = RefreshToken(
            token=refresh_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + REFRESH_TTL_SECONDS,
        )
        del self.auth_codes[authorization_code.code]
        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=TOKEN_TTL_SECONDS,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_value,
        )

    def _issue_access_token(self, client_id: str, scopes: list[str], resource: str | None) -> AccessToken:
        token_value = secrets.token_urlsafe(32)
        access = AccessToken(
            token=token_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + TOKEN_TTL_SECONDS,
            resource=resource,
        )
        self.access_tokens[token_value] = access
        return access

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        stored = self.refresh_tokens.get(refresh_token)
        if not stored or stored.client_id != client.client_id:
            return None
        if stored.expires_at and stored.expires_at < time.time():
            del self.refresh_tokens[refresh_token]
            return None
        return stored

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if refresh_token.token not in self.refresh_tokens:
            raise TokenError("invalid_grant", "Refresh token does not exist")
        if not client.client_id:
            raise TokenError("invalid_client", "No client_id")

        old_access = [t for t, a in self.access_tokens.items() if a.client_id == client.client_id]
        for t in old_access:
            del self.access_tokens[t]
        del self.refresh_tokens[refresh_token.token]

        access = self._issue_access_token(client.client_id, scopes, None)
        new_refresh_value = secrets.token_urlsafe(32)
        self.refresh_tokens[new_refresh_value] = RefreshToken(
            token=new_refresh_value,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(time.time()) + REFRESH_TTL_SECONDS,
        )
        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=TOKEN_TTL_SECONDS,
            scope=" ".join(scopes),
            refresh_token=new_refresh_value,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = self.access_tokens.get(token)
        if not access:
            return None
        if access.expires_at and access.expires_at < time.time():
            del self.access_tokens[token]
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self.access_tokens.pop(token.token, None)
        else:
            self.refresh_tokens.pop(token.token, None)


def create_authenticated_mcp(name: str, host: str, port: int) -> tuple[FastMCP, PropertyOAuthProvider]:
    oauth_settings = MCPOAuthSettings()
    public_base = get_public_base_url()
    provider = PropertyOAuthProvider(oauth_settings.username, oauth_settings.password, public_base)
    mcp = FastMCP(
        name,
        host=host,
        port=port,
        auth_server_provider=provider,
        auth=build_auth_settings(public_base),
    )
    register_login_routes(mcp, provider)
    return mcp, provider


def register_login_routes(mcp: FastMCP, provider: PropertyOAuthProvider) -> None:
    @mcp.custom_route("/login", methods=["GET"])
    async def login_page(request: Request) -> Response:
        state = request.query_params.get("state")
        if not state:
            raise HTTPException(400, "Missing state parameter")
        return await provider.get_login_page(state)

    @mcp.custom_route("/login/callback", methods=["POST"])
    async def login_callback(request: Request) -> Response:
        return await provider.handle_login_callback(request)


def create_mcp_server(name: str, host: str, port: int) -> FastMCP:
    if _auth_disabled():
        return FastMCP(name, host=host, port=port)
    mcp, _ = create_authenticated_mcp(name, host, port)
    return mcp
