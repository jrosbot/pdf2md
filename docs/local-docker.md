# Run PDF2MD MCP locally with Docker

The Docker image runs PDF2MD's Streamable HTTP MCP server. Docker Compose builds
an image for the machine's CPU architecture and publishes the HTTP port only on
the local computer. The same commands work on Ubuntu, macOS, and Windows with
Docker Compose installed.

## Start the container

Clone the repository and enter its folder:

```sh
git clone https://github.com/jrosbot/pdf2md.git
cd pdf2md
```

Copy the development environment template:

```sh
# Ubuntu or macOS
cp .env.docker.example .env
```

```powershell
# Windows PowerShell
Copy-Item .env.docker.example .env
```

Build and start the server:

```sh
docker compose up --build
```

In a second terminal, check the health endpoint:

```sh
# Ubuntu or macOS
curl http://localhost:8000/healthz
```

```powershell
# Windows PowerShell
Invoke-RestMethod http://localhost:8000/healthz
```

The response should be `{"status":"ok","service":"pdf2md"}`. This checks
that the web server started; it does not authenticate an MCP client or test a
conversion. The database is kept in the named `pdf2md-data` Docker volume.

Stop the server with Ctrl+C, or from another terminal run:

```sh
docker compose down
```

To also delete the local test account database and quota history:

```sh
docker compose down -v
```

## Connect ChatGPT for a local test

The MCP endpoint is `http://localhost:8000/mcp`. A ChatGPT-hosted conversation
cannot call a developer computer's `localhost` address directly. For a private
server test, use OpenAI's Secure MCP Tunnel: create a tunnel in the Platform
settings, run `tunnel-client` on the same computer, and associate the tunnel
with the ChatGPT workspace. In the ChatGPT workspace, add a custom MCP server
using the tunnel connection. The Docker port stays bound to loopback; no inbound
public port is needed.

The tunnel provides a private path to the local MCP endpoint and carries MCP
OAuth discovery. To test a tool call, configure at least one sign-in method:
Google, Microsoft, Apple, or email/password with Brevo. The development example
has no credentials, so it only supports the health check. With a sign-in method
configured, use the development-only authenticated `echo` tool to check the
MCP/OAuth round trip before trying a PDF.

For a local desktop test, the browser can reach `http://localhost:8000` while
the callback completes. Register the exact callback URL for the provider you
are testing. Apple requires an HTTPS domain, so it needs a reachable HTTPS
development hostname; production OAuth credentials are not interchangeable
with the local callback URL. Keep `tunnel-client` running while connecting and
testing. The tunnel carries OAuth discovery, but it does not automatically
forward the browser-facing authorization server: make sure the browser can
reach the login and callback URLs.

Alternatively, use an MCP Inspector on the same computer and point it to
`http://localhost:8000/mcp`. For ChatGPT Work Desktop, use the app installed for
your operating system and select the workspace's tunnel connection.

## File handling

When a PDF is attached to a ChatGPT conversation, ChatGPT stores that attachment
and provides the MCP tool with a short-lived download URL. With this local
container test, the tunnel routes the tool call to your computer and the local
container fetches and converts the file. This avoids sending the PDF to a
separate hosted PDF2MD server, but attaching it to ChatGPT still sends it to
ChatGPT.

For a public plugin, a private tunnel is not a deployment endpoint. Public
distribution needs a stable HTTPS MCP endpoint and production OAuth credentials.
