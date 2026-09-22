# Reconciliation and reviewed repairs

Open **Reports → Reconciliation**. Running checks is read-only. **Review repair**
opens actions suited to the finding; nothing is applied until you enter an
explanation, preview the effects and select **Apply this reviewed repair**.

## Stock and cost lots describe the same cards

Stock is the number of physical units on hand. Acquisition lots describe their
purchase dates and costs. For example, stock of 2 can consist of one unit bought
for $2.33 and one for $7.643. FIFO chooses the oldest unit's cost when stock leaves.
The sum of remaining lot units must equal stock; it is not another inventory count.

Lots are shared across rows with the same card/custom SKU, condition and printing,
including different bins, languages and archived rows. **Qty** in the lot table is
the original lot quantity; **Remaining** is today's balance. A merge transfers
quantity between inventory rows, with signed history on both rows, without creating
new purchased units. Filtered valuation uses the same allocation as the full list.
Costs retain four decimal places until report totals are rounded for display.

## Available corrections

- **Excess cost units:** review remaining units by lot, or correct stock using a
  confirmed physical count. The suggestion removes $0 lots first; it is only a
  proposal, not evidence that those lots are incorrect.
- **Wrong printing or condition:** move excess cost units to the same card's
  missing variant. Acquisition dates, cost and original purchase links carry over.
- **Missing stock costs:** record the missing cost breakdown without adding stock.
  Choose verified, estimated or unknown cost and document the evidence or method.
- **Missing sale costs:** allocate an existing excess lot or reconstruct omitted
  historical intake. A documented missing purchase can affect purchase totals;
  an opening-balance correction does not. Unknown costs remain flagged, including
  when those units have already sold.
- **Conflicting sale COGS:** use recorded lot allocations, or review the lot cost
  itself. Past allocations and original purchase amounts require explicit choices.
  Transferred child lots retain their own costs and can be reviewed separately.
- **Legacy bulk transfers:** review proposed original purchase links. Matching
  dates and costs suggest a link but do not prove it. Lots from separate purchases
  can instead be verified as original purchases. Transferred lots stop counting
  as additional purchases once their ancestry is recorded.
- **Archived stock:** restore it or dispose of its units and remaining cost as a
  non-sale movement. Emptying trash requires zero stock and retains purchase lots.
- **Unclassified outflows:** select the affected records and document whether they
  were adjustments, supplier returns or sales missing their order record.
- **Missing opening history:** record an explained journal correction after
  confirming physical quantity. Stock and purchase costs are unchanged.

Use **Review a lot cost** for a cost correction even when no discrepancy is flagged.
A verified free unit is different from an unknown cost recorded numerically as $0.
Estimated and unknown costs continue to appear as review notes.

## Preview, apply and evidence

Preview shows global stock units (including archived stock), remaining lot units,
remaining cost, recorded sale COGS, purchase totals and individual record changes.
It executes the proposed correction in a transaction and rolls it back. Apply checks
that the records still match the preview and saves the repair atomically. A stale
preview must be regenerated. Retrying the same Apply request does not apply it twice.

Every applied repair stores its explanation and before/after evidence. Expand
**Saved repairs** to inspect recent repairs or download the complete JSON record.
Keep database backups and supporting invoices alongside these records. Corrections
are not automatically applied to historical data when the app starts.

## Preventing new discrepancies

Manual adjustments, bulk quantity edits and cycle counts update quantity and cost
lots together. A decrease consumes remaining costs as a non-sale adjustment. An
increase without a supplied cost is explicitly unknown. Existing mismatches block
these operations and point to reconciliation; stale physical counts also require
review. Supplier returns retain both cost movements and recovered purchase amounts.

New CSV imports record their exact affected rows, lots and allocations. Undo restores
those effects or refuses when later activity makes reversal unsafe. It refuses to
undo staging rows already approved or removed, and legacy imports without an exact
reversal record. Invalid or fractional quantities are rejected. An insufficient
matching deduction is rejected instead of partly applied.

Order reversals restore the quantity actually deducted, rather than the requested
quantity of an oversold order. New sales retain exact order-line allocation links.
Transfers and remaining-cost edits preserve original purchase ancestry and spending.
Costs shared by multiple stocked bins/languages must be reviewed by lot instead of
silently applying a row cost edit to a different row's allocation.

## What the checks prove

The checks compare signed inventory history, remaining stock and cost lots, original
lot bounds, transferred/consumed units, order COGS and sale allocations. Balanced
records establish internal consistency; they do not verify invoices or physical stock.
Some orphaned or ambiguous legacy relationships require investigation rather than a
simple balance correction. Migrated Airtable sales retain their imported historical
COGS and are reported separately; they are not replayed through today's stock.

## Command-line checks

From the backend directory:

```powershell
python audit_inventory.py --output ../audit-output/inventory.json
python diagnose_reconciliation.py --output ../audit-output/workflows.json
python -m pytest -q
```

The audit opens the source SQLite database read-only, copies a consistent snapshot
to memory and adds new bookkeeping columns only to that copy. It never migrates the
source. Use `--database PATH` to audit a backup. The diagnostic script exercises stock
and cost workflows in disposable databases and exits with status 1 on any failure.
The regression suite also checks previews, stale/repeated applies, historical repair
paths, additive migration and HTTP transactions. Audit output and backups stay local
and are excluded from Git.
