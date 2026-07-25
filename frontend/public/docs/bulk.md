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

### 2. Pull the good cards out (during scanning)
When you sift a pile for hits, go to the **Scan** page and set **Pull from bulk
lot** to that pile before pulling scans (or set it per-card / in bulk from the
review queue). Every card you then confirm is **pulled out of the pile**: the
pile's count drops by one and the card graduates into normal tracked inventory,
carrying its share of the pile's per-card cost and the pile's acquisition date.
No fresh cost is invented and nothing is double-counted — the money was already
spent when you bought the bulk.

Staging rows headed for a pile show a **⟵ from &lt;pile&gt;** badge, and their cost
column reads *(from bulk)* because the cost comes from the pile, not the row.

### 3. Sell it in chunks
**Sell** from a pile records a manual/offline sale by card count + price (e.g.
100 cards for $5). Pick the **platform** it sold on (eBay / TCGplayer / Whatnot /
your own) just like a regular manual sale. COGS is booked **FIFO** (oldest cards
first) and the sale lands in **Orders** and the **P&L** report like any other
sale — so you delete, cancel (restocks the pile), or refund it from the Orders
page.

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
