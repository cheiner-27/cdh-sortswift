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

The buyer's **name is shown while you review** — so you can hold the screen
against the physical slip — but it is not carried onto the order and isn't
retained once the batch is discarded. The destination city, state and ZIP are
kept, because the fee estimate and any shipping label need them.

A slip whose trailing text runs onto a second page is still one order — pages are
grouped by order number, not counted.

## Matching cards to your inventory

Matching runs against **your inventory, never the catalog**. That's the point of
the step: a packing slip is a list of cards you're about to pull off a shelf, so
a hit against a card you don't own tells you nothing, and a miss is the useful
signal — it means the stock was never entered, or was entered wrong.

The key is the **collector number plus the card name**, with the name compared
loosely rather than as an exact string — either a shared prefix, or one name
found inside the other. The second form is what a crossover card needs:
TCGplayer labels those "{new character} - {original card} (Treatment)" — e.g.
"Suki of the Kyoshi Warriors - Captain Sisay (Borderless)" — which shares no
prefix at all with the catalog's plain "Captain Sisay", but does contain it.
Finish and condition must agree exactly: TCGplayer prints the finish inside the
condition ("Near Mint Foil"), and a non-foil record will never satisfy a foil
sale, because it isn't the same physical card.

Set name is deliberately **not** part of the key. TCGplayer names sets
differently from the catalog often enough to be useless for matching — it lists
"Commander: Innistrad: Crimson Vow" where the catalog says "Crimson Vow
Commander", and a card on The List under its List heading rather than the set it
was originally printed in. Matching on set name is what caused most misses
before. Scoping to inventory keeps a collector-number collision to a
manageable few rows instead of the full catalog, but low numbers ("1", "13")
are genuinely common across many different cards even within one collection —
the name check is what tells them apart, not a formality. When none of the
colliding rows agrees by name, that means the right card isn't among them, so
the line is reported as unmatched rather than pointed at a stranger's card.

**Pre-numbering-era sets** — Alpha/Beta/Unlimited, Arabian Nights, Antiquities,
Legends, The Dark, Fallen Empires, and the rest of that early run — predate
Magic's on-card numbering scheme, so TCGplayer's slip prints no collector number
at all for that line. Those lines fall back to matching on the card name alone,
still scoped to your inventory the same way the numbered path is, since there's
nothing else on the slip to key on.

A line ends up in one of four states:

- **matched** — exactly one in-stock record fits. Nothing to do.
- **ambiguous** — several in-stock records fit, usually the same card in more
  than one bin. Nothing is guessed, because picking wrong sends you to the wrong
  shelf mid-pick.
- **out of stock** — the right record exists but holds zero. This is worth its
  own state: it means you sold something your inventory says you don't have, so
  the record is what needs fixing, not the slip.
- **unmatched** — nothing in inventory looks like this. If you hold the same card
  in a different grade or finish, the note says so and offers it — selling NM
  while holding only LP usually means the card was graded wrong on the way in.

An order commits only when every line is resolved. **One awkward card blocks its
own order and nothing else** — the rest of the batch still commits, so a single
oddity never holds up the day's shipping.

For anything not matched automatically:

- **Find…** — pick from the records it already found, or search your inventory by
  name. Untick *in stock only* to reach a record sitting at zero.
- **Skip** — commit the line without linking inventory. The revenue stays on the
  order (it's what the buyer paid, and what the fee is charged on) but no stock is
  consumed and no cost of goods is booked. Use it for something that was never in
  tracked stock.
- **Re-match** — after you add the missing card to inventory, re-run matching on
  the whole order instead of resolving line by line.

## Buyer shipping paid

Slips don't print what the buyer paid for shipping, so it's pre-filled: the flat
rate on orders under the free-shipping threshold, zero above it (both in
Settings). It's revenue and part of the commission base, so it shouldn't sit at
zero by default — but it's a guess about *your* rates, not something read off the
slip, so it stays editable per order.

## Shipping cost (postage)

This is the other side of shipping: what *you* paid for postage, not what the
buyer paid you. Slips never print it, so it starts at zero — type it in here as
you buy the label, and it's the last cost on this screen: it comes off **net
before COGS** alongside the fee, and carries onto the order on commit the same
way the fee estimate does. Recording it here means every cost on the order is in
one place instead of split between this screen and a follow-up edit on
**Costs** after commit.

## The anticipated fee

Each order shows the fee TCGplayer is expected to take, before the payout
confirms it:

- **commission** on the items plus any shipping the buyer paid
- **a flat charge plus a processing percentage** on the tax-inclusive total

Each percentage is rounded to the **nearest** cent (same as your payout) and then
added. The rates live in Settings, so a change in your seller level is a
setting, not a code change.

The **Fee** box is the estimate, and you can type straight over it when you know
better. Doing so pins your figure and stops it being recalculated; clearing the
box hands it back to the estimate.

### Why tax is behind an estimate

Packing slips don't print the sales tax, but the processing percentage is charged
on the tax-inclusive total. So the tax is estimated from the buyer's state. You
never enter it — if the fee is wrong, correct the fee directly.

That is close enough to be safe, and it is worth knowing why: tax reaches the fee
only through that one percentage, so the whole plausible tax range moves the fee
by a few cents on a normal order. Being off by a full percentage point on the rate
costs well under a cent on a $35 order. State-level rates are therefore accurate
to about a cent, and an address-level tax service would buy fractions of one.

The note under the fee shows the rate used and the state it came from.

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
