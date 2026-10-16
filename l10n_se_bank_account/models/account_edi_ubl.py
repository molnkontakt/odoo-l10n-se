from odoo import models

from ..lib import se_bank


class AccountEdiUbl(models.AbstractModel):
    """Swedish giro numbers in imported UBL/Peppol bills.

    ``cac:PaymentMeans/cac:PayeeFinancialAccount`` of a Swedish supplier carries the bare giro
    digits in ``cbc:ID`` and says which giro it is in ``cac:FinancialInstitutionBranch/cbc:ID``
    (``SE:BANKGIRO``/``SE:PLUSGIRO``, Peppol BIS Billing 3 Swedish rules). Odoo reads only the
    digits, so the account is created as ``8-digit number``, type *Other*, and payment files
    cannot use it. This makes the account ``BG 555-1239``/``PG 123456-7`` with its type set,
    reuses an account the supplier already has with the same digits, and puts the Bankgiro first
    when the bill offers both.
    """

    _inherit = "account.edi.ubl"

    def _import_ubl_invoice_add_partner_bank_values(self, collected_values):
        super()._import_ubl_invoice_add_partner_bank_values(collected_values)
        partner_bank_values = collected_values.get("partner_bank_values")
        if partner_bank_values is None or collected_values.get("odoo_document_type") != "purchase":
            return  # own accounts (sale documents) are never created from a file
        tree = collected_values["tree"]
        giros = {}  # raw cbc:ID text -> (giro type, formatted number or None), in document order
        for node in tree.findall("./{*}PaymentMeans/{*}PayeeFinancialAccount"):
            id_node = node.find("{*}ID")
            raw = (id_node.text or "").strip() if id_node is not None else ""
            if not raw or raw in giros:
                continue
            scheme = node.findtext("{*}FinancialInstitutionBranch/{*}ID") or id_node.get("schemeID")
            giro_type = se_bank.giro_type_from_scheme(scheme)
            if not giro_type:
                continue
            valid = (se_bank.bankgiro_valid if giro_type == "bankgiro" else se_bank.plusgiro_valid)(raw)
            giros[raw] = (giro_type, se_bank.format_giro(raw, giro_type) if valid else None)
        partner_bank_values["l10n_se_giros"] = giros
        # the supplier may be found through a bank account it already has, written as a giro
        partner_bank_values["account_numbers"] |= {fmt for _type, fmt in giros.values() if fmt}

    def _import_ubl_retrieve_partner_bank(self, collected_values):
        giros = collected_values.get("partner_bank_values", {}).get("l10n_se_giros")
        invoice = collected_values["invoice"]
        partner = collected_values.get("customer_values", {}).get("customer")
        if not giros or invoice.move_type != "in_invoice" or not partner:
            # only a vendor bill names the supplier's accounts; a vendor credit note is paid to
            # the company's own account, which is never created from a file
            return super()._import_ubl_retrieve_partner_bank(collected_values)

        partner_bank_values = collected_values["partner_bank_values"]
        logs = collected_values["logs"]
        company = collected_values["company"]
        PartnerBank = self.env["res.partner.bank"]
        type_names = dict(PartnerBank._fields["l10n_se_account_type"]._description_selection(self.env))
        giro_banks = {"bankgiro": PartnerBank, "plusgiro": PartnerBank}
        handled = set()
        for raw, (giro_type, formatted) in giros.items():
            if not formatted:
                logs.append(
                    self.env._(
                        "%(giro)s number %(number)s in the file has an invalid check digit. The "
                        "account was kept as it is, with the type Other: check the number with the "
                        "supplier before paying.",
                        giro=type_names[giro_type],
                        number=raw,
                    )
                )
                continue  # Odoo creates the raw number as usual
            handled.update({raw, formatted})
            bank = self._l10n_se_find_giro_account(partner, company, raw, giro_type)
            if bank and bank.l10n_se_account_type == "other":
                logs.append(
                    self.env._(
                        "Bank account %(account)s of %(partner)s has no Swedish account type; the "
                        "file says it is a %(giro)s number. Fill in the type on the account to pay it "
                        "with a payment file.",
                        account=bank.acc_number,
                        partner=partner.display_name,
                        giro=type_names[giro_type],
                    )
                )
            if not bank:
                bank = PartnerBank._find_or_create_bank_account(
                    account_number=formatted,
                    partner=partner,
                    company=company,
                    extra_create_vals={"l10n_se_account_type": giro_type},
                )
            giro_banks[giro_type] |= bank

        # let Odoo handle the rest (IBANs, numbers without a scheme, invalid giro numbers)
        account_numbers = partner_bank_values["account_numbers"]
        partner_bank_values["account_numbers"] = account_numbers - handled
        try:
            super()._import_ubl_retrieve_partner_bank(collected_values)
        finally:
            partner_bank_values["account_numbers"] = account_numbers

        # Swedish practice: pay the Bankgiro when the bill offers one, else the Plusgiro
        others = partner_bank_values.get("partner_banks") or PartnerBank
        banks = giro_banks["bankgiro"] | giro_banks["plusgiro"] | others
        partner_bank_values["partner_banks"] = banks
        if banks:
            collected_values["to_write"]["partner_bank_id"] = banks[0].id

    def _l10n_se_find_giro_account(self, partner, company, number, giro_type):
        """A bank account of the supplier with the same giro digits, however it is written.

        ``BG 555-1239``, ``555-1239`` and ``5551239`` are the same Bankgiro, but Odoo's sanitized
        number keeps the ``BG``, so the standard lookup would create a second account. An account
        of the same giro type is preferred over one with the type Other; an account typed as the
        other giro (or a bank account/IBAN) is not the same payee and is never reused.
        """
        digits = se_bank.account_digits(number)
        candidates = (
            self.env["res.partner.bank"]
            .sudo()
            .search(
                [
                    ("partner_id", "child_of", partner.commercial_partner_id.id),
                    ("l10n_se_account_type", "in", (giro_type, "other")),
                    *self.env["res.partner.bank"]._check_company_domain(company),
                ]
            )
            .filtered(lambda bank: se_bank.account_digits(bank.acc_number) == digits)
            .sorted(lambda bank: (bank.l10n_se_account_type != giro_type, bank.partner_id != partner))
        )
        return candidates.sudo(False)[:1]
