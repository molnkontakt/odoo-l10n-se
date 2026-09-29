# Bank Reconciliation (OCA) – Mobile Layout

`account_reconcile_oca` lays out the reconciliation view for a desktop: the statement lines in a
30 % column next to the reconciliation form (70 %), a status bar with a fixed height, a wide table of
reconciliation lines and a matching list with seven columns. On a phone the line list shrinks to a
narrow strip where references break after nearly every character, the status bar buttons overlap the
table, and the amounts are cut off.

This module only adds styles for screens narrower than 768 px (Bootstrap's `md` breakpoint); the
desktop layout is unchanged.

- The statement lines are shown full width above the form (at most a third of the screen, scrollable),
  and the whole view scrolls as one page.
- The status bar buttons (*Reconcile*, *Reset*, *To check*, *Show entry*) wrap instead of overlapping.
- The reconciliation lines become cards: account and journal entry on top, label and date below, the
  amount on the right marked *Debet* or *Kredit*.
- The matching list becomes cards: journal entry and residual amount on top, partner and date below.
  Due date, account and label are hidden on phones, and so is the header row (sort on a desktop).

No logic, fields or views change. Remove the module to get the original layout back.

Tested on Odoo 19.0 with `account_reconcile_oca` 19.0.1.0.8 at 390 × 844 px.
