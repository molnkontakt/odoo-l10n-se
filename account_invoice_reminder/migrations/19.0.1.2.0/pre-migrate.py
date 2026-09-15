"""1.2.0: avgiften faktureras inte längre separat. Flytta nivåernas avgiftsprodukt → belopp + intäktskonto och
sätt fee_amount på befintliga påminnelser från deras avgiftsfaktura (som ligger kvar via fee_move_id)."""


def migrate(cr, version):
    cr.execute("ALTER TABLE account_reminder_level ADD COLUMN IF NOT EXISTS fee_account_id integer")
    cr.execute("ALTER TABLE account_reminder ADD COLUMN IF NOT EXISTS fee_amount numeric")
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name='account_reminder_level' AND column_name='fee_product_id'")
    if cr.fetchone():
        # belopp: nivåns eget belopp, annars produktens listpris; konto: produktens intäktskonto per bolag
        cr.execute("""
            UPDATE account_reminder_level l
               SET fee_amount = COALESCE(NULLIF(l.fee_amount, 0), pt.list_price)
              FROM product_product pp JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE pp.id = l.fee_product_id AND (l.fee_amount IS NULL OR l.fee_amount = 0)
        """)
        # intäktskontot sätts av administratören på nivån (produktens konto är bolagsberoende jsonb i 19)
    cr.execute("""
        UPDATE account_reminder r
           SET fee_amount = m.amount_total
          FROM account_move m
         WHERE m.id = r.fee_move_id AND (r.fee_amount IS NULL OR r.fee_amount = 0)
    """)
