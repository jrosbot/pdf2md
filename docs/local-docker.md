# Run PDF2MD MCP locally with Docker

The Docker image runs PDF2MD's Streamable HTTP MCP server. Docker Compose builds
an image for the machine's CPU architecture and publishes the HTTP port only on
the local computer. The same commands work on Ubuntu, macOS, and Windows with
Docker Compose installed.

## Start the container

Because this work is in an unmerged pull request, clone its branch:

```sh
git clone --branch codex/auth-mvp-email-password --single-branch https://github.com/jrosbot/pdf2md.git
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

The example runs in development mode and stores its database in the named
`pdf2md-data` Docker volume. Do not switch `PDF2MD_ENV` to `production` for
this test: the development-only authenticated `echo` tool is useful for
checking the login round trip.

## Give the local app an HTTPS development URL with ngrok

ChatGPT cannot reach your laptop's `localhost` directly. ngrok forwards an
HTTPS URL to the local Docker port while the ngrok process is running. This
makes the app reachable from the internet for testing; it does not deploy the
app or keep it online after you stop the tunnel.

1. Install ngrok and sign in to an ngrok account. Add the ngrok authtoken to
   the ngrok CLI configuration once; do not put it in the PDF2MD `.env` file
   or commit it to GitHub.
2. Use the stable development domain assigned to your ngrok account, if
   available. Find it in the ngrok dashboard under **Gateway → Domains**.
3. Edit `.env` and set the public URLs to that exact HTTPS hostname:

   ```dotenv
   PDF2MD_PUBLIC_BASE_URL=https://<your-ngrok-domain>
   PDF2MD_ISSUER_URL=https://<your-ngrok-domain>
   PDF2MD_RESOURCE_URL=https://<your-ngrok-domain>/mcp
   ```

4. Configure at least one sign-in provider in `.env`. For Google, Microsoft,
   or Apple, add its client ID and secret. Register the exact PDF2MD callback
   URL in that provider's developer settings:

   ```text
   https://<your-ngrok-domain>/oauth/callback/google
   https://<your-ngrok-domain>/oauth/callback/microsoft
   https://<your-ngrok-domain>/oauth/callback/apple
   ```

   Start with one provider. Follow [OAuth credential setup](oauth-credentials.md)
   for provider configuration. Apple web sign-in requires a registered HTTPS
   domain and return URL; a stable hostname is especially helpful. For
   email/password, configure Brevo instead.

5. Start the app after saving `.env`:

   ```sh
   docker compose up --build --force-recreate
   ```

6. In a second terminal, start ngrok on port 8000, using the same stable
   hostname:

   ```sh
   ngrok http 8000 --url https://<your-ngrok-domain>
   ```

   Keep both terminals running. Check the public health endpoint from the
   laptop:

   ```sh
   curl https://<your-ngrok-domain>/healthz
   ```

   It should return `{"status":"ok","service":"pdf2md"}`.

The tunnel makes this development app publicly reachable while it is running.
Use development provider credentials and test accounts, and do not test with
confidential PDFs. The ngrok free plan may show a browser warning page; if that
interrupts the OAuth browser flow, use an endpoint without that interstitial
and keep the same registered HTTPS hostname. See ngrok's
[development-domain details](https://ngrok.com/blog/free-static-domains-ngrok-users).

## Connect from ChatGPT Work Desktop

Create the custom MCP connection once in ChatGPT on the web, then use the
resulting plugin in the ChatGPT Work desktop app. Current custom MCP setup is
through ChatGPT's Plugins page; plugin availability in Desktop still depends on
the workspace and account permissions.

1. In ChatGPT on the web, open **Plugins**, select **+**, then **Add custom MCP
   server**.
2. Name it **PDF2MD local test** and set the server URL to:

   ```text
   https://<your-ngrok-domain>/mcp
   ```

3. Choose OAuth, review the risk notice, and create the plugin. Complete the
   PDF2MD sign-in with the provider you configured. If ChatGPT reports that it
   cannot discover the server or login, check that all three PDF2MD public URL
   settings and the provider callback URL use the same hostname.
4. Install the resulting plugin. Open ChatGPT Work Desktop, start a new Work
   chat, and select or mention **PDF2MD local test**.
5. First ask it to call the authenticated `echo` tool with a short test string.
   This checks ChatGPT → ngrok → Docker and PDF2MD authentication without
   uploading a document.
6. Then attach a small, non-sensitive PDF and ask PDF2MD to convert it to
   Markdown and create/attach a `.md` file.

If the custom-MCP option is unavailable, the workspace may need an admin to
enable the relevant plugin/MCP permissions. OpenAI's current setup steps are in
[Connecting custom MCP servers in ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

To stop exposing the development app, stop ngrok with Ctrl+C. Stop Docker with
Ctrl+C or run `docker compose down`. To delete the local test account database
and quota history as well, run `docker compose down -v`.

## Optional: private connection with OpenAI Secure MCP Tunnel

If you only need to test MCP calls and do not need a public HTTPS hostname, use
OpenAI Secure MCP Tunnel instead. It keeps the MCP endpoint private, but it
does not automatically make PDF2MD's browser-facing authorization server
reachable. For real Google, Microsoft, or Apple login, use the ngrok HTTPS
steps above or provide another reachable HTTPS URL for the auth endpoints.

## File handling

When a PDF is attached to a ChatGPT conversation, ChatGPT stores that attachment
and provides the MCP tool with a short-lived download URL. With this local
container test, ngrok routes the tool call to your computer and the local
container fetches and converts the file. This avoids sending the PDF to a
separate hosted PDF2MD server, but attaching it to ChatGPT still sends it to
ChatGPT.

For a public plugin, this temporary ngrok URL is for testing only. Public
distribution needs a stable, always-available HTTPS MCP endpoint and production
OAuth credentials. See OpenAI's
[plugin connection and testing guide](https://developers.openai.com/plugins/deploy/connect-chatgpt).
