"""19.0.1.1.0: the OCR flag fields came from l10n_se_payment_file_seb_csv with its Swedish help texts
("SEB CSV-exporten ..."). A module update keeps existing translations; replace exactly those two
texts, and only where they are still the old SEB CSV wording (a text someone edited is kept)."""

HELP_SV = {
    "l10n_se_ocr_required": (
        "Det här bankgiro- eller plusgironumret tar bara emot betalningar med giltigt OCR-nummer. "
        "Betalfiler vägrar fakturor till det som saknar ett giltigt OCR-nummer i betalningsreferensen."
    ),
    "l10n_se_ocr_refused": (
        "Det här bankgiro- eller plusgironumret tar bara emot textmeddelanden, inte OCR eller "
        "RF-referens. Betalfiler skickar leverantörens fakturanummer som meddelande, även när fakturan "
        "har ett giltigt OCR-nummer."
    ),
}


def migrate(cr, version):
    if not version:
        return
    for field, text in HELP_SV.items():
        cr.execute(
            """
            UPDATE ir_model_fields
               SET help = jsonb_set(help, '{sv_SE}', to_jsonb(%s::text))
             WHERE name = %s
               AND model IN ('res.partner.bank', 'account.setup.bank.manual.config')
               AND help->>'sv_SE' LIKE %s
            """,
            (text, field, "%SEB CSV%"),
        )
