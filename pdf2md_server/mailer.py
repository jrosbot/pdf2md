from __future__ import annotations

import html

import httpx

from .config import Settings


class BrevoMailer:
    """Send PDF2MD transactional messages through Brevo's SMTP API."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def send_email_verification(self, recipient: str, verification_url: str) -> None:
        await self._send(
            recipient,
            "Verify your email address for PDF2MD",
            "Use this link to verify your email address and continue creating or linking your PDF2MD sign-in method.",
            f'<p>Use this link to verify your email address and continue creating or linking your PDF2MD sign-in method:</p>'
            f'<p><a href="{html.escape(verification_url, quote=True)}">Verify email address</a></p>'
            "<p>If you did not request this, you can ignore this message.</p>",
        )

    async def send_password_reset(self, recipient: str, reset_url: str) -> None:
        await self._send(
            recipient,
            "Reset your PDF2MD password",
            "Use this link to set a new PDF2MD password. If you did not request this, ignore the message.",
            f'<p>Use this link to set a new PDF2MD password:</p>'
            f'<p><a href="{html.escape(reset_url, quote=True)}">Reset password</a></p>'
            "<p>If you did not request this, you can ignore this message.</p>",
        )

    async def _send(self, recipient: str, subject: str, text: str, html_content: str) -> None:
        if not self.settings.brevo_api_key or not self.settings.email_from:
            raise RuntimeError("Transactional email is not configured")
        payload = {
            "sender": {"name": self.settings.email_from_name, "email": self.settings.email_from},
            "to": [{"email": recipient}],
            "subject": subject,
            "textContent": text,
            "htmlContent": html_content,
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"accept": "application/json", "api-key": self.settings.brevo_api_key,
                         "content-type": "application/json"},
                json=payload,
            )
            if response.is_error:
                raise RuntimeError("Brevo rejected the transactional email")
