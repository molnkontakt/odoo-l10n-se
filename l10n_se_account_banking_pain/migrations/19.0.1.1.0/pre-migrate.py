"""1.1.0: res.partner.bank.l10n_se_account_type moved to l10n_se_bank_account.

The new module is installed before this one is upgraded (it is a dependency), and it finds the
column in place, so neither the column nor the values (including types set by hand) are touched.
Hand the field's and its selection values' external ids over to the new module, so that the end of
this upgrade does not remove them as data this module no longer declares. Where the new module
already created its own external id for the same record, this module's duplicate is dropped.
"""

import logging

_logger = logging.getLogger(__name__)

OLD = "l10n_se_account_banking_pain"
NEW = "l10n_se_bank_account"


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        SELECT id, name FROM ir_model_data
         WHERE module = %s
           AND (
                (model = 'ir.model.fields' AND name LIKE 'field\\_%%\\_\\_l10n\\_se\\_account\\_type')
             OR (model = 'ir.model.fields.selection'
                 AND name LIKE 'selection\\_\\_%%\\_\\_l10n\\_se\\_account\\_type\\_\\_%%')
           )
        """,
        [OLD],
    )
    moved = dropped = 0
    for imd_id, name in cr.fetchall():
        cr.execute("SELECT 1 FROM ir_model_data WHERE module = %s AND name = %s", [NEW, name])
        if cr.fetchone():
            cr.execute("DELETE FROM ir_model_data WHERE id = %s", [imd_id])
            dropped += 1
        else:
            cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s", [NEW, imd_id])
            moved += 1
    _logger.info(
        "l10n_se_account_type: %d external ids moved to %s, %d duplicates dropped", moved, NEW, dropped
    )
