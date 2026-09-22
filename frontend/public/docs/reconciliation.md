# Reconciliation & data quality

Open **Reports → Reconciliation** to run read-only checks and download the full
audit as JSON. Nothing in this screen changes stock, acquisition lots or COGS.

The checks compare:

- Each inventory row's quantity with its full signed history.
- Each FIFO pool's remaining lot units with its stock across all rows, including
  archived rows. A pool consists of card/custom SKU, condition and printing.
- Lot bounds and allocations: remaining units cannot be negative or exceed the
  original lot quantity, and remaining plus consumed cannot exceed that quantity.
- Order-line COGS with linked FIFO costs, and sold quantities with allocated units.

Migrated Airtable orders without FIFO links are reported separately. Their
historical COGS is preserved; missing links do not automatically mean the costs
are wrong. Likewise, archived stock and $0 lots are review notes, not automatic
instructions to delete stock or invent costs.

## Reading an inventory record

**Qty** in the lot table is that lot's original quantity. **Remaining** is its
current balance. Original quantities include additions that were later sold,
removed or transferred; they do not need to equal today's stock.

The lot table covers the shared FIFO pool, including other bins and languages.
The stock history covers only the selected inventory row. Both totals are now
shown explicitly. A merge posts a positive transfer to the surviving row and
the matching negative transfer to the retired row; the original intake stays in
the original row's history. A merge does not create a purchase.

## Known limitations under investigation

The September 2026 audit found gaps in quantity-decrease handling, import undo,
bulk cost edits, oversold-order reversals and purchase reporting. The audit
screen detects discrepancies; it does not fix those mutation paths or repair
historical records. Resolve reported differences before relying on the affected
cost totals.

Purchase totals are reconstructed from acquisition lots. Legacy lots do not
reliably distinguish purchases from internal transfers or adjustments, and $0
does not distinguish a verified free unit from missing cost data. Purchase
invoices and historical sale records remain necessary for reconciliation.

## Command-line audit

From the backend directory:

```powershell
python audit_inventory.py --output ../audit-output/inventory.json
python diagnose_reconciliation.py --output ../audit-output/workflows.json
```

The first command opens SQLite in read-only mode and runs all checks on one
consistent snapshot. It does not start the app or run migrations. Use
`--database PATH` to audit a backup instead. The second exercises known risky
workflows in disposable in-memory databases and reports which invariants fail.
Its `DEFECT` results are unresolved defects, not passing tests.
It exits with status 1 when any invariant fails, so it can be used as a repair gate.

Audit outputs and database backups are local and excluded from Git. Review
historical corrections individually, preserve a backup, and record the reason
and source evidence for each correction.
