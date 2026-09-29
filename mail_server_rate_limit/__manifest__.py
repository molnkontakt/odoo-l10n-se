{
    "name": "Mail Server Rate Limit",
    "version": "19.0.1.0.0",
    "category": "Hidden/Tools",
    "summary": "Most messages per window for an outgoing mail server: overflow waits in the queue, "
    "temporary (4xx) errors are retried for the recipients not yet reached",
    "description": """
For SMTP providers that refuse mail above a rate ("451 Too many mails").

* On an outgoing mail server: at most N messages per M minutes (0 = no limit), counted per
  recipient address over a sliding window
* Mail through a limited server is only sent by the email queue cron; what does not fit waits in
  the queue with a scheduled date and the cron is triggered for it
* A temporary (4xx) answer puts the mail back in the queue for the recipients it has not reached,
  a limited number of times, then it fails as before
    """,
    "author": "Molnkontakt AB",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [
        "security/ir.model.access.csv",
        "views/ir_mail_server_views.xml",
        "views/mail_mail_views.xml",
    ],
    "installable": True,
}
