from lxml import etree

from odoo import api, models
from odoo.exceptions import UserError

# Swedish clearing numbers used as creditor agent for the giro systems
SE_CLEARING_BANKGIRO = "9900"
SE_CLEARING_PLUSGIRO = "9960"


class AccountPaymentOrder(models.Model):
    _inherit = "account.payment.order"

    def _l10n_se_profile(self):
        return self.payment_mode_id.l10n_se_bank_profile or False

    def generate_payment_file(self):
        self.ensure_one()
        if self._l10n_se_profile() and self.payment_method_id.code == "sepa_credit_transfer":
            self._l10n_se_check_order()
        return super().generate_payment_file()

    def _l10n_se_check_order(self):
        if not (self.payment_mode_id.l10n_se_customer_id or "").strip():
            raise UserError(
                self.env._(
                    "Betalningssättet %(mode)s saknar kund-id i banken (krävs i svenska betalfiler).",
                    mode=self.payment_mode_id.display_name,
                )
            )
        for payment in self.payment_ids:
            if payment.currency_id.name != "SEK":
                raise UserError(
                    self.env._(
                        "Svenska inrikes betalningar måste vara i SEK: %(partner)s (%(ccy)s).",
                        partner=payment.partner_id.display_name,
                        ccy=payment.currency_id.name,
                    )
                )
            bank = payment.partner_bank_id
            if bank and bank.l10n_se_account_type == "other":
                raise UserError(
                    self.env._(
                        "Kontot %(acc)s för %(partner)s har ingen känd svensk kontotyp. "
                        "Ange bankgiro, plusgiro, bankkonto eller IBAN på kontot.",
                        acc=bank.acc_number,
                        partner=payment.partner_id.display_name,
                    )
                )

    # --- Payment information block -------------------------------------------------------

    def generate_start_payment_info_block(
        self,
        parent_node,
        payment_info_ident,
        priority,
        local_instrument,
        category_purpose,
        sequence_type,
        requested_date,
        eval_ctx,
        gen_args,
    ):
        payment_info, nb_of_transactions, control_sum = super().generate_start_payment_info_block(
            parent_node,
            payment_info_ident,
            priority,
            local_instrument,
            category_purpose,
            sequence_type,
            requested_date,
            eval_ctx,
            gen_args,
        )
        if self._l10n_se_profile() and gen_args.get("payment_method") == "TRF":
            # SEB 7.10.x: batch booking follows Bankgirot's logic (omit), service level MPNS
            batch = payment_info.find("BtchBookg")
            if batch is not None:
                payment_info.remove(batch)
            pmt_tp = payment_info.find("PmtTpInf")
            for child in list(pmt_tp):
                if child.tag in ("InstrPrty", "SvcLvl"):
                    pmt_tp.remove(child)
            svc = etree.Element("SvcLvl")
            etree.SubElement(svc, "Prtry").text = "MPNS"
            pmt_tp.insert(0, svc)
        return payment_info, nb_of_transactions, control_sum

    # --- Parties -------------------------------------------------------------------------

    def _l10n_se_add_bank_customer_id(self, party_node):
        ident = etree.SubElement(party_node, "Id")
        org = etree.SubElement(ident, "OrgId")
        other = etree.SubElement(org, "Othr")
        etree.SubElement(other, "Id").text = self.payment_mode_id.l10n_se_customer_id.strip()
        scheme = etree.SubElement(other, "SchmeNm")
        etree.SubElement(scheme, "Cd").text = "BANK"

    @api.model
    def generate_initiating_party_block(self, parent_node, gen_args):
        if not self._l10n_se_profile():
            return super().generate_initiating_party_block(parent_node, gen_args)
        initiating_party = etree.SubElement(parent_node, "InitgPty")
        etree.SubElement(initiating_party, "Nm").text = self._prepare_field(
            "Company Name",
            "self.company_partner_bank_id.partner_id.name",
            {"self": self},
            gen_args.get("name_maxsize"),
            gen_args=gen_args,
        )
        self._l10n_se_add_bank_customer_id(initiating_party)
        return True

    @api.model
    def generate_party_id(self, parent_node, party_type, partner):
        if self._l10n_se_profile() and party_type == "Dbtr":
            self._l10n_se_add_bank_customer_id(parent_node)
            return
        return super().generate_party_id(parent_node, party_type, partner)

    @api.model
    def generate_address_block(self, parent_node, partner, gen_args):
        # Swedish domestic payments need no postal address for either party
        if self._l10n_se_profile():
            return True
        return super().generate_address_block(parent_node, partner, gen_args)

    @api.model
    def generate_party_agent(
        self, parent_node, party_type, order, partner_bank, gen_args, bank_line=None
    ):
        if not (self._l10n_se_profile() and party_type == "Cdtr" and order == "C"):
            return super().generate_party_agent(
                parent_node, party_type, order, partner_bank, gen_args, bank_line=bank_line
            )
        acc_type = partner_bank.l10n_se_account_type
        clearing = {"bankgiro": SE_CLEARING_BANKGIRO, "plusgiro": SE_CLEARING_PLUSGIRO}.get(acc_type)
        if acc_type == "bban":
            # Optional per the MIG (the full BBAN identifies the bank); the sample gives the
            # first four digits of the clearing number
            clearing = partner_bank._l10n_se_account_digits()[:4]
        if clearing:
            agent = etree.SubElement(parent_node, "CdtrAgt")
            inst = etree.SubElement(agent, "FinInstnId")
            clr = etree.SubElement(inst, "ClrSysMmbId")
            clr_id = etree.SubElement(clr, "ClrSysId")
            etree.SubElement(clr_id, "Cd").text = "SESBA"
            etree.SubElement(clr, "MmbId").text = clearing
        elif acc_type == "iban":
            bic = partner_bank._l10n_se_bic()
            if bic:
                agent = etree.SubElement(parent_node, "CdtrAgt")
                inst = etree.SubElement(agent, "FinInstnId")
                etree.SubElement(inst, gen_args.get("bic_xml_tag")).text = bic
        return True

    @api.model
    def generate_party_acc_number(
        self, parent_node, party_type, order, partner_bank, gen_args, bank_line=None
    ):
        if not (self._l10n_se_profile() and party_type == "Cdtr"):
            return super().generate_party_acc_number(
                parent_node, party_type, order, partner_bank, gen_args, bank_line=bank_line
            )
        acc_type = partner_bank.l10n_se_account_type
        if acc_type == "iban":
            return super().generate_party_acc_number(
                parent_node, party_type, order, partner_bank, gen_args, bank_line=bank_line
            )
        account = etree.SubElement(parent_node, "CdtrAcct")
        ident = etree.SubElement(account, "Id")
        other = etree.SubElement(ident, "Othr")
        etree.SubElement(other, "Id").text = partner_bank._l10n_se_account_digits()
        scheme = etree.SubElement(other, "SchmeNm")
        if acc_type == "bankgiro":
            etree.SubElement(scheme, "Prtry").text = "BGNR"
        else:  # plusgiro and bban
            etree.SubElement(scheme, "Cd").text = "BBAN"
        return True

    # --- Remittance ----------------------------------------------------------------------

    @api.model
    def generate_remittance_info_block(self, parent_node, line, gen_args):
        if not self._l10n_se_profile():
            return super().generate_remittance_info_block(parent_node, line, gen_args)
        payment_line = line.payment_line_ids[:1]
        acc_type = line.partner_bank_id.l10n_se_account_type
        remittance = etree.SubElement(parent_node, "RmtInf")
        if payment_line.communication_type == "ocr" and acc_type in ("bankgiro", "plusgiro"):
            structured = etree.SubElement(remittance, "Strd")
            creditor_ref = etree.SubElement(structured, "CdtrRefInf")
            ref_type = etree.SubElement(creditor_ref, "Tp")
            code_or = etree.SubElement(ref_type, "CdOrPrtry")
            etree.SubElement(code_or, "Cd").text = "SCOR"
            etree.SubElement(creditor_ref, "Ref").text = self._prepare_field(
                "Creditor Structured Reference",
                "line.payment_reference",
                {"line": line},
                25,
                gen_args=gen_args,
            )
        else:
            etree.SubElement(remittance, "Ustrd").text = self._prepare_field(
                "Remittance Unstructured Information",
                "line.payment_reference",
                {"line": line},
                140,
                gen_args=gen_args,
            )
        return True

    def finalize_sepa_file_creation(self, xml_root, gen_args):
        if self._l10n_se_profile():
            # SEB 7.10.x: charges DEBT or SHAR, never SLEV (SEPA)
            for charge_bearer in xml_root.iter("ChrgBr"):
                if charge_bearer.text == "SLEV":
                    charge_bearer.text = "SHAR"
        return super().finalize_sepa_file_creation(xml_root, gen_args)
