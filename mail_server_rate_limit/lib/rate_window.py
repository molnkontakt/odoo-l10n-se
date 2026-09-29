"""Sliding-window arithmetic for the per-server rate limit, without the ORM.

A *slot* is ``(at, count)``: ``count`` messages counted against a mail server at the naive UTC
datetime ``at``. A slot counts while ``at > now - window``, so it leaves the window at exactly
``at + window``.
"""

import re
from datetime import timedelta

# Deferred mail is scheduled this long after a slot leaves the window, so that a clock
# difference with the SMTP server does not land the next message just inside its window.
MARGIN = timedelta(seconds=15)

# A temporary SMTP answer as Odoo stores it in failure_reason, e.g.
#   SMTPRecipientsRefused: {'x@example.com': (451, b'4.7.1 [R2] Too many mails ...')}
#   SMTPSenderRefused: (451, b'4.7.1 Rate limited. Slow down', 'y@example.com')
#   SMTPDataError: (421, b'4.7.0 Try again later')
# i.e. smtplib's "(4xx," reply code, an RFC 3463 enhanced status code 4.x.x, or the wording.
# A bare three-digit number is not enough: "port 465" must not count as a 4xx answer.
TEMPORARY_SMTP_ERROR = re.compile(
    r"\(4\d\d,"
    r"|(?<![\d.])4\.\d{1,3}\.\d{1,3}(?![\d.])"
    r"|\btoo many (?:mails|messages)\b",
    re.IGNORECASE,
)


def is_temporary_smtp_error(reason):
    """True when ``reason`` (a failure_reason text) holds a temporary (4xx) SMTP answer."""
    return bool(reason and TEMPORARY_SMTP_ERROR.search(reason))


def used(slots, now, window):
    """Messages counted in the window that ends at ``now``."""
    start = now - window
    return sum(count for at, count in slots if at > start)


def schedule(slots, costs, now, window, limit, margin=MARGIN):
    """Earliest send time for each message cost, in order, without exceeding ``limit``.

    ``slots`` are the slots already counted (older ones are ignored); ``costs`` the messages
    still to send, first in line first. Every message gets a time no earlier than the one
    before it (first in, first out) and no window of length ``window`` holds more than
    ``limit``. A cost above ``limit`` is counted as ``limit``: such a message waits for an
    empty window, where it is split into parts that fit.

    :return: list of naive datetimes, one per cost
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    events = [(at, count) for at, count in slots if at > now - window]
    times = []
    t = now
    for cost in costs:
        cost = min(max(cost, 1), limit)
        while used(events, t, window) + cost > limit:
            # wait until the oldest slot still in the window has left it
            t = min(at for at, _count in events if at > t - window) + window + margin
        events.append((t, cost))
        times.append(t)
    return times
