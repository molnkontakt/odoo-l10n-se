"""pytest for lib/rate_window.py, without Odoo.

Run from the repository root: ``python -m pytest -p no:cacheprovider mail_server_rate_limit/tests/pytest``.
The library is loaded from its file so the Odoo package (and its ``odoo`` import) is never imported.
"""

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[2] / "lib" / "rate_window.py"
_spec = importlib.util.spec_from_file_location("rate_window", _LIB)
rate_window = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rate_window)

NOW = datetime(2026, 1, 5, 10, 0, 0)
WINDOW = timedelta(minutes=5)
MARGIN = rate_window.MARGIN


@pytest.mark.parametrize("reason", [
    "Mail Delivery Failed\nMail delivery failed via SMTP server 'None'.\n"
    "SMTPRecipientsRefused: {'a@example.com': (451, b'4.7.1 [R2] Too many mails received from x within the last 5 minutes')}",
    "SMTPSenderRefused: (451, b'4.7.1 [M7] Rate limited. Slow down', 'b@example.com')",
    "SMTPDataError: (421, b'Service not available, closing channel')",
    "SMTPDataError: (452, b'Requested action not taken: insufficient system storage')",
    "Server said: 4.7.0 try again later",
    "Too many mails",
])
def test_temporary_errors(reason):
    assert rate_window.is_temporary_smtp_error(reason)


@pytest.mark.parametrize("reason", [
    None,
    "",
    "SMTPRecipientsRefused: {'a@example.com': (550, b'5.1.1 No such user')}",
    "SMTPDataError: (554, b'5.7.1 Message rejected')",
    "Connection refused: smtp.example.com:465",
    "[Errno 111] Connection refused 10.4.1.20",
    "Error without exception. Probably due to sending an email without computed recipients.",
])
def test_not_temporary_errors(reason):
    assert not rate_window.is_temporary_smtp_error(reason)


def test_used_counts_only_the_window():
    slots = [(NOW - WINDOW, 5), (NOW - WINDOW + timedelta(seconds=1), 2), (NOW, 1)]
    # a slot exactly one window old has left it
    assert rate_window.used(slots, NOW, WINDOW) == 3


def test_schedule_fills_whole_windows():
    times = rate_window.schedule([(NOW, 3)], [1, 1, 1, 1], NOW, WINDOW, 3)
    first = NOW + WINDOW + MARGIN
    assert times == [first, first, first, first + WINDOW + MARGIN]


def test_schedule_uses_room_as_old_slots_leave():
    slots = [(NOW - timedelta(minutes=4), 2), (NOW, 1)]
    times = rate_window.schedule(slots, [1, 1, 1], NOW, WINDOW, 3)
    early = NOW + timedelta(minutes=1) + MARGIN
    # the 2 from four minutes ago leave after one minute; the 1 sent now only after five
    assert times == [early, early, NOW + WINDOW + MARGIN]


def test_schedule_is_first_in_first_out():
    # a small message never jumps ahead of a bigger one that is waiting
    times = rate_window.schedule([(NOW, 2)], [2, 1], NOW, WINDOW, 3)
    assert times[1] >= times[0]
    assert times[0] == NOW + WINDOW + MARGIN


def test_schedule_now_when_there_is_room():
    assert rate_window.schedule([], [1, 2], NOW, WINDOW, 3) == [NOW, NOW]


def test_schedule_caps_a_message_larger_than_the_limit():
    times = rate_window.schedule([(NOW, 1)], [10, 1], NOW, WINDOW, 3)
    first = NOW + WINDOW + MARGIN
    assert times == [first, first + WINDOW + MARGIN]


def test_schedule_after_a_penalty():
    # a temporary error counts a full window: nothing before it has passed
    times = rate_window.schedule([(NOW, 3), (NOW, 3)], [1, 1], NOW, WINDOW, 3)
    assert times == [NOW + WINDOW + MARGIN] * 2


def test_schedule_needs_a_limit():
    with pytest.raises(ValueError):
        rate_window.schedule([], [1], NOW, WINDOW, 0)
