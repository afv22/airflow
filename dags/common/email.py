"""Thin wrapper around the Resend API for sending mail from DAGs."""

import resend

from airflow.sdk import Variable

RESEND_VAR_KEY = "resend-api-key"


def send_email(
    to: str | list[str],
    subject: str,
    html: str,
    name: str = "Mainframe",
) -> str:
    """Send an email and return the Resend message id."""
    resend.api_key = Variable.get(RESEND_VAR_KEY)
    response = resend.Emails.send(
        {
            "from": f"{name} <mainframe@avagliano.me>",
            "to": [to] if isinstance(to, str) else to,
            "subject": subject,
            "html": html,
        }
    )
    return response["id"]
