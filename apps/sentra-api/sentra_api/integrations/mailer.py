"""Gmail SMTP sender (B-45, R-22): STARTTLS, login with an app password, one message to many recipients.

A failure raises `MailError` with a short message that carries no password and no address. The caller records the call in
`integration_call` (`gmail`) and the result in `notification`.
"""

import smtplib
import ssl
from collections.abc import Iterable
from email.message import EmailMessage

from sentra_api.config import Settings

TIMEOUT_S = 15
ROLE_ATTRS = {"BUYER": "role_email_buyer", "PPIC": "role_email_ppic", "SALES": "role_email_sales",
              "SCM_MANAGER": "role_email_scm_manager", "CFO": "role_email_cfo", "COO": "role_email_coo",
              "CEO": "role_email_ceo", "LEGAL": "role_email_legal"}


class MailError(Exception):
    """The e-mail could not be sent; `args[0]` is a short message without secrets."""


def addresses_for(settings: Settings, roles: Iterable[str]) -> list[str]:
    """`ROLE_EMAIL_<ROLE>` of each role: empty ones are skipped and a duplicate address is sent once (in the order of the roles)."""
    found: list[str] = []
    for role in roles:
        address = (getattr(settings, ROLE_ATTRS[role], None) or "").strip()
        if address and address not in found:
            found.append(address)
    return found


def smtp_ready(settings: Settings) -> bool:
    return bool(settings.smtp_user and settings.smtp_app_password)


def _login(smtp: smtplib.SMTP, settings: Settings) -> None:
    smtp.ehlo()
    smtp.starttls(context=ssl.create_default_context())
    smtp.ehlo()
    smtp.login(settings.smtp_user, settings.smtp_app_password.get_secret_value())


def send_mail(settings: Settings, *, recipients: list[str], subject: str, text: str, html: str | None = None) -> None:
    """One message to all `recipients` (the `To` header lists them)."""
    if not smtp_ready(settings):
        raise MailError("SMTP is not configured")
    if not recipients:
        raise MailError("No recipient address is configured")
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, settings.mail_from or settings.smtp_user, ", ".join(recipients)
    message.set_content(text)
    if html is not None:
        message.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT_S) as smtp:
            _login(smtp, settings)
            smtp.send_message(message, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError:
        raise MailError("SMTP authentication failed") from None
    except (smtplib.SMTPException, OSError) as failure:
        raise MailError(f"SMTP send failed ({type(failure).__name__})") from None


def check_login(settings: Settings) -> None:
    """Only the connection and the login (no message): `POST /api/system/checks`."""
    if not smtp_ready(settings):
        raise MailError("SMTP is not configured")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT_S) as smtp:
            _login(smtp, settings)
    except smtplib.SMTPAuthenticationError:
        raise MailError("SMTP authentication failed") from None
    except (smtplib.SMTPException, OSError) as failure:
        raise MailError(f"SMTP login failed ({type(failure).__name__})") from None
