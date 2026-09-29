"""19.0.1.2.0: the OCR flags on res.partner.bank moved to l10n_se_bank_account.

Hand their ir.model.data records to l10n_se_bank_account before this module's update: otherwise an
update of this module alone would remove the fields it no longer defines - and drop their columns
with the values. When l10n_se_bank_account was updated first it has its own records already; ours
are then left for the normal cleanup.
"""

FIELDS = [
    f"field_{model}__{field}"
    for model in ("res_partner_bank", "account_setup_bank_manual_config")
    for field in ("l10n_se_ocr_required", "l10n_se_ocr_refused")
]


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = 'l10n_se_bank_account'
         WHERE d.module = 'l10n_se_payment_file_seb_csv'
           AND d.model = 'ir.model.fields'
           AND d.name = ANY(%s)
           AND NOT EXISTS (
                SELECT 1 FROM ir_model_data o
                 WHERE o.module = 'l10n_se_bank_account' AND o.name = d.name
           )
        """,
        (FIELDS,),
    )
