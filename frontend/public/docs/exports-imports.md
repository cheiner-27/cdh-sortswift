# Exports & imports

There is **no TCGplayer API** available to this app, so moving data to/from
TCGplayer is done with CSV export/import. eBay uses its API where possible, but
the export layouts help with bulk tools too.

## TCGplayer export (the important one)

On the **Export** page choose layout **TCGplayer**. It emits the exact columns of
a TCGplayer *"Pricing Custom Export"*:

```
TCGplayer Id, Product Line, Set Name, Product Name, Title, Number, Rarity,
Condition, TCG Market Price, TCG Direct Low, TCG Low Price With Shipping,
TCG Low Price, Total Quantity, Add to Quantity, TCG Marketplace Price, Photo URL
```

This matches a real export from your TCGplayer account, so you can **re-upload it
in Seller Hub** to push prices and quantities. Notes:

- Rows must carry a **TCGplayer Id** to match a product — sync your catalog first
  so each card has one (older Pokémon data lacked it; the current TCGcsv-based
  sync stores it).
- **TCG Marketplace Price** carries the price you're setting (your current price
  or per-item override).
- **Add to Quantity** is set to **0** — i.e. re-price only, don't add stock.
  **Total Quantity** reflects your on-hand count.
- The market/low reference columns are filled from your local price data when
  available (informational).

Workflow: reprice in the app → export TCGplayer layout → upload to TCGplayer.

## eBay export

The **eBay** layout is a simple SKU / title / condition-id / quantity / price
sheet for bulk tools. Day-to-day eBay listing/repricing goes through the
Marketplaces page (eBay API) rather than CSV.

## Native export (column picker)

The **native** layout lets you pick and order any columns — for your own
spreadsheets, backups, or ad-hoc analysis. Save column sets as templates.

## Out-of-stock export

Previously-stocked, now-zero-quantity items — your restock shortlist.

## CSV import

The **CSV Import** page brings cards *in* (e.g. a TCGplayer export of your
current listings). Imports land in **Staging** for review, support add /
overwrite / deduction modes, and have a time-limited one-click undo (window set
in Settings).
