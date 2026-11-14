import os
import smtplib
import ssl
from email.message import EmailMessage


def send_email(subject, body, recipient=None):
    recipient = recipient or os.environ.get('ALERT_EMAIL_TO') or os.environ.get('NAKED_TRADER_EMAIL_TO')
    sender = os.environ.get('DEFAULT_FROM_EMAIL') or os.environ.get('EMAIL_HOST_USER')
    if not recipient or not sender:
        raise ValueError('Configure ALERT_EMAIL_TO and EMAIL_HOST_USER (or DEFAULT_FROM_EMAIL)')
    if os.environ.get('EMAIL_HOST_USER') and not os.environ.get('EMAIL_HOST_PASSWORD'):
        raise ValueError('Configure EMAIL_HOST_PASSWORD when EMAIL_HOST_USER is set')
    try:
        port = int(os.environ.get('EMAIL_PORT', '587'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError('EMAIL_PORT must be an integer between 1 and 65535') from None
    message = EmailMessage()
    message['Subject'], message['From'], message['To'] = subject, sender, recipient
    message.set_content(body)
    with smtplib.SMTP(os.environ.get('EMAIL_HOST', 'smtp.gmail.com'), port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if os.environ.get('EMAIL_HOST_USER'):
            smtp.login(os.environ['EMAIL_HOST_USER'], os.environ['EMAIL_HOST_PASSWORD'])
        smtp.send_message(message)
