# Bulk lots

Lots let you bundle many cards into a single sellable unit (e.g. a "500 assorted
commons/uncommons" lot). You build **templates** (the recipe), then **generate**
actual lots from current stock.

## A lot template answers: which cards, how many, priced how?

- **Which cards** — the filter groups. Check any **Games**, **Rarities**, and
  **Conditions** you want to allow (leaving a group unchecked means "any"),
  optionally restrict to specific **set codes** (comma-separated, e.g.
  `MH3, OP01`), and optionally a card-**value range**. A card must match *all*
  the criteria you set to be eligible.
- **Lot size** — how many cards go in the lot.
- **Max copies per card** — cap duplicates so a lot isn't 100 copies of one card.
- **Pricing** — either *total value × margin %* (e.g. 80% of the summed card
  value) or a *fixed price*.

## Generating

**Generate lot** pulls eligible, in-stock, **un-reserved** cards (highest value
first) up to the lot size and reserves them. Regenerating later builds a fresh
lot from whatever remaining stock still qualifies.

## Reservation

Cards in an **open** or **listed** lot are *reserved* — they're held back from
marketplace push quantities so you don't oversell a card that's already committed
to a lot. Quantities aren't actually deducted until the lot **sells**; the
reservation is released if you **dissolve** the lot.

- **Mark listed** — you've posted the lot somewhere.
- **Sold** — deducts the reserved units and books FIFO cost of goods.
- **Dissolve** — cancels the lot and frees the cards back to normal stock.
