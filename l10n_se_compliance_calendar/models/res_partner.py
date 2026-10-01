from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    l10n_se_cc_company_id = fields.Many2one(
        "res.company",
        string="Compliance Calendar of",
        index="btree_not_null",
        ondelete="set null",
        readonly=True,
        copy=False,
        help="This contact is the attendee of the company's compliance calendar dates. It can "
        "never have an e-mail address, so that no invitation or reminder is ever sent.",
    )

    @api.constrains("email", "l10n_se_cc_company_id")
    def _check_l10n_se_cc_no_email(self):
        for partner in self:
            if partner.l10n_se_cc_company_id and partner.email:
                raise ValidationError(self.env._(
                    "The compliance calendar contact “%(name)s” must not have an e-mail address.",
                    name=partner.display_name,
                ))
