"""Useful cron diagnostics without logging provider messages or local variables."""
import traceback


SAFE_MESSAGES = {
    'Missing or changed share table', 'Malformed trade row', 'Empty table',
    'Configure ALERT_EMAIL_TO and EMAIL_HOST_USER (or DEFAULT_FROM_EMAIL)',
    'Configure EMAIL_HOST_PASSWORD when EMAIL_HOST_USER is set',
    'EMAIL_PORT must be an integer between 1 and 65535',
}


def describe_failure(exc):
    detail = str(exc) if type(exc) is ValueError and str(exc) in SAFE_MESSAGES else None
    frames = traceback.extract_tb(exc.__traceback__)
    locations = ' -> '.join(f'{frame.filename}:{frame.lineno} in {frame.name}' for frame in frames)
    summary = type(exc).__name__ + (f': {detail}' if detail else '')
    return summary + (f'\nFailure locations: {locations}' if locations else '')
