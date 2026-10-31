#!/usr/bin/env python3
"""Run once daily with cron; alert on new or updated published share trades."""
import argparse
from collections import Counter
from email.message import EmailMessage
import fcntl
import json
import os
from pathlib import Path
import smtplib
import ssl
import sys

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.nakedtrader.co.uk/trades.htm'
HEADERS = ['Stock', 'Epic', 'Qty', 'Price', 'Target', 'Stop', 'Buy Date', 'Sell', 'Sell Date', 'P/L']


def parse_trades(html):
    table = BeautifulSoup(html, 'html.parser').select_one('table.trades')
    if table is None or [c.get_text(' ', strip=True) for c in table.select('th')] != HEADERS:
        raise ValueError('Missing or changed share table')
    rows = []
    for tr in table.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if not cells:
            continue
        row = [' '.join(c.get_text(' ', strip=True).split()) for c in cells]
        if len(row) != len(HEADERS):
            raise ValueError('Malformed trade row')
        # The live log contains empty placeholder rows and historical entries
        # with missing buy dates. Preserve historical rows; skip placeholders.
        if not row[0] and not row[1]:
            continue
        rows.append(row)
    if not rows:
        raise ValueError('Empty table')
    return rows


def fetch_trades():
    with requests.Session() as session:
        session.headers['User-Agent'] = 'Tradezens daily trade-log checker'
        response = session.get('https://www.nakedtrader.co.uk/agree.htm?agree=1', timeout=30)
        response.raise_for_status()
        response = session.get(URL, timeout=30)
        response.raise_for_status()
        return parse_trades(response.text)


def changed_rows(previous, current):
    remaining = Counter(tuple(row) for row in previous)
    changes = []
    for row in current:
        key = tuple(row)
        if remaining[key]:
            remaining[key] -= 1
        else:
            changes.append(row)
    return changes


def send_alert(rows):
    recipient = os.environ.get('NAKED_TRADER_EMAIL_TO')
    sender = os.environ.get('DEFAULT_FROM_EMAIL') or os.environ.get('EMAIL_HOST_USER')
    if not recipient or not sender:
        raise ValueError('Configure NAKED_TRADER_EMAIL_TO and EMAIL_HOST_USER')
    message = EmailMessage()
    message['Subject'] = f'Naked Trader: {len(rows)} new or updated share trade(s)'
    message['From'] = sender
    message['To'] = recipient
    lines = ['New or updated rows in the published share trade log:', URL, '']
    for row in rows:
        lines.extend(f'{name}: {value or "—"}' for name, value in zip(HEADERS, row))
        lines.append('')
    lines.append('Updates may include corrections and target/stop changes. Publication can lag the actual trade.')
    message.set_content('\n'.join(lines))
    with smtplib.SMTP(os.environ.get('EMAIL_HOST', 'smtp.gmail.com'), int(os.environ.get('EMAIL_PORT', '587')), timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if os.environ.get('EMAIL_HOST_USER'):
            smtp.login(os.environ['EMAIL_HOST_USER'], os.environ['EMAIL_HOST_PASSWORD'])
        smtp.send_message(message)


def check(state, fetch=fetch_trades, send=send_alert, dry_run=False):
    current = fetch()
    previous = json.loads(state.read_text())['rows'] if state.exists() else None
    changes = changed_rows(previous, current) if previous is not None else []
    if dry_run:
        print(f'Read {len(current)} trades; {len(changes)} updates; baseline exists: {previous is not None}. No email/state writes.')
        return
    if changes:
        send(changes)  # Do not advance state if delivery fails.
    temporary = state.with_suffix('.tmp')
    temporary.write_text(json.dumps({'rows': current}, indent=2) + '\n')
    temporary.replace(state)
    print(f'Baseline saved: {len(current)} trades.' if previous is None else f'Checked {len(current)} trades; emailed {len(changes)} updates.')


def main():
    load_dotenv(ROOT / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / '.naked_trader' / 'state.json')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix('.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another checker is running; skipped.')
            return
        check(args.state, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Checker failed ({type(exc).__name__}); saved state was not advanced.', file=sys.stderr)
        sys.exit(1)
