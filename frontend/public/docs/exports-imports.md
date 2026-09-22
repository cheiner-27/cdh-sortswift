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

- **TCGplayer Id** must be the SKU ID learned from an approved CSV in Cycle
  Counts. Catalog sync supplies a product ID, which cannot be used here.
  Rows without a learned, unchanged SKU identity or price are excluded; the
  download message reports the skipped count. Complete their matches first.
- **TCG Marketplace Price** carries the applied TCGplayer target price. This
  preserves a deliberate ignore-overrides reprice while leaving the override
  itself stored for future decisions.
- Condition includes the finish (for example **Near Mint Foil**), and learned
  rows retain TCGplayer's exact set and product names.
- **Add to Quantity** is set to **0** — i.e. re-price only, don't add stock.
  **Total Quantity** reflects your on-hand count.
- The market/low reference columns are filled from your local price data when
  available (informational).

Workflow: Cycle Counts → upload current CSV → review each quantity variance →
approve → download and upload quantity corrections once → verify with a fresh
TCG export. Then Pricing → simulate → reprice → export pricing CSV → upload to
TCGplayer. Quantity corrections and price updates are separate files; see
**Help → Cycle counts** for the full round trip.

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

The **CSV Import** page brings new stock *in* using mapped columns.
For comparing current TCGplayer listings against existing inventory, use
**Cycle Counts → Upload current TCGplayer CSV** instead. Imports land in **Staging** for review, support add /
overwrite / deduction modes, and have a time-limited one-click undo (window set
in Settings).
