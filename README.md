# cdh-sortswift

Personal TCG inventory, scanning, pricing, and marketplace management tool — a single-user recreation of SortSwift's core workflow. FastAPI + SQLite backend, React frontend, runs locally on Windows. See `REQUIREMENTS.md` for the full spec.

## Quick start

```powershell
# one-time setup
cd backend
pip install -r requirements.txt
cd ..\frontend
npm install
npm run build

# run (serves API + UI at http://127.0.0.1:8000)
cd ..
.\run.ps1

# or for frontend hot-reload during development (UI at http://localhost:5173)
.\run.ps1 -Dev
```

Run tests with `cd backend; python -m pytest tests`.

## First-time setup checklist

1. **Catalog** page → sync sets/cards for the games you sell (MTG and Pokémon sync per set; Yu-Gi-Oh! and One Piece sync the whole catalog). Then "Sync prices (TCGcsv)".
2. **Settings** page → set your default scan folder, Tesseract path (install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) for OCR recognition), Shippo token, ship-from address, and eBay merchant location key.
3. **Marketplaces** page → enter eBay credentials (client ID / secret / refresh token from your developer keyset) and create at least one listing rule referencing your Seller Hub business policy IDs. Use **dry run** mode to exercise the flow without touching eBay.
4. **CSV Import** page → migrate existing inventory (map your columns, remap legacy values like `holo` → `foil`, preview, run). Undo window is 15 minutes.
5. Optional: **Catalog** page → "Build reference phashes" per set to enable image-match fallback when OCR fails.

## Layout

```
backend/
  app/
    models.py            # full schema: catalog, scans, staging, inventory,
                         #   FIFO acquisition log, audit log, pricing, listings,
                         #   lots, orders, cycle counts
    domain.py            # canonical conditions/printings + normalization
    services/
      scanning.py        # folder pull, SHA-256 dedup, OCR + phash recognition
      staging.py         # pre-commit review layer
      inventory.py       # all quantity mutations, audit logging, FIFO costing
      importing.py       # CSV import: mapping, 3 modes, disambiguation, undo
      exporting.py       # column-picker exports, TCGplayer/eBay layouts
      pricing.py         # tiered rules: sources→modifiers→adjustments→guards
      lots.py            # template-based lot generation with reservations
      orders.py          # pick lists, packing slips, Shippo labels, refunds
      reports.py         # FIFO P&L, aging, location summary
      marketplaces/
        base.py          # adapter interface + listing-rule matching
        ebay.py          # Sell Inventory + Fulfillment APIs (dry-run capable)
        tcgplayer.py     # CSV fallback now, API-ready interface for later
        sync.py          # resync/push/clear/rebuild, order polling, cross-delist
    routers/             # REST API (one router per domain)
  tests/                 # FIFO, pricing, import, oversell, end-to-end flow
frontend/
  src/pages/             # one page per workflow (Scan, Staging, Inventory, …)
```

## Key behaviors (matching REQUIREMENTS.md)

- **Master quantity is authoritative** — marketplace quantities are derived: `master − other-marketplace reserves − open-lot reservations`, capped by per-marketplace listing caps (cap 0 = in-store only).
- **Scan images are never moved or deleted**; dedup is by SHA-256 in `processed_scans`. Re-pull a rejected image by clearing its hash.
- **Everything routes through staging by default** (scans, CSV adds, manual adds) with a skip-staging option for trusted intake.
- **Every inventory mutation is logged** (`inventory_log`) and deductions clamp at zero. Imports are undoable for 15 minutes. Order deductions are replay-safe (reprocessing reverses before reapplying).
- **FIFO costing from day one** — `acquisition_log` batches are consumed oldest-first at sale time; refunds/cancellations restore them.
- **Repricing is manual-trigger only**, always previewable, per-marketplace, with guard rails (cost floor, rarity floors, max-move cap, marketplace minimums raising the listed price only).
- **eBay** listings are Fixed Price/GTC, condition 4000 (Ungraded) with the grade in the Card Condition aspect, photo required (scan image or catalog fallback), Best Offer thresholds stored as percentages and floored at COGS.
- **TCGplayer** works via CSV fallback (listing export + cross-channel deduction export that never double-deducts TCGplayer-originated sales) until API access is granted; the adapter interface is ready to enable.
- **Printing** happens client-side via the browser/Windows print dialog (pick lists, packing slips, inventory labels).

## Notes / deviations

- Shippo is called via its REST API with `httpx` (equivalent to the SDK, fewer dependency risks on Python 3.14). Configure the token in Settings.
- OCR requires a local Tesseract install; without it, recognition falls back to perceptual-hash matching (after building reference phashes) and manual search.
- CardMarket is a reserved future slot (marketplace list and pricing sources are extensible; EUR→USD conversion hook exists in the pricing config design).
