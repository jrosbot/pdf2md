from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from pdf2md_core import convert_one

from .auth import PDF2MDOAuthProvider
from .config import Settings
from .storage import SQLiteStore


class PdfAttachment(BaseModel):
    """A ChatGPT file reference made available to an MCP tool."""

    model_config = ConfigDict(extra="forbid")

    download_url: AnyHttpUrl = Field(description="Short-lived HTTPS URL from the ChatGPT file service")
    file_id: str = Field(min_length=1, description="ChatGPT file identifier")
    # Keep these optional properties as plain string schemas. ChatGPT omits
    # them when unavailable; the file handler validator expects `type: string`
    # for all four supported properties.
    mime_type: str = Field(default=None, description="The attachment MIME type")  # type: ignore[assignment]
    file_name: str = Field(default=None, description="Original attachment name")  # type: ignore[assignment]


class DownloadError(RuntimeError):
    pass


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
        return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)
    except ValueError:
        pass
    if not allowed_hosts:
        return True
    return any(host == allowed or host.endswith(allowed.removeprefix("*")) for allowed in allowed_hosts)


def _public_addresses(host: str) -> bool:
    """Reject DNS names resolving to private addresses to reduce SSRF risk."""

    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    return bool(addresses) and all(_host_allowed(address, ()) for address in addresses)


async def _download_pdf(settings: Settings, store: SQLiteStore, user_id: str, attachment: PdfAttachment) -> Path:
    parsed = urlparse(str(attachment.download_url))
    if parsed.scheme != "https":
        raise DownloadError("PDF download URLs must use HTTPS")
    if not parsed.hostname or not _host_allowed(parsed.hostname, settings.allowed_download_hosts):
        raise DownloadError("The PDF download host is not allowed")
    if not _public_addresses(parsed.hostname):
        raise DownloadError("The PDF download host is unavailable or resolves to a private address")

    usage_date = datetime.now(timezone.utc).date().isoformat()
    temp = tempfile.NamedTemporaryFile(prefix="pdf2md-", suffix=".pdf", delete=False)
    temp_path = Path(temp.name)
    total = 0
    completed = False
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            async with client.stream("GET", str(attachment.download_url), headers={"Accept": "application/pdf"}) as response:
                response.raise_for_status()
                raw_length = response.headers.get("content-length")
                if raw_length and raw_length.isdigit():
                    declared_size = int(raw_length)
                    if declared_size > settings.max_file_bytes:
                        raise DownloadError("The PDF exceeds the per-file size limit")
                async for chunk in response.aiter_bytes(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > settings.max_file_bytes:
                        raise DownloadError("The PDF exceeds the per-file size limit")
                    if not store.try_reserve_usage(
                        user_id=user_id,
                        amount=len(chunk),
                        usage_date=usage_date,
                        daily_limit=settings.daily_quota_bytes,
                    ):
                        raise DownloadError("The daily PDF quota has been reached")
                    temp.write(chunk)
        temp.flush()
        if total == 0:
            raise DownloadError("The PDF download was empty")
        completed = True
        return temp_path
    except (httpx.HTTPError, OSError) as exc:
        raise DownloadError("Could not download the PDF") from exc
    finally:
        temp.close()
        if not completed:
            temp_path.unlink(missing_ok=True)


def create_server(settings: Settings | None = None) -> tuple[MCPServer, SQLiteStore]:
    settings = settings or Settings.from_env()
    settings.validate(production=os.getenv("PDF2MD_ENV", "development").lower() == "production")
    store = SQLiteStore(settings.database_path)
    oauth = PDF2MDOAuthProvider(settings, store)
    server = MCPServer(
        name="pdf2md",
        title="PDF2MD",
        description="Convert PDF attachments to Markdown without retaining the source document.",
        instructions=(
            "Use convert_pdf_to_markdown for PDF attachments. After it returns, create and attach a .md file "
            "containing the returned Markdown. The remote service processes the PDF temporarily and does not "
            "retain the source or generated Markdown."
        ),
        website_url="https://github.com/jrosbot/pdf2md",
        version="0.2.0",
        auth_server_provider=oauth,
        auth=AuthSettings(
            issuer_url=settings.issuer_url,
            resource_server_url=settings.resource_url,
            validate_token_resource=True,
            required_scopes=["pdf:convert"],
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["pdf:convert"],
                default_scopes=["pdf:convert"],
            ),
        ),
    )

    @server.tool(
        name="convert_pdf_to_markdown",
        title="Convert PDF to Markdown",
        description=(
            "Convert one PDF attachment to Markdown. The file parameter must contain the ChatGPT file's "
            "download_url, file_id, file_name and mime_type when available. Return the Markdown text only; "
            "the ChatGPT agent should create and attach a .md file from this result."
        ),
        meta={"openai/fileParams": ["file"]},
        structured_output=False,
    )
    async def convert_pdf_to_markdown(file: PdfAttachment, ctx: Context) -> str:
        access_token = get_access_token()
        if not access_token or not access_token.subject:
            raise PermissionError("A PDF2MD account is required")
        source_path: Path | None = None
        try:
            source_path = await _download_pdf(settings, store, access_token.subject, file)
            with tempfile.TemporaryDirectory(prefix="pdf2md-output-") as output_dir:
                destination = await asyncio.to_thread(convert_one, source_path, Path(output_dir))
                markdown = destination.read_text(encoding="utf-8")
            await ctx.info(f"Converted {file.file_name or file.file_id} to Markdown")
            return markdown
        finally:
            if source_path:
                source_path.unlink(missing_ok=True)

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "service": "pdf2md"})

    @server.custom_route("/login", methods=["GET"], include_in_schema=False)
    async def login(request: Request) -> Response:
        return await oauth.login_page(request)

    @server.custom_route("/oauth/login/{provider}", methods=["GET"], include_in_schema=False)
    async def provider_login(request: Request) -> Response:
        return await oauth.start_provider_login(request)

    @server.custom_route("/oauth/callback/{provider}", methods=["GET"], include_in_schema=False)
    async def provider_callback(request: Request) -> Response:
        return await oauth.provider_callback(request)

    return server, store


settings = Settings.from_env()
server, store = create_server(settings)
app = server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=os.getenv("PDF2MD_STATELESS_HTTP", "false").lower() == "true",
    max_request_body_size=settings.max_file_bytes,
    host="0.0.0.0",
)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("pdf2md_server.server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

