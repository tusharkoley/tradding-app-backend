import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from check_naked_trader import HEADERS, parse_trades, changed_rows, check


class CheckerTests(unittest.TestCase):
    def test_parser_rejects_disclaimer(self):
        with self.assertRaises(ValueError):
            parse_trades('<h1>Accept disclaimer</h1>')
        row = ['Example', 'EX', '100', '123', '150', '100', '01/10/2026', '', '', '']
        html = '<table class="trades"><tr>' + ''.join(f'<th>{h}</th>' for h in HEADERS) + '</tr><tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr></table>'
        self.assertEqual(parse_trades(html), [row])

    def test_reordering_archiving_duplicate_lots(self):
        self.assertEqual(changed_rows([['a'], ['b']], [['b']]), [])
        self.assertEqual(changed_rows([['a']], [['a'], ['a']]), [['a']])

    def test_baseline_sale_retry_no_repeat_and_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            send = Mock()
            check(state, lambda: [['open']], send)
            send.assert_not_called()
            baseline = state.read_text()
            send.side_effect = OSError('Mail unavailable')
            with self.assertRaises(OSError):
                check(state, lambda: [['closed']], send)
            self.assertEqual(state.read_text(), baseline)
            send.side_effect = None
            check(state, lambda: [['closed']], send)
            send.reset_mock()
            check(state, lambda: [['closed']], send)
            send.assert_not_called()
            check(state, lambda: [['new']], send, dry_run=True)
            send.assert_not_called()
            self.assertIn('closed', state.read_text())
