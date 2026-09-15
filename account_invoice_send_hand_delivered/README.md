# Invoice delivered by hand

For invoices you drop in a mailbox or hand over the counter. Adds **Lämnad för
hand** to the invoice *Send* dialog: nothing is sent, the invoice is marked as
sent and a note with date and user is posted. For several invoices at once,
select them in the list and use *Action → Markera som lämnad för hand*.

Odoo only sets *Sent* the first time the PDF is generated, so the flag is set
explicitly here; an invoice whose PDF already existed is marked correctly.
