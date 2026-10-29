"""Sends login codes by text message and email.

The "log" provider writes codes to the server log: fine for development, useless for
real users. Real sending needs credentials: Twilio for SMS, any SMTP server for
email. See config.example.json.
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Protocol

import httpx

from game_platform.config import EmailAuthConfig, SmsAuthConfig

logger = logging.getLogger(__name__)


class SenderError(Exception):
    """The code couldn't be sent."""


class CodeSender(Protocol):
    def send_login_code(self, destination: str, code: str) -> None:
        """Sends [code] to a phone number or email address. Raises [SenderError]."""
        ...


class LogSender:
    """Pretends to send by logging the code. For development only."""

    def send_login_code(self, destination: str, code: str) -> None:
        logger.info("login code for %s: %s", destination, code)


class TwilioSender:
    """Sends texts through Twilio (https://www.twilio.com)."""

    def __init__(
        self, *, account_sid: str, auth_token: str, from_number: str
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number

    def send_login_code(self, destination: str, code: str) -> None:
        try:
            response = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/Messages.json",
                auth=(self._account_sid, self._auth_token),
                data={
                    "From": self._from_number,
                    "To": destination,
                    "Body": (
                        f"Your game platform login code is {code}. "
                        "It expires in 10 minutes."
                    ),
                },
                timeout=10,
            )
        except httpx.HTTPError as error:
            raise SenderError(f"couldn't reach Twilio: {error}") from error
        if response.status_code >= 300:
            raise SenderError(f"Twilio refused to send: {response.text[:200]}")


class TwilioVoiceSender:
    """Send a six-digit verification code by automated phone call."""

    def __init__(self, *, account_sid, auth_token, from_number):
        self.sid = account_sid
        self.token = auth_token
        self.from_number = from_number

    def send_login_code(self, destination, code):
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise SenderError("invalid verification code")

        spoken = ". ".join(code)
        twiml = (
            "<Response>"
            "<Say>Your BuildPlay login code is.</Say>"
            f"<Say>{spoken}.</Say>"
            f"<Say>Again. {spoken}.</Say>"
            "</Response>"
        )

        try:
            response = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls.json",
                auth=(self.sid, self.token),
                data={
                    "From": self.from_number,
                    "To": destination,
                    "Twiml": twiml,
                },
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise SenderError(
                "couldn't reach Twilio for phone verification"
            ) from exc

        if response.status_code >= 300:
            raise SenderError(
                "Twilio could not place the verification call"
            )


class TwilioVerifyClient:
    def __init__(self, *, account_sid: str, auth_token: str, service_sid: str):
        self.sid = account_sid
        self.token = auth_token
        self.service_sid = service_sid

    def _request(self, action: str, payload: dict[str, str]) -> dict:
        if not (self.sid and self.token and self.service_sid):
            raise SenderError("Twilio Verify credentials are not configured")
        try:
            result = httpx.post(
                f"https://verify.twilio.com/v2/Services/{self.service_sid}/{action}",
                auth=(self.sid, self.token),
                data=payload,
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise SenderError("Could not connect to Twilio Verify") from exc

        if result.status_code >= 300:
            if action == "VerificationCheck" and result.status_code == 404:
                return {"status": "expired"}
            raise SenderError(
                f"Twilio Verify failed (HTTP {result.status_code})"
            )
        try:
            return result.json()
        except ValueError as exc:
            raise SenderError("Unexpected Twilio Verify response") from exc

    def start_verification(self, number: str, channel: str) -> None:
        if channel not in ("sms", "call"):
            raise SenderError("Unsupported verification channel")
        result = self._request(
            "Verifications", {"To": number, "Channel": channel}
        )
        if result.get("status") != "pending":
            raise SenderError("Twilio did not start the verification")

    def check_verification(self, number: str, code: str) -> bool:
        if not (len(code) == 6 and code.isascii() and code.isdigit()):
            return False
        result = self._request(
            "VerificationCheck", {"To": number, "Code": code}
        )
        return result.get("status") == "approved"


def make_verify_client(config: SmsAuthConfig) -> TwilioVerifyClient | None:
    if config.provider != "verify":
        return None
    return TwilioVerifyClient(
        account_sid=os.environ.get("TWILIO_ACCOUNT_SID")
            or config.twilio_account_sid,
        auth_token=os.environ.get("TWILIO_AUTH_TOKEN")
            or config.twilio_auth_token,
        service_sid=os.environ.get("TWILIO_VERIFY_SERVICE_SID")
            or config.twilio_verify_service_sid,
    )


class SmtpSender:
    """Sends email through any SMTP server."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
        use_tls: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_address = from_address
        self._use_tls = use_tls

    def send_login_code(self, destination: str, code: str) -> None:
        message = EmailMessage()
        message["Subject"] = "Your game platform login code"
        message["From"] = self._from_address
        message["To"] = destination
        message.set_content(
            f"Your game platform login code is {code}. It expires in 10 minutes.\n\n"
            "If you didn't ask for this, just ignore this email."
        )
        try:
            with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
                if self._use_tls:
                    smtp.starttls()
                if self._username:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as error:
            raise SenderError(f"couldn't send the email: {error}") from error


def make_sms_sender(config: SmsAuthConfig) -> CodeSender:
    if config.provider == "twilio":
        return TwilioSender(
            account_sid=os.environ.get("TWILIO_ACCOUNT_SID", config.twilio_account_sid),
            auth_token=os.environ.get("TWILIO_AUTH_TOKEN", config.twilio_auth_token),
            from_number=os.environ.get("TWILIO_FROM_NUMBER", config.twilio_from_number),
        )
    return LogSender()


def make_email_sender(config: EmailAuthConfig) -> CodeSender:
    if config.provider == "smtp":
        return SmtpSender(
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_username,
            password=config.smtp_password,
            from_address=config.smtp_from_address,
            use_tls=config.smtp_use_tls,
        )
    return LogSender()


def make_voice_sender(config: SmsAuthConfig) -> CodeSender:
    if config.provider != "twilio" or not config.twilio_voice_enabled:
        return LogSender()

    return TwilioVoiceSender(
        account_sid=os.environ.get("TWILIO_ACCOUNT_SID", config.twilio_account_sid),
        auth_token=os.environ.get("TWILIO_AUTH_TOKEN", config.twilio_auth_token),
        from_number=os.environ.get("TWILIO_FROM_NUMBER", config.twilio_from_number),
    )
