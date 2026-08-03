# Orders

Orders are sales to fulfill. They arrive three ways:

- **eBay** — via polling (or "Sync orders now" on Marketplaces).
- **TCGplayer** — by uploading the packing-slip PDF on **Order Intake**, which
  reads each sheet into an order and estimates the fee. See *Order intake*.
- **Manual** — an off-platform / in-person sale you record yourself.

> The TCGplayer **Deduction CSV** import is a different job: it adjusts stock for
> cards sold on *another* channel. It does not create orders, so it books no
> revenue, cost of goods or fees — use Order Intake for TCGplayer sales.

## Manual / offline sale

Use **+ Manual sale** for a sale that didn't come through a marketplace. Two
fields to understand:

- **Buyer name** — this is the **customer**, not a platform. It's just a label
  for your own reference (defaults to "walk-in" if left blank). The *platform* of
  a manual sale is always the "manual" channel — that's what distinguishes it
  from eBay/TCGplayer orders.
- **Items** — search your **live inventory** by card name (or comment) and click
  **Add**; no need to know internal IDs. The unit price pre-fills from the card's
  current price and each line shows how many you have on hand. Recording the sale
  deducts the cards (FIFO cost of goods booked) and logs it.

Every order also has a **Costs** button to record what you paid for shipping and
marketplace/processing fees after the fact (e.g. postage bought outside the app),
feeding straight into Reports → P&L.

## Fulfillment

- **Pick list** — select orders and print a merged, bin-sorted pick list.
- **Packing slip** — per-order slip.
- **Label** — buys a Shippo label (only for orders above your Settings threshold;
  below it you handle postage yourself). You can also just type a tracking number.
- **Ship** — marks shipped locally and on the originating marketplace, and
  deducts inventory if it wasn't already.
- **Cancel** — pre-shipment cancellation; restores the inventory.

## Refunds

The **Refund…** button on a shipped order handles the full range:

- **Partial refund** — refund part of the total to the buyer. Reduces net revenue
  and profit, and credits back a pro-rata slice of the selling fees; inventory and
  COGS are untouched. The order shows as *partially refunded* and can be refunded
  again up to the refundable total.
- **Full refund** — refunds the **items plus the shipping the buyer paid you**
  (both are revenue, so both come back out), with an **"item returned to me?"**
  choice:
  - **returned** → the card's COGS is backed out of this sale, so the cost follows
    the card and lands on whichever sale actually sticks. The refunded sale is left
    showing only what you really ate: outbound shipping plus any **return
    shipping** you paid (capture it in the dialog). If the line is linked to an
    inventory record the card is also restocked (+qty) automatically.
  - **not returned** → a write-off: the card does not come back, inventory stays
    deducted, and its cost remains a real loss.

**Selling fees credited back.** TCGplayer returns the selling fees on a refund, so
the dialog pre-fills the full fee and P&L only counts the fees you actually ate.
Set it to `0` (or any partial amount) for a marketplace that keeps them.

**Why the refunded sale shouldn't carry the COGS.** If the cost stayed expensed on
a refunded sale, the card sitting back in your inventory would have no cost basis:
inventory-at-cost would understate, the aging report would value it at $0, and the
eventual resale would show a fake 100% margin. Backing it out means a
sell → refund → resell cycle expenses the card's cost exactly once.

**Migrated sales have no inventory link.** Historical orders imported from Airtable
carry their COGS on the order line with no live inventory record behind them. A
refund still backs the COGS out correctly, but it **cannot auto-restock** the card —
the dialog warns you, and you re-add that stock on the Inventory page before
re-listing it.

### Supplier refunds (a refund to *you* on a purchase)

These live on **Inventory → Detail → Supplier refund / return**, not on orders:

- **Partial** — you keep the goods but got money back; the refund lowers your cost
  basis **FIFO** (oldest cost first), so near-term COGS drops.
- **Full** — you return units to the supplier: they're removed from inventory and
  their cost recovered (no P&L hit).

All refunds are logged, and Reports → P&L nets everything out:
`profit = revenue − refunds − COGS − shipping (incl. returns) − fees kept`,
where *fees kept* is what you were charged minus what was credited back.
