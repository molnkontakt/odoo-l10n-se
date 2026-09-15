# OCR payment reference (Sweden)

Generates the Swedish OCR payment reference on customer invoices: the invoice
number digits, a length digit and a Luhn (mod 10) check digit, as expected by
Bankgiro/Plusgiro OCR processing. The reference is written to
`payment_reference` when the invoice is posted and printed on the invoice.

No configuration. If your bank requires a fixed length or a different variant
(hard/soft length control), adjust `make_ocr()` in `models/account_move.py`.
