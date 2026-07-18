# Overview

cdh-sortswift is your personal TCG inventory, pricing, and selling tool. It is
single-user — no accounts, no billing. Everything runs locally against a SQLite
database.

## The end-to-end flow

1. **Catalog** — sync the card catalog and market prices for each game so the
   app can recognize cards and price them. See *Catalog & price sync*.
2. **Scan** (or **Add cards** / **CSV Import**) — get cards into the system.
   Scanning recognizes images from a folder; you confirm each match.
3. **Staging** — a review buffer. Confirmed scans, CSV imports, and manual adds
   land here so you can eyeball them before they hit live inventory.
4. **Inventory** — the source of truth: one record per card + condition +
   printing + language + bin. Adjust, split, merge, transfer, set overrides.
5. **Pricing** — build per-game rules, simulate, and reprice.
6. **Marketplaces / Lots / Orders** — push listings, build bulk lots, and
   fulfill sales.

## Key ideas

- **FIFO costing.** Cost basis and inventory age come from acquisition batches,
  consumed oldest-first when you sell.
- **Everything is logged.** Every quantity change is an audit-log entry.
- **Nothing auto-commits the risky stuff.** Staging, repricing, and cycle-count
  adjustments all require an explicit approve/apply step.

See the **Glossary** for the meaning of terms like *printing*, *tier*, *reserve*,
*phash distance*, and *large-move flag*.
