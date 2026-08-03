# Order Intake

Order Intake turns the TCGplayer packing-slip PDF into orders. You upload one
file, review what it read, and commit — each sheet in the PDF is one order.

This is the review buffer for sales, the way Staging is the review buffer for
stock: **nothing goes live on upload**. Parsed orders sit in a batch until you
commit them.

## What it reads off the slip

TCGplayer's packing slips are real text PDFs, not scans, so every field is
decoded exactly — there is no OCR anywhere in this path and no guessing about a
price or a quantity. From each sheet it pulls:

- the **order number**, which becomes the order's external id
- the **order date**
- the buyer's **name, city and state**
- every line's **quantity, card, collector number, condition, finish and price**
- the printed **subtotal and item count**

Those last two are the reason you can trust the rest. The parsed lines are summed
and checked against the totals TCGplayer printed on the slip; if they disagree,
the order is held back and flagged rather than committed on a bad read.

A slip whose trailing text runs onto a second page is still one order — pages are
grouped by order number, not counted.

## Matching cards to your inventory

Each line is matched on set name, collector number, condition and finish. Foil is
part of that: TCGplayer prints the finish inside the condition ("Near Mint Foil"),
and a non-foil record will never satisfy a foil sale, because it isn't the same
physical card.

A line ends up in one of three states:

- **matched** — exactly one in-stock record fits. Nothing to do.
- **ambiguous** — several records fit, usually the same card sitting in more than
  one bin. Nothing is guessed here, because picking wrong sends you to the wrong
  shelf while you're picking the order.
- **unmatched** — no catalog card, or none of your stock is in that condition and
  finish.

An order commits only when every line is resolved. **One awkward card blocks its
own order and nothing else** — the rest of the batch still commits, so a single
oddity never holds up the day's shipping.

For anything not matched automatically you have three options:

- **Find…** — pick from the suggested records, or search the catalog. Choosing a
  catalog card re-runs the inventory match against it.
- **Skip** — commit the line without linking inventory. The revenue stays on the
  order (it's what the buyer paid, and what the fee is charged on) but no stock is
  consumed and no cost of goods is booked. Use it for something that was never in
  tracked stock.
- **Re-match** — after you add the missing card to inventory, re-run matching on
  the whole order instead of resolving line by line.

## The anticipated fee

Each order shows the fee TCGplayer is expected to take, before the payout
confirms it:

- **commission** on the items plus any shipping the buyer paid
- **a flat charge plus a processing percentage** on the tax-inclusive total

Each percentage is rounded **up** to the cent and then added, which is how the
fee is actually billed — computing it from unrounded products lands a hair low.
The rates live in Settings, so a change in your seller level is a setting, not a
code change.

### Why tax is an estimate

Packing slips don't print the sales tax, but the processing percentage is charged
on the tax-inclusive total. So the tax is estimated from the buyer's state.

That is close enough to be safe, and it is worth knowing why: tax reaches the fee
only through that one percentage, so the whole plausible tax range moves the fee
by a few cents on a normal order. Being off by a full percentage point on the rate
costs well under a cent on a $35 order. State-level rates are therefore accurate
to about a cent, and an address-level tax service would buy fractions of one.

The fee is marked with an asterisk while the tax is estimated, and the note under
it shows the rate used. If you know the real figure, type it into **Tax** and the
fee becomes exact; clear the field to go back to the estimate.

None of this is tax you owe. On these marketplaces the platform collects and
remits it — the number exists here only to get the fee right.

The estimate is written to the order's marketplace fees on commit, so it flows
into Reports immediately. When the payout lands you can replace it with the real
figure using **Costs** on the order — see *Orders & refunds*.

## Committing

**Commit** creates the order as **open**, and deliberately does *not* deduct
inventory. That happens at **Ship**, which is also where the marketplace is told
and other channels are de-listed. Committing open is what lets the order flow
into the normal pick-list workflow, and it means a cancellation before shipping
needs no stock reversal.

From there the order behaves like any other — see *Orders & refunds*.

Re-uploading the same PDF is safe. Orders already recorded come back marked
**duplicate** and are skipped rather than recorded twice, since the slip carries
TCGplayer's own order number. That means a partly-processed batch can be
re-uploaded without cleaning anything up first.

That check can only recognise sales recorded *through this page*, because it
matches on the order number. Sales that came in another way carry a different id —
everything brought over from Airtable is `airtable-SALE-…` — so an old slip for a
sale you already migrated would not be spotted as a repeat. When an order looks
like one of those (same channel, same day, same total) you get a **⚠ warning**
naming the existing order. It never blocks the commit, because same-day
same-total is a real coincidence as well as a real duplicate — it's there so you
can check rather than find out later in the P&L.

**Discard** throws away a review batch. Orders already committed from it are live
orders now and are left alone.
