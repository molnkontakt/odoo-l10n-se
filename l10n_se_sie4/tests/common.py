import base64
from decimal import Decimal
from pathlib import Path
from unittest import SkipTest

from odoo import Command
from odoo.tests.common import TransactionCase, new_test_user

DATA = Path(__file__).parent / "data"


class SieCase(TransactionCase):
    """Companies of their own with the Swedish chart of accounts (l10n_se, needed for the test
    run: install it together with this module) and an accounting administrator."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.env["ir.module.module"].search_count(
                [("name", "=", "l10n_se"), ("state", "=", "installed")]):
            raise SkipTest("l10n_se is not installed: run the tests with -i l10n_se,l10n_se_sie4")
        cls.sek = cls.env.ref("base.SEK")
        cls.sek.active = True
        cls.company = cls._create_company("Testbolaget SIE AB")
        cls.user = new_test_user(
            cls.env, login="sie_manager", name="SIE Manager",
            groups="base.group_user,account.group_account_manager",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.env = cls.env(user=cls.user, context=dict(
            cls.env.context, allowed_company_ids=cls.company.ids))

    @classmethod
    def _create_company(cls, name):
        env = cls.env(su=True)
        company = env["res.company"].create({
            "name": name, "currency_id": cls.sek.id, "country_id": env.ref("base.se").id,
        })
        env.user.company_ids |= company
        env["account.chart.template"].try_loading("se", company=company, install_demo=False)
        company.currency_id = cls.sek
        return company

    @classmethod
    def _as_user_of(cls, company):
        cls.user.sudo().company_ids |= company
        return cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))

    # -- files and wizards

    def _data(self, name):
        return (DATA / name).read_bytes()

    def _attachments(self, env, files):
        Attachment = env["ir.attachment"]
        out = Attachment
        for name, data in files:
            out |= Attachment.create({
                "name": name,
                "datas": base64.b64encode(data),
                "res_model": "l10n_se.sie.import.wizard",
            })
        return out

    def _wizard(self, *names, files=None, env=None, **vals):
        env = env or self.env
        files = list(files or []) + [(n, self._data(n)) for n in names]
        wizard = env["l10n_se.sie.import.wizard"].create({
            "attachment_ids": [Command.set(self._attachments(env, files).ids)],
        })
        wizard.action_analyse()
        if vals:
            wizard.write(vals)
        return wizard

    def _import(self, *names, files=None, env=None, **vals):
        wizard = self._wizard(*names, files=files, env=env, **vals)
        action = wizard.action_import()
        return (env or self.env)["l10n_se.sie.import"].browse(action["res_id"])

    # -- the books

    def _balances(self, company, date_to, date_from=None, states=("posted",)):
        """{code: Decimal} of the company's lines up to date_to (from date_from)."""
        domain = [("company_id", "=", company.id), ("parent_state", "in", states),
                  ("date", "<=", date_to)]
        if date_from:
            domain.append(("date", ">=", date_from))
        out = {}
        for account, balance in self.env["account.move.line"].sudo()._read_group(
                domain, ["account_id"], ["balance:sum"]):
            code = account.with_company(company).code
            out[code] = out.get(code, Decimal(0)) + Decimal(str(round(balance, 2)))
        return {k: v for k, v in out.items() if v}

    def _account(self, code, company=None):
        company = company or self.company
        Account = self.env["account.account"].with_company(company).with_context(
            active_test=False)
        return Account.search([*Account._check_company_domain(company), ("code", "=", code)])

    def _entry(self, date, lines, company=None, ref="Manual entry", post=True):
        """A journal entry with [(code, amount), ...] in the company's general journal."""
        company = company or self.company
        env = self.env(context=dict(self.env.context, allowed_company_ids=company.ids))
        journal = env["account.journal"].search([
            *env["account.journal"]._check_company_domain(company), ("type", "=", "general")],
            limit=1)
        move = env["account.move"].create({
            "move_type": "entry", "date": date, "ref": ref, "journal_id": journal.id,
            "company_id": company.id,
            "line_ids": [Command.create({
                "account_id": self._account(code, company).id, "name": ref,
                "debit": amount if amount > 0 else 0.0, "credit": -amount if amount < 0 else 0.0,
            }) for code, amount in lines],
        })
        if post:
            move.action_post()
        return move
