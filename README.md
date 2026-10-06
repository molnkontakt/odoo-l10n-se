# odoo-l10n-se

Odoo 19 Community modules for Swedish accounting: bank statement imports
(Swedbank CSV, Enable Banking PSD2 feed, Bankgirot), supplier payment files (SEB CSV, ISO 20022
pain.001), OCR payment references, payment reminders with the statutory reminder fee, a
calendar of statutory dates and SIE 4 import and export. Sister repository of
[odoo-l10n-se-skv](https://github.com/molnkontakt/odoo-l10n-se-skv), which
holds the Skatteverket-facing modules (VAT return / eSKD).

## Modules

| Module | Description |
|--------|-------------|
| [`account_reconcile_oca_mobile`](account_reconcile_oca_mobile/) | Makes the OCA bank reconciliation view readable on phones: stacked layout, wrapping buttons, reconciliation lines and matching list as cards (styling only) |
| [`account_statement_import_swedbank_csv`](account_statement_import_swedbank_csv/) | Swedbank CSV statement export in the OCA import wizard; balance-based dedupe; automatic Swish matching on the payer's mobile number |
| [`account_statement_import_online_enable_banking`](account_statement_import_online_enable_banking/) | Enable Banking (PSD2) as an OCA online statement provider: scheduled pull of booked transactions and closing balance, Swish payer matching; needs `account_statement_import_online` |
| [`account_statement_import_bankgirot_xlsx`](account_statement_import_bankgirot_xlsx/) | Bankgiro deposit details from Bankgirot *Insättningsuppgifter* (XLSX) or the bank's ISO 20022 **camt.054**: splits a lump-sum Bankgiro deposit into payer details, matches invoices by OCR/name/amount and reconciles automatically |
| [`l10n_se_bank_account`](l10n_se_bank_account/) | Swedish account types and check digits for payment files: Bankgiro, Plusgiro, bank accounts written per clearing number range (MIG Annex 5), IBAN, OCR and RF references; a pure-Python library for payment-file writers |
| [`l10n_se_payment_file_seb_csv`](l10n_se_payment_file_seb_csv/) | Vendor bills to SEB's CSV payment upload (*Domestic* template: Bankgiro, Plusgiro, bank account, IBAN, OCR) without OCA or registered payments; per-bill checks, payment files that keep a bill from being paid twice. Value formats not yet verified with SEB |
| [`l10n_se_account_banking_pain`](l10n_se_account_banking_pain/) | Swedish domestic supplier payments in ISO 20022 **pain.001** (Bankgiro, Plusgiro, bank account, IBAN, OCR) on top of OCA `account_banking_sepa_credit_transfer`; bank profile SEB |
| [`l10n_se_ocr`](l10n_se_ocr/) | OCR payment reference (Luhn check digit, length digit) on customer invoices |
| [`account_invoice_send_ekopost`](account_invoice_send_ekopost/) | *Brev via Ekopost* in the invoice Send dialog: the PDF is posted as a letter through Ekopost's API |
| [`account_invoice_send_sms_46elks`](account_invoice_send_sms_46elks/) | *SMS via 46elks*: payment info and a portal link as a text message |
| [`account_invoice_send_hand_delivered`](account_invoice_send_hand_delivered/) | *Lämnad för hand*: mark invoices delivered by hand (dialog checkbox + list action) |
| [`account_invoice_reminder`](account_invoice_reminder/) | Payment reminders: levels per company (days after due date, statutory fee as a separate posted invoice), PDF, e-mail, history, review dialog. Follow-up is Enterprise-only and the OCA modules are not on 19 |
| [`account_invoice_reminder_ekopost`](account_invoice_reminder_ekopost/), [`account_invoice_reminder_sms_46elks`](account_invoice_reminder_sms_46elks/) | Glue (auto-installed): payment reminders by letter / SMS |
| [`l10n_se_sie4`](l10n_se_sie4/) | SIE 4 files: import the bookkeeping from Fortnox, Visma/Spiris and others (preview, opening balance, vouchers, missing accounts, dimensions as analytics, lock dates, no duplicates, undo, reconciliation against #UB/#RES), reconciliation only, and export as SIE 4E/4I/1/2/3 for the auditor; a pure-Python reader and writer |
| [`l10n_se_compliance_calendar`](l10n_se_compliance_calendar/) | Annual cycle of statutory dates in Calendar, computed from the law and the company's settings (VAT, EC sales list, employer declaration, F-tax, income tax return, AGM, annual report; moved to the next weekday where Skatteverket says so), plus custom fixed and relative dates for associations. No feed, no network |
| [`l10n_se_bolagsverket`](l10n_se_bolagsverket/) | Bolagsverket's free API (*värdefulla datamängder*): fetch registered name and address from the organisation number, and watch customers and suppliers for bankruptcy, liquidation, reorganisation and deregistration (banner on the contact and its invoices, to-do); warns when a supplier's name doesn't match its number |
| [`mail_server_rate_limit`](mail_server_rate_limit/) | Not Swedish-specific: at most N messages per M minutes on an outgoing mail server, the rest waits in the mail queue; a temporary (4xx) refusal is retried only for the recipients not yet reached |

Each module installs independently, except that the payment-file modules
(`l10n_se_payment_file_seb_csv`, `l10n_se_account_banking_pain`) depend on
`l10n_se_bank_account`, and `l10n_se_account_banking_pain` on OCA
`account_banking_sepa_credit_transfer`. `account_statement_import_swedbank_csv`
requires OCA `account_statement_import_file`; the automatic reconciliation in
both bank modules is optional and only active when OCA `account_reconcile_oca`
is installed.

## Disclaimer

Modules in this repository are under active development. **Use at your own
risk.** No functionality has been verified for every possible scenario, bank
export variation, chart of accounts or fiscal setup. You are solely
responsible for the correctness of every payment file, payment,
reconciliation, reminder and fee before relying on the result.

Molnkontakt AB disclaims all liability for errors, omissions, incorrect
bookkeeping, wrongly sent reminders or fees, or any other consequences,
direct, indirect, incidental or consequential, including payments made to
the wrong account or paid twice, arising from use of these modules. Always reconcile against your bank and consult a qualified
accountant if uncertain. This disclaimer supplements the warranty and
liability terms in LGPL-3.

*På svenska:* Modulerna är under utveckling och tillhandahålls i befintligt
skick. All användning sker på eget ansvar. Molnkontakt AB friskriver sig från
allt ansvar för felaktigheter i betalfiler, betalningar, bokföring,
avstämning, påminnelser och avgifter som kan uppstå vid användning.

## License

LGPL-3, same as the surrounding Odoo CE ecosystem, except
`account_statement_import_online_enable_banking`: AGPL-3, because the OCA module it extends
(`account_statement_import_online`) is AGPL-3.
