# Bulk

**Bulk piles** are cards you buy and sell *by the count* without inventorying
each one — commons, lands, energy, "500 assorted." A pile tracks a running card
count and its cost; it never knows *which* cards are in it.

This is different from **Bulk lots** (the [lot builder](/docs/lots.md)), which
bundles specific, individually-tracked cards into a sellable listing.

## The three things you do with a pile

### 1. Buy into it
On the **Bulk** page, create a pile (name + game), then **Buy**: enter how many
cards you got and what you paid (a total, or a per-card cost). Each purchase is
recorded as a **FIFO cost batch** — so if you buy 500 @ $0.05 and later 1,000 @
$0.06, the first 500 cards that leave the pile cost $0.05 each and the next 1,000
cost $0.06. You never have to average anything.

Set the **Acquired date** to the day you actually bought the bulk. Leave it blank
and it defaults to today, which is rarely what you mean for a pile you've had for
months. It matters beyond bookkeeping: every card you later pull out of the pile
inherits this date as its acquisition date, so it drives the card's inventory age.

The pile's **Purchased** column shows that date. On a pile with one buy you can
**edit** it there to correct a date entered wrong (or defaulted to today). A pile
with several buys shows the oldest and a `+n more` note — each buy carries its own
date, so there's no single date to edit; fix the buy that was wrong.

### 2. Pull the good cards out (during scanning)
When you sift a pile for hits, go to the **Scan** page and set **Pull from bulk
lot** to that pile before pulling scans (or set it per-card / in bulk from the
review queue). Every card you then confirm is **pulled out of the pile**: the
pile's count drops by one and the card graduates into normal tracked inventory,
carrying its share of the pile's per-card cost and the pile's acquisition date.
No fresh cost is invented and nothing is double-counted — the money was already
spent when you bought the bulk.

Only piles with cards on hand are offered as a source — an empty pile has nothing
to pull out of. The count and purchase date next to each pile's name in the picker
are what a card pulled from it will carry.

Staging rows headed for a pile show a **⟵ from &lt;pile&gt;** badge, their cost
column reads *(from bulk)*, and their **Acquired** column shows the pile's
purchase date rather than an empty box — both come from the pile, not the row.

If a pile can't be pulled from when you approve (it emptied out in the meantime,
or the pile was deleted), those rows **stay in staging** and Staging tells you
why. They're never consumed by an approve that moved nothing.

### 3. Sell it in chunks
**Sell** from a pile records a manual/offline sale by card count + price (e.g.
100 cards for $5). Pick the **platform** it sold on (eBay / TCGplayer / Whatnot /
your own) just like a regular manual sale. COGS is booked **FIFO** (oldest cards
first) and the sale lands in **Orders** and the **P&L** report like any other
sale — so you delete, cancel (restocks the pile), or refund it from the Orders
page.

## What a pile is worth

A pile has no catalog card, so there's no TCGplayer price to look up — left
alone it would value at $0, which for a sift-and-sort workflow means most of the
cards you own read as worthless. Instead a pile is valued the way bulk is
actually priced: a going rate per card for each broad grade, times roughly how
much of the pile is that grade.

Two things feed it:

- **Settings → Bulk rates** — what one card of each grade is worth, per game.
  Magic splits into rare/mythic, common/uncommon, and basic land; Pokémon into
  ultra rare, holo, common/uncommon, and basic energy; Yu-Gi-Oh and One Piece
  have their own. Set these once and they apply to every pile of that game, so
  when the bulk market moves you edit one number, not fifty piles.
- **The mix on each pile** — click **Set mix** (or **mix**) in the Est. value
  column and give the rough percentage for each grade. Eyeball it; this is an
  estimate, not a contents manifest.

A pile that's 5% rares at $0.03, 90% commons at $0.005 and 5% land at $0.002 is
worth $0.0061 a card, so 10,000 cards is $61. That number shows in the pile's
**Est. value** column and in the **market** total on the Inventory page.

Percentages don't have to reach 100 — whatever you leave out counts as $0, which
is usually what you mean when you only bother to account for the good stuff.
They can't add up to *over* 100. Until you set a mix the column reads **Set mix**
rather than $0.00, so "worth nothing" and "nobody has said yet" stay distinct.

This is purely for valuation. It never changes what a pile sells for, never
touches FIFO cost, and never affects a listing.

## Notes

- Quantity is always **individual cards**, so both "sold a pack of 100" and
  "pulled one hit" are just deductions of 100 and 1.
- Bulk piles are **in-store only** — they're capped out of eBay/TCGplayer pushes
  so a giant pile never accidentally lists as a single item.
- A pile is stored as a custom product under the hood, so it flows through the
  same FIFO cost, audit log, and reporting as everything else.
- **Delete** a pile from its row. A pile you never stocked is removed outright;
  one with stock or sales history is hidden (its cards drop out of inventory and
  valuation) while its FIFO batches and past sales stay intact.
