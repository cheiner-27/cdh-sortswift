# Inventory

The source of truth. One record per **card + condition + printing + language +
bin**, with an aggregate quantity — there's no per-unit tracking. Same card in
two bins = two records.

## The totals strip

Under the filters sit four numbers covering **everything the current filter
matches** — not just the rows visible in the table — and they re-compute on every
search:

- **Units · records** — cards on hand, and how many records hold them. A bulk
  pile is one record holding thousands of cards, so these diverge a lot.
- **Purchase price** — what those units cost you: the remaining FIFO cost basis.
  Units with no recorded cost (migrated stock) count as $0.
- **Market price** — TCGplayer market value × quantity. A reference sticker
  number: it ignores condition, age, and your pricing rules.
- **Listed price** — what you're asking: price override where set, otherwise the
  auto price, × quantity.

So filter to a set, a bin, or a price band and the strip values just that slice.
Purchase vs Market is your unrealized margin; Market vs Listed shows how far your
rules have moved you off market.

> Deleted and zero-quantity rows are excluded by default, so the totals describe
> sellable stock. Tick **show deleted** to include soft-deleted records.

## Filtering

Search text matches card name *and* comment. Beyond that: game, set, condition,
printing, bin, marketplace + listing status, and four ranges:

- **Price ≥ / ≤** — price override, else the auto price.
- **Cost ≥ / ≤** — FIFO unit cost: what you paid for the oldest unit on hand.
- **Age ≥ / ≤ days** — days since that oldest unit was acquired.

Cost and age come from the acquisition batches, and the batch pool is keyed on
card + condition + printing — **not** bin. Two bins of the same NM normal card
therefore show the same cost and age.

A row with no acquisition history has no age at all, so it matches **neither**
age bound. Its cost reads as $0, so it does match `Cost ≤`.

Press **Search** (or Enter in the search box) to apply. Nothing filters as you
type.

## Acting on a selection

Tick rows, then:

- **Bulk edit** — price, override, floor, cost (flat or % of price), quantity,
  comment, bin, condition, printing, listing caps. Preview before applying.
- **Adjust stock** — corrections, shrinkage, found stock. Set an exact quantity
  or a +/− delta, with an optional damaged flag. Deductions clamp at 0.
- **Transfer bin** — move records to a new bin; logged to transfer history.
- **Delete** — soft delete, restorable via **show deleted**.

**Merge duplicates** collapses rows identical in every field (including bin,
cost, comment, and listing links) into one. It's irreversible.

## One record at a time

The **Price** box on each row is the price *override* — a fixed price that
bypasses autopricing. Blank it to hand the card back to your pricing rules; the
muted line underneath always shows the auto price for comparison.

**Detail** opens the full record: acquisition lots (each purchase batch with its
own date and cost, oldest consumed first), supplier refunds, per-marketplace
listing controls, and the complete audit history. **Split record** peels units
off into a new record with a different condition, printing, or language.
