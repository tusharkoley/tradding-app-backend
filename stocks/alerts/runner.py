from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.check_naked_trader import run as run_naked_trader
from stocks.alerts.email import send_email
from stocks.alerts.weekly import build_report, DataNotReady

DEFAULT_CONFIG = Path(__file__).with_name('config.json')


def due_slot(schedule, now):
    frequency = schedule['frequency']
    if frequency == 'daily':
        hours = schedule['hours']
        if not hours or any(type(h) is not int or not 0 <= h <= 23 for h in hours):
            raise ValueError('Daily hours must be integers 0..23')
        due = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours]
        due = [slot for slot in due if slot <= now]
        return max(due).isoformat() if due else None
    if frequency == 'weekly':
        weekday, hour = schedule['weekday'], schedule['hour']
        if type(weekday) is not int or not 0 <= weekday <= 6 or type(hour) is not int or not 0 <= hour <= 23:
            raise ValueError('Invalid weekly weekday/hour')
        monday = now.date() - timedelta(days=now.weekday())
        slot = datetime.combine(monday + timedelta(days=weekday), datetime.min.time(), tzinfo=now.tzinfo).replace(hour=hour)
        return slot.isoformat() if slot <= now else None
    raise ValueError('Unknown schedule frequency')


def naked_trader(options, slot, state_dir, dry_run, recipient):
    run_naked_trader(state_dir / 'naked-trader' / 'state.json', dry_run=dry_run, recipient=recipient)


def weekly_performers(options, slot, state_dir, dry_run, recipient):
    subject, body = build_report(datetime.fromisoformat(slot).date(), options)
    if dry_run:
        print(subject + '\n' + body, flush=True)
    else:
        send_email(subject, body, recipient)


HANDLERS = {'naked_trader': naked_trader, 'weekly_performers': weekly_performers}


def run_alerts(config_path, state_dir, dry_run=False, only=None, now=None):
    config = json.loads(Path(config_path).read_text())
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(config['timezone']))
    alerts = config['alerts']
    if set(alerts) - set(HANDLERS) or (only and only not in alerts):
        raise ValueError('Unknown alert name')
    # Validate the entire configuration before invoking handlers.
    slots = {}
    for name, options in alerts.items():
        if type(options.get('enabled')) is not bool:
            raise ValueError('enabled must be true or false')
        if options['enabled']:
            slots[name] = due_slot(options['schedule'], now)
            if name == 'weekly_performers' and (options['schedule']['frequency'] != 'weekly' or options['schedule']['weekday'] != 4):
                raise ValueError('Weekly performers must be scheduled on Friday')
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    with (state_dir / 'runner.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another alert runner is active; skipped.', flush=True)
            return
        state_path = state_dir / 'sent.json'
        sent = json.loads(state_path.read_text()) if state_path.exists() else {}
        for name, slot in slots.items():
            if only and only != name:
                continue
            if not slot or sent.get(name) == slot:
                print(f'{name}: not due or already completed.', flush=True)
                continue
            print(f'{name}: running slot {slot}; dry_run={dry_run}', flush=True)
            try:
                HANDLERS[name](alerts[name], slot, state_dir, dry_run, config.get('recipient'))
                if not dry_run:
                    sent[name] = slot
                    temporary = state_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(sent, indent=2) + '\n')
                    temporary.replace(state_path)
                print(f'{name}: completed.', flush=True)
            except Exception as exc:
                errors.append(name)
                print(f'{name}: failed ({type(exc).__name__}); will retry. Check database/data, SMTP and volume configuration.', flush=True)
                if isinstance(exc, DataNotReady):
                    print(str(exc), flush=True)
    if errors:
        raise RuntimeError('Failed alerts: ' + ', '.join(errors))
