# Cycle counts

Cycle Counts supports physical bin audits and TCGplayer CSV reconciliation.
Each count records its source. Uploading a CSV does not mark a physical bin as
verified. Progress saves so you can stop and resume.

## Physical bin counts

1. Pick a bin and start a count to snapshot expected quantities.
2. Count physically and enter each actual quantity.
3. Review and approve to write discrepancies as logged inventory adjustments.

Green means a match, yellow a discrepancy, red uncounted. Export expected
produces a CSV for offline counting. Completed counts cannot be edited or
approved twice.

## TCGplayer reconciliation — part one

1. Export current live listings from TCGplayer as a Pricing Custom Export.
2. Upload it on Cycle Counts. Nothing changes inventory or pricing yet.
3. Review each variance. **Update local** accepts TCG's quantity;
   **Correct TCG** keeps local quantity and prepares a correction for TCGplayer.
   **Skip** leaves that row out of all approval effects.
4. Resolve ambiguous or unmatched identities with **Find / change**. Search
   includes zero-stock inventory. If the item is missing, add it through normal
   intake, then refresh matches. A row represents one selected inventory record;
   review quantities carefully when the same card is stored across bins.
5. Approve after every variance has a decision. Approval learns TCG SKU IDs,
   adopts current listed prices, and applies only the local adjustments you
   selected. Each adjustment is logged with cause `tcg_reconcile`; FIFO stock
   is updated for both additions and deductions. Stored price overrides remain.
6. Download quantity corrections from the completed count. Upload that file
   once in TCGplayer, then mark it uploaded here.

The correction file uses signed **Add to Quantity** deltas and keeps the source
CSV's listed prices. Uploading the same file again repeats those deltas; marking
it uploaded prevents another download from that count. A correction becomes
unavailable if local stock, its identity or its target price has changed, or a
new reconciliation supersedes it. Use a fresh export in that case.

## Pricing — part two

After uploading corrections, export fresh current listings from TCGplayer.
Reconcile that fresh CSV to verify quantities and adopt current prices. Then go
to Pricing, simulate, apply the chosen prices and download the pricing CSV.

The pricing file has **Add to Quantity = 0**. It never repeats the quantity
corrections. The repricing movement guard uses the current platform price
adopted from the reviewed CSV.

## Matching and review details

TCGplayer Id is a SKU identity for a particular condition and printing; it is
not the catalog product ID. Matching first uses an already learned SKU. For new
identities, it looks up the exact normalized **TCGCSV product name and set**, plus
the collector number when supplied, then finds inventory by that product ID.
For local cards missing a product ID, a verified TCGCSV set code, complete collector
number, and agreeing name can identify the record. This never replaces a known
conflicting product ID or changes the primary catalog.
Blank numbers are supported for vintage sets: a unique name/set match is enough.
Multiple products with the same name/set require review. Numbers for double-sided
tokens retain both sides.

TCGCSV set/product names are cached locally for 24 hours. The first upload across
many sets can take a few minutes; later uploads reuse the cache. Failed downloads
are shown as catalog notices and retain cached data. If no TCGCSV catalog data is
available for a set, the primary catalog name/number matcher is the fallback.
Known product IDs never fall back to a different product simply because its
name/number is close. The review summary separates missing local product links,
condition/finish/language differences, ambiguous records, and quantity variances.

Condition and printing must agree with English-language inventory; TCG's finish
is read from Condition, such as "Lightly Played Foil." TCG's own set and product
names are retained for export. Identifying a catalog product does not prove that
its particular condition/finish exists in local stock.

A SKU cannot be silently reassigned to another inventory record, and two CSV
rows cannot both claim the same record. Fix the identity or skip the conflicting
row. Changes to stock, identity or pricing during review block approval until
you refresh and review again. Refresh keeps manual matches and skipped rows.
Quantity decisions remain when the linked inventory snapshot is unchanged; changed
stock or pricing requires review again. If a manually selected record changes
identity or is deleted, choose it again. Approval saves the reviewed SKU-to-inventory
mapping for future uploads, so a corrected name mismatch does not recur.

Zero-stock dead listings are collapsed by default. Sealed/custom listings
(C-prefixed IDs or Unopened) are shown separately and excluded from singles
reconciliation. Unknown conditions or product lines require a deliberate skip.

**Local stock not linked in this CSV** is a report only. Missing CSV rows never
zero local inventory. They may be unlisted on TCGplayer or awaiting a manual
match. Price downloads exclude rows without a learned SKU or usable price and
report how many were skipped.

## TCGplayer file reference

The signed quantity-delta and required-price behavior follows
[TCGplayer's CSV import/export documentation](https://help.tcgplayer.com/hc/en-us/articles/115002358027-Importing-and-Exporting-CSVs-to-Mass-Update-Prices-and-Quantities).
