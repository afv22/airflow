"""Thin wrapper around the Resend API for sending mail from DAGs."""

import resend

from airflow.sdk import Variable

RESEND_VAR_KEY = "resend-api-key"
FROM_ADDRESS = "mainframe@avagliano.me"


def send_email(
    to: str | list[str],
    subject: str,
    html: str,
    from_address: str = FROM_ADDRESS,
) -> str:
    """Send an email and return the Resend message id."""
    resend.api_key = Variable.get(RESEND_VAR_KEY)
    response = resend.Emails.send(
        {
            "from": from_address,
            "to": [to] if isinstance(to, str) else to,
            "subject": subject,
            "html": html,
        }
    )
    return response["id"]
