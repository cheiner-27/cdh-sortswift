# Orders

Orders are sales to fulfill. They arrive three ways:

- **eBay** — via polling (or "Sync orders now" on Marketplaces).
- **TCGplayer** — via importing a Deduction CSV.
- **Manual** — an off-platform / in-person sale you record yourself.

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
  and profit; inventory is untouched. The order shows as *partially refunded* and
  can be refunded again up to the total.
- **Full refund** — refund the whole total, with a **"item returned to me?"**
  choice:
  - **returned** → the card is restocked (+qty) and its COGS reversed, so the
    sale nets to ~0 minus any **return shipping** you paid (capture it in the
    dialog).
  - **not returned** → a write-off: the card does not come back, inventory stays
    deducted, and its cost remains a real loss.

### Supplier refunds (a refund to *you* on a purchase)

These live on **Inventory → Detail → Supplier refund / return**, not on orders:

- **Partial** — you keep the goods but got money back; the refund lowers your cost
  basis **FIFO** (oldest cost first), so near-term COGS drops.
- **Full** — you return units to the supplier: they're removed from inventory and
  their cost recovered (no P&L hit).

All refunds are logged, and Reports → P&L nets everything out:
`profit = revenue − refunds − COGS − shipping (incl. returns) − fees`.
