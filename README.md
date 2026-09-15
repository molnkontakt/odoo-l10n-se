# odoo-l10n-se

Odoo 19 Community modules for Swedish accounting: bank statement imports
(Swedbank, Bankgirot), OCR payment references and payment reminders with
the statutory reminder fee. Sister repository of
[odoo-l10n-se-skv](https://github.com/molnkontakt/odoo-l10n-se-skv), which
holds the Skatteverket-facing modules (VAT return / eSKD).

## Modules

| Module | Description |
|--------|-------------|
| [`account_statement_import_swedbank_csv`](account_statement_import_swedbank_csv/) | Swedbank CSV statement export in the OCA import wizard; balance-based dedupe; automatic Swish matching on the payer's mobile number |
| [`account_statement_import_bankgirot_xlsx`](account_statement_import_bankgirot_xlsx/) | Bankgirot *Insättningsuppgifter → Detaljer* (XLSX): splits a lump-sum Bankgiro deposit into payer details, matches invoices by OCR/name/amount and reconciles automatically |
| [`l10n_se_ocr`](l10n_se_ocr/) | OCR payment reference (Luhn check digit, length digit) on customer invoices |
| [`account_invoice_reminder`](account_invoice_reminder/) | Payment reminders: levels per company (days after due date, statutory fee as a separate posted invoice), PDF, e-mail, history, review dialog. Follow-up is Enterprise-only and the OCA modules are not on 19 |

Each module installs independently. `account_statement_import_swedbank_csv`
requires OCA `account_statement_import_file`; the automatic reconciliation in
both bank modules is optional and only active when OCA `account_reconcile_oca`
is installed.

## Disclaimer

Modules in this repository are under active development. **Use at your own
risk.** No functionality has been verified for every possible scenario, bank
export variation, chart of accounts or fiscal setup. You are solely
responsible for the correctness of every reconciliation, reminder and fee
before relying on the result.

Molnkontakt AB disclaims all liability for errors, omissions, incorrect
bookkeeping, wrongly sent reminders or fees, or any other consequences,
direct, indirect, incidental or consequential, arising from use of these
modules. Always reconcile against your bank and consult a qualified
accountant if uncertain. This disclaimer supplements the warranty and
liability terms in LGPL-3.

*På svenska:* Modulerna är under utveckling och tillhandahålls i befintligt
skick. All användning sker på eget ansvar. Molnkontakt AB friskriver sig från
allt ansvar för felaktigheter i bokföring, avstämning, påminnelser och
avgifter som kan uppstå vid användning.

## License

LGPL-3, same as the surrounding Odoo CE ecosystem.
