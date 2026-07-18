# cdh-sortswift — Requirements Document

**Purpose:** Personal TCG inventory, scanning, pricing, and marketplace management tool. Single-user, runs locally on Windows 11. Not a SaaS product.

---

## Reference Software: SortSwift (sortswift.com)

This project is a personal recreation of SortSwift's core workflow for single-user use. SortSwift is a commercial multi-tenant SaaS platform; this build strips it down to the parts relevant to one seller and adds no commercial infrastructure. SortSwift's public help center (sortswift.com/docs) was systematically mined for feature depth beyond the marketing-page overview — this doc reflects that deeper pass, not just the top-level feature list.

**Features recreated from SortSwift:**
- Card scanning & recognition (OCR-based; SortSwift uses ML/AI)
- Chaos-style folder intake with deduplication
- Bin/location tracking for physical inventory
- Condition grading during intake (session default + per-card override)
- Staging/review queue for scan and CSV intake before anything hits live inventory
- Adjustment/audit logging and undo for imports, stock corrections, and order deductions
- Automated pricing engine with multi-tier rule configuration
- Per-marketplace independent pricing configs
- Price simulation before applying
- Marketplace listing creation and sync (eBay, TCGplayer), including cross-marketplace oversell prevention
- Custom/non-catalog item support (graded cards, sealed product, accessories)
- Bulk lot builder with template-based lot generation
- Order fulfillment: pick lists, packing slips, shipping labels
- Shippo shipping integration (SortSwift uses negotiated USPS rates; same concept)
- P&L reporting with FIFO costing, inventory age tracking

**Features intentionally not built (out of scope):**
- Super Sorter / Simple Sifter hardware integration
- Point of Sale system
- Buylist customer portal
- In-store kiosk
- Consignment management
- Mobile app
- Shopify, Square, ManaPool, CardTrader integrations

**Changes from SortSwift's approach:**
- Recognition uses OCR + perceptual hashing instead of ML — works well for controlled scanner input
- Single user, no auth, no billing, no multi-tenancy
- Manual reprice trigger only (no automated 12-hour schedule)
- Shippo for shipping rather than SortSwift's own USPS negotiated rate wallet
- CardMarket reserved as a future integration slot (not v1)
- Label printing scoped to 1-2 fixed layouts rather than SortSwift's drag-and-drop template builder (no hardware label printer in this build)

---

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python + FastAPI |
| Frontend | React |
| Database | SQLite |
| OS | Windows 11 |
| Image processing | Pillow, pytesseract (or easyocr), imagehash |
| Shipping | Shippo Python SDK |
| Pricing data | TCGcsv (TCGplayer market prices via CSV download) |

---

## Games Supported (v1)

- Magic: The Gathering
- Pokémon
- One Piece TCG
- Yu-Gi-Oh!

---

## Card Catalog Sources (per game)

| Game | Catalog API | Notes |
|---|---|---|
| MTG | Scryfall API | Free, comprehensive, day-of-release updates, high-quality images |
| Pokémon | pokemontcg.io | Free tier available, full card database |
| One Piece | TCGplayer catalog via TCGcsv | Verify coverage as new sets release |
| Yu-Gi-Oh! | YGOPRODECK API | Free, comprehensive |

Catalog data (card names, set codes, collector numbers, printings, images) is fetched via API and stored locally. Needs periodic refresh as new sets release.

---

## External Integrations

| Service | Status | Purpose |
|---|---|---|
| eBay API | Have key | Listing, inventory sync, order retrieval, mark shipped |
| TCGplayer API | Pending (user to pursue via rep) | Listing, inventory sync, order retrieval, mark shipped |
| Shippo API | To set up | Label generation, negotiated USPS rates |
| TCGcsv | Available | TCGplayer market price data download |
| CardMarket API | Future slot | European pricing + listing (design integration slot, implement later) |

**TCGplayer note:** Build the full integration assuming API access will be obtained, but also build the CSV-based fallback path described in Section 6 — SortSwift's own real-world TCGplayer integration is CSV export/upload plus a browser extension for order capture, not a live API, so a CSV fallback is a realistic long-term path even after access is granted. The TCGplayer developer program is closed to new public applicants but sellers can pursue access through their TCGplayer rep. Design the integration interface so it can be enabled/disabled cleanly.

---

## Features In Scope

### 1. Card Scanning & Recognition

**Input method:** Folder-based pull. User sets a default scan folder (scanner saves images there). A "Pull Scans" button in the UI reads new images from the folder.
- Supports a parent folder containing per-bin subfolders (e.g. `Bin1/`, `Bin2/`); a "use subfolder as bin" toggle pulls all subfolders in one operation and auto-assigns each card's bin from its immediate parent folder name, instead of requiring one pull per bin.
- Optional front/back pairing toggle: treats sequential image pairs in a pull as one card — the first image is used for recognition, the second stored as a secondary reference photo (useful for double-faced cards or two-sided listing photos).
- Supported formats: JPEG, PNG, WEBP. Flag images below a minimum resolution threshold at pull time as likely to fail OCR.

**Deduplication:** On pull, compute SHA-256 hash of each image file. Cross-reference against a `processed_scans` table in the DB. Skip any image whose hash has already been processed. Files are never moved or deleted.

**Recognition pipeline:**
1. **OCR (primary):** Extract collector number and set code from the card image using pytesseract. Each game has a known location for this text on the card (MTG: bottom-left, Pokémon: bottom-right, etc.). Look up the extracted code against the local catalog DB to identify the card.
2. **Perceptual hash (fallback):** If OCR fails or returns low confidence, compute a perceptual hash of the card image and compare against pre-computed hashes of catalog reference images. Return top N matches for user confirmation.
3. **Manual disambiguation UI:** When multiple similar cards match (e.g. same card, different set/printing), present side-by-side image comparison for user to select the correct card. This is the primary reason React is used over a server-side-rendered approach.
4. **Manual catalog search fallback:** When OCR and perceptual hash both fail to produce any candidate (or all candidates are rejected), present a manual search box (name/set/collector number) against the local catalog — also usable as a first-class "add without scanning" path.

**Confidence & review:** Each recognized card gets a confidence score (OCR match strength / phash distance). Results below a configurable threshold are visually flagged "needs review." An alternatives panel (top-N candidates) is available for every scan, not only ambiguous ones.

**Session persistence:** Recognized-but-unconfirmed results are written to a `scan_queue` table immediately, not held only in UI state, so a session can be closed and reopened without re-processing already-scanned images. Provide a simple "past pulls" view (date, image count, resolved/pending count).

**Language:** Attempt language detection via OCR where the catalog carries multi-language print data (e.g. Japanese/Korean/German prints). If detection fails or the catalog lacks that language's print data, fall back to a session-default language with per-card override (same pattern as condition). Feeds the pricing language modifier (Section 5) and eBay item specifics (Section 6).

**No automatic condition detection.** Condition is assigned manually during intake (see below).

**Sealed product:** Filter sealed product entries out of the catalog at import time so they don't appear as recognition candidates. Sealed product still has a full inventory lifecycle once acquired — see Section 4 (Custom / Non-Catalog Items) for breaking sealed boxes/cases down into components.

---

### 2. Intake & Condition Grading

During a scanning session:
- User sets a **session-default condition** (NM, LP, MP, HP, Damaged) before scanning begins. Changeable at any time mid-session.
- User can also set a **session-default printing/variant** (e.g. "treat this batch as Foil") and a **session-default bin**, mirroring session-default condition — a whole pull can be pre-assigned in one go, with per-card override for exceptions.
- Each recognized card is presented for review with its assigned condition (and printing/bin) pre-filled from the session defaults.
- User can override any of these per card before confirming.
- User confirms or rejects each recognition result. **Reject** discards the recognition result and removes the item from the active queue — the source image is never deleted or moved, so it can be resolved immediately via manual search (Section 1) or re-pulled later by clearing its hash from `processed_scans`.
- On confirm: card enters staging (see below) with condition, quantity, cost basis (if entered), and bin assignment.

**Supported conditions:** NM, LP, MP, HP, Damaged (matching TCGplayer standard)

**Printing variants:** Track foil, reverse holo, etched, first edition, alternate art, etc. per game. Naming conventions normalized to system-standard terms (e.g., "foil" for MTG, "holo" for Pokémon — stored with a canonical internal type, not user-facing synonyms).

**Bulk review actions:** The scan review queue supports multi-select (checkboxes) with bulk-set condition, printing, language, and bin; bulk-approve (confirm + send to staging); bulk-reject/delete; and a "clear all" action to discard the whole pulled batch. Per-card confirm/reject/override remains available for one-offs. Support sorting (scan order, card name, file name, market price, confidence, collector number, rarity) and filtering (status, flagged-only for low confidence) so large pulls can be triaged low-confidence-first.

**Staging (pre-commit review):** Confirmed scans and CSV imports land in a `staging` state distinct from live inventory, rather than going straight to live inventory.
- Each staged row is individually editable (condition, printing, language, bin, cost, price).
- Bulk approve/reject across many rows at once. Approval can be **partial** — push some rows to live inventory now, leave the rest staged for later.
- A repricing preview (Section 5's simulation mode) can be applied while still staged, before anything goes live.
- Rejecting in staging permanently discards the row — it is not added to inventory.
- A "skip staging" direct-to-live mode remains available for trusted or already-reviewed intake (e.g. a single manually-added card); staging is the default for scan batches and CSV imports, not mandatory for every path.

**Manual add (non-scan intake):** Two manual entry tiers:
- A full form (cost, bin, condition, comment) for one-off detailed entries — receiving a shipment, correcting a mistake.
- A streamlined bulk-add flow: search/multi-select several catalog products via checkboxes, bulk-set quantity/price/cost per row (with an "apply to all" shortcut), and submit in one action.

Both route through staging like any other intake path.

**Scan result export:** Allow exporting the current session's recognized cards to CSV/XLSX with selectable columns, independent of pushing to inventory (for backup or external use). Rejected/zero-quantity items excluded.

---

### 3. Inventory Management

**Inventory record model:** Cards with the same identity (card + condition + printing) in the same bin are stored as a single inventory record with an aggregate quantity. There is no per-unit tracking at the inventory level.

Each inventory record stores:
- Card identity (catalog ID, game, set, collector number, name, printing variant)
- Condition
- Bin location
- Quantity (aggregate of all units sharing this identity + condition + printing + bin)
- **Comment:** optional free-text note per record, independent of bin, searchable/filterable, includable on exports (e.g. "signed," "played in FNM — see photo")
- Marketplace listing status per marketplace (unlisted / listed / sold)
- Listed price per marketplace
- External listing/offer IDs per marketplace (e.g. eBay listing ID + offer ID, TCGplayer SKU ID) — persisted fields, not just a listing-status enum, so updates target the existing listing instead of duplicating it
- Per-marketplace **listing cap**: optional max quantity to expose to a given marketplace, independent of quantity on hand. Setting a cap to 0 excludes the item from that marketplace entirely ("in-store only"). See Section 6.1 for how this interacts with cross-marketplace oversell prevention.

**FIFO cost tracking (separate from inventory record):** A dedicated `acquisition_log` table records each purchase event:
- Card identity + condition + printing
- Quantity acquired in this batch
- Unit cost paid
- Date acquired

When a unit sells, COGS is taken from the oldest unexhausted acquisition batch for that card+condition+printing. The acquisition log is the authoritative source for cost basis and inventory age; the inventory record just tracks current quantity and location. This log is FIFO-cost-specific; general inventory mutations (manual adjustments, transfers, sales) are recorded separately in the Audit / Adjustment Log below.

**Bin/location tracking:**
- User defines named bins (arbitrary labels: "MTG-NM-A", "BOX-3", etc.) — bins are created ad hoc: the first time a new label is typed during intake, it exists, with no pre-definition step required.
- Each inventory record is assigned to a bin
- Bin assignment is set during intake or can be edited later
- Bin contents viewable: filter inventory by bin
- Bin is a soft reference — no enforced physical structure
- **Bulk transfer:** filter/select multiple records, assign a new bin in one action; logged to transfer history.
- **Location Summary report:** inventory grouped by bin (including an explicit "unassigned" bucket) for browsing and spotting orphaned/mistyped bins to fix via bulk edit. No automatic fuzzy-merge of similar bin names.

**Filtering & search:** Filter inventory by game, set, condition, rarity, printing, bin, listing status, price range, days in inventory (derived from oldest acquisition log entry), and comment text.

**CSV import:** Bulk import inventory from CSV with a field-mapping UI (user maps their column names to system fields). Handles the migration of existing inventory.
- Required fields: a **stable catalog identifier** per row (Scryfall ID, TCGplayer product/SKU ID, or set+collector number — free-text card name alone is not sufficient for reliable matching), plus condition and quantity. Optional: bin, cost basis, date acquired, comment.
- Three import modes: **Add** (default, additive — increments existing quantities / creates new records); **Overwrite** (sets exact quantities from the file, for full recounts/migrations); **Deduction** (applies a marketplace order-export, e.g. a TCGplayer sold-orders CSV, as decrements — a fallback for reflecting marketplace sales without a live API connection; see Section 6).
- Rows that can't be confidently matched (ambiguous printing/finish, name collisions across sets/reprints) are queued for manual disambiguation using the same side-by-side UI as OCR/phash recognition (Section 1), rather than auto-imported or silently dropped.
- Every import batch is logged (status: completed / error / in-progress / partially-complete; row and quantity counts; filename; timestamp; per-row errors) and reversible within a **15-minute window** from the import log — undo removes exactly the added quantities (clamped at 0) and skips items already sold/deleted with a warning.
- Preview before import: show sample rows with mapped values for confirmation.

**Inventory export:** Export inventory to CSV/XLSX, filtered by game/set/product type, with a column picker (show/hide/reorder) and reusable saved export templates. Choice of output layout: native format or a marketplace-specific column layout (e.g. TCGplayer, eBay) for external tools. Options to exclude zero-quantity rows or merge duplicate SKUs into one export row. A separate **Out of Stock** export lists previously-stocked, now-zero-quantity items as a restock candidate list. Export is on-demand only — no scheduled/emailed export, consistent with the manual-trigger philosophy used elsewhere in this doc.

**Audit / Adjustment Log:** Every inventory mutation — addition, deduction, manual adjustment, transfer — writes to an `inventory_log` table (type enum: addition / deduction / adjustment / transfer), distinct from the FIFO-focused `acquisition_log` above. Each entry records: card identity, signed quantity delta, price/cost at time of change, comment and bin before/after, a cause tag (manual adjustment, bulk update, undo, sale, CSV import, transfer), source (staff/automated/platform), and timestamp. Viewable per-item as a history timeline from the inventory record, and globally as a searchable/filterable/exportable log — useful for spotting shrinkage or loss patterns.

**Manual Stock Adjustments:** A dedicated correction workflow, distinct from sale-driven deduction or bulk CSV edit — for physical count corrections, shrinkage, found stock, and data-entry fixes. Two modes: **set exact quantity** (system computes the delta) or **adjust by amount** (+/- entered directly), with an optional "Damaged" flag and free-text comment. Deductions clamp at 0 — inventory can never go negative. Supports multi-select bulk adjustment. Every adjustment writes to the Audit / Adjustment Log above.

**Cycle Count:** A guided, per-bin physical count tool: scan or search to tally physical stock against system quantity, with each line flagged green (matches), yellow (discrepancy), or red (uncounted). Nothing changes inventory until a review screen is explicitly approved; approving commits the deltas (logged as adjustments) and marks the bin verified. Progress auto-saves and is resumable. Offline fallback: export the bin's expected contents, count on paper/spreadsheet, bulk-adjust afterward.

**Item Splitting:** A staged or live inventory record can be split: a dialog lets the user peel off a quantity into a new record with a different condition, printing, or language (at least one attribute must differ, or the split is blocked). Corrects a scan/import batch where some units differ from the rest of the aggregate. No split-by-bin or split-by-arbitrary-quantity-only.

**Bulk Edit:** Filter/select a set of inventory records and bulk-set: price; cost (flat $ or % of price, with an only-fill-blanks default and an explicit overwrite toggle); price override / clear override (fixed price bypassing autopricing) and a per-item price floor / clear floor (distinct from the rarity/cost guards in Section 5); quantity (set/add/subtract); comment; bin (bulk transfer, see above); per-marketplace listing caps and clears; condition/printing/language/set reassignment. Live preview before applying, a progress log, and a brief post-run undo window.

**Merge Duplicate Inventory Rows:** A utility scans selected/filtered inventory for exact-match rows — same card identity + condition + printing + language **and** same bin, cost, comment, price overrides, and marketplace listing links — and merges their quantities into one row, deleting the extras. Rows differing in any of those fields (e.g. same card split across two bins) are left separate. Irreversible.

**Delete / Restore:** Deleting an inventory record is a soft delete (a `deleted` flag) — hidden by default, viewable via a toggle, and restorable; its audit history is preserved. A separate explicit hard-delete exists for permanent removal.

---

### 4. Custom / Non-Catalog Items

For products outside the four game catalogs — graded slabs, sealed product, supplies/accessories, other collectibles — define a custom product via a Catalog Builder: Category > Group > Product > SKU. A product has a name, item type (**Graded Card** / Sealed Product / Accessory / Other), description, images (first = primary), and SKU variants (condition/language/printing as applicable). Defining a product doesn't stock it — it's added to inventory via the normal manual-add path (Section 2) with price/quantity/bin.

**Graded cards:** carry a grading company (PSA/BGS/CGC/SGC/Raw), grade value, and cert number. Flow through the same pricing/listing/fulfillment pipeline as catalog singles, with grading fields surfaced as additional item specifics on eBay.

**UPC lookup:** a barcode field on manual add resolves against custom-catalog UPCs — the intake path for sealed/accessory items that the OCR pipeline deliberately excludes (Section 1).

**Sealed product breakdown:** a "Break Down Sealed Product" action deducts 1 from a parent sealed item (box, case, display) and creates the component items (box→packs, case→displays) as new inventory records, carrying cost across proportionally. User sets per-unit price (markup % or flat amount) before confirming; new component items sync to marketplaces normally.

---

### 5. Automated Pricing Engine

Pricing rules are configured per marketplace (eBay and TCGplayer can have different configs). Rules apply globally with per-set and per-card overrides.

**Price source layer:**
- Primary source: TCGcsv (TCGplayer Market, Mid, Low, Direct Low — user selects which)
- Fallback chain: if primary source has no price, fall back to next configured source
- **Multi-source comparison mode:** when more than one price source is enabled, choose how they combine: **primary only** (default fallback chain above), **use lowest**, or **use highest** across all enabled sources — evaluated live on each reprice, not only when the primary source is missing
- Currency conversion: support EUR→USD for CardMarket prices (when integrated)

**Price tier system:** Define non-overlapping price ranges (e.g. <$1, $1–$10, $10+) with independent rule configurations per tier. This allows bulk commons to use entirely different rules than high-value singles.

**Modifier layer (% adjustments applied sequentially):**
- Condition multipliers: configurable % per condition (NM: 100%, LP: 85%, MP: 70%, HP: 50%, Damaged: 30% — all editable)
- Rarity adjustments: % modifier per rarity tier (Common, Uncommon, Rare, Mythic/Holo Rare, Secret Rare, etc.)
- Printing modifiers: % per printing type (foil, reverse holo, 1st edition, etched, alt art, etc.)
- Language modifier: % per language (English base, Japanese, German, etc.)
- Age decay: reduce price by X% after N days in inventory (configurable threshold and rate)

**Adjustment layer:**
- Flat dollar offset (add/subtract fixed amount)
- % margin on top of modified price
- Platform fee offset: add X% to cover marketplace fees (e.g. eBay 13.25% + shipping offset)
- Temporary offset: a % or dollar adjustment with an expiry date (auto-reverts)

**Guard layer:**
- Per-rarity minimum floor: never price below $X for a given rarity
- Cost-based floor: never price below cost basis (FIFO cost of oldest unit)
- **Marketplace-imposed minimum:** some marketplaces reject listings below a hard floor (e.g. eBay: $0.99) — distinct from the floors above because the rejection comes from the marketplace API, not our margin policy. Raise to the marketplace floor for that marketplace's listed price only; other marketplaces and the internal price stay at true market value.
- Per-item override: manually set a fixed price for a specific card, bypassing all rules
- Max movement cap: price cannot change by more than X% in a single reprice cycle
- Rounding rules: configurable per tier (.99, .49, nearest $0.25, nearest $0.10, exact)

**Scope controls:**
- Global defaults
- Per-set overrides (e.g. suppress repricing on a recently-released set)
- Per-card overrides (specific card gets fixed price or custom modifier)

**Simulation mode:** "Preview" button shows what prices would be calculated across inventory before committing any changes. Shows current price vs. new price, flagging large movements.

**Reprice trigger:** Manual on-demand only (user clicks "Reprice"). No automated scheduled repricing — this is a personal tool and the user controls when it runs.

---

### 6. Marketplace Sync

#### 6.1 Cross-Marketplace Inventory Conflict Resolution

Since the same physical card can be listed on both eBay and TCGplayer simultaneously, a local master quantity is authoritative — marketplace listing quantities are derived from it, not tracked independently. Three stackable controls prevent overselling:
- **Reserve quantity** (per card, per marketplace): hold back N units exclusively for one marketplace. Effective listed quantity on the other marketplace = raw quantity − reserve.
- **Listing cap** (per card, per marketplace — see Section 3): a max-quantity-to-expose ceiling, independent of reserves.
- **Cross-delist on sale:** when a sale is detected on either marketplace (via order pull), decrement the master quantity immediately and push the reduced quantity to the *other* marketplace in the same sync pass. If effective quantity hits zero, delist (end) the listing there rather than leaving a zero-quantity listing live.

#### 6.2 Sync Triggers & Staleness

eBay order detection is poll-based (eBay's API has no order webhooks) — support a configurable poll interval (default 5–15 min) alongside the manual "Sync" button. This creates a real staleness window during which the same card could sell on both marketplaces before the poll catches up and cross-delists it; reserve quantities (6.1) mitigate but don't eliminate this. State the same expected latency explicitly for TCGplayer once its integration is live, rather than assuming eBay parity.

#### 6.3 Listing Lifecycle

Distinguish four listing operations rather than one generic "Sync":
1. **Resync** — refresh price/quantity/title/description/policies on listings that already have a stored marketplace listing ID. Never creates new listings.
2. **Push Remaining** — create listings only for inventory records that are eligible (in stock, matches an active listing rule) but have no stored listing ID yet.
3. **Clear Listing ID(s)** — local-only: forget a marketplace's stored listing ID for one or all records without calling the marketplace API. Needed to recover from listings deleted out-of-band on the marketplace side (avoids a "phantom listed" state).
4. **Rebuild** — destructive clear + re-end + re-push; used only when a listing rule itself was broken (wrong category, deleted policy reference).

Per-marketplace **Pause** (stop auto-push/reprice-push, keep credentials and existing listings live) is distinct from **Disconnect** (revoke credentials, all automation stops).

#### 6.4 Listing Error Handling

Every push/sync attempt that fails records a per-item, per-marketplace error status with a specific reason code (no matching listing rule, missing required field, marketplace-side rejection, price below marketplace floor) — surfaced in the UI, not just a log file. Items with sync errors are excluded from bulk operations until resolved, then re-attempted individually once the underlying cause is fixed.

#### 6.5 Per-Marketplace Listing Eligibility

Each marketplace's listing rule includes a condition allow-list (which of NM/LP/MP/HP/Damaged are eligible for that marketplace — e.g. keep Heavily Played/Damaged off eBay but still list on TCGplayer) and optional "block sealed" / "block singles" toggles to exclude a whole product type from new listings on that marketplace (existing live listings unaffected; items that sell to zero still auto-delist).

#### 6.6 Auto-Push (optional)

Optional per-marketplace "Auto-push on add" toggle: pushes newly-added inventory automatically without a manual Sync click. Independent of the deliberate manual-only reprice trigger (Section 5) — this fires on stock addition, not on a price-change timer.

#### eBay

- Create new listings (single cards and bulk lots)
- Push updates to existing listings: price changes (after reprice) and quantity changes (after sales or inventory edits)
- Pull open orders
- Mark orders as shipped with tracking number
- **Requires a photo per listing** — use the scan image captured during intake, or fall back to the catalog reference image if no scan is available. For double-faced/transform/double-sided cards, push both front and back images — scan images if both sides were captured (Section 1), catalog reference images per side otherwise.
- **Listing rules (templates):** support multiple named rule sets, each scoped by filter (game/set/condition/product type/price range) and given an explicit priority order. First-match-wins: an item is claimed by the highest-priority rule whose filters it satisfies. Items matching no active rule are flagged as a listing error (6.4), not silently skipped.
- **Business policies:** each rule references seller-configured eBay Business Policies by ID (shipping/fulfillment, payment, return policy) — this tool selects existing policies, it does not author shipping rates itself.
- **Platform constraints (fixed, not configurable):** listings are always Fixed Price / Good Till Cancelled; there is no draft/review-before-publish state — API-pushed listings go live immediately, so use pre-push filters as the control point. A valid eBay Business Location must be configured in Seller Hub before a rule's items can publish (see Open Items).
- **Condition mapping:** eBay's top-level `condition` field for trading cards is fixed at Ungraded (4000) — actual grade is set via the Card Condition item aspect, mapped per our condition scale (NM/LP/MP/HP/Damaged). Don't map to eBay's top-level condition enum.
- **Best Offer (optional):** per-rule enable/disable, with an auto-accept threshold and auto-decline threshold stored as percentages of current price (so they stay valid across repricing) and a hard floor tied to cost basis so auto-accept can never fall below COGS.
- **Implementation note:** eBay's Inventory API separates inventory item → offer → listing; persist all three IDs (`ebay_sku`, `ebay_offer_id`, `ebay_listing_id`) per record. A populated offer ID with a blank listing ID means publish failed — surface as a listing error (6.4), not a silent "unlisted" state.

#### TCGplayer (pending API key)

- Create new listings (single cards and bulk lots)
- Push updates to existing listings: price changes and quantity changes
- Pull open orders
- Mark orders as shipped with tracking number
- **No photo required** — TCGplayer listings use their own card catalog imagery; no image upload needed
- Build the integration interface cleanly so it can be enabled once API access is confirmed
- **CSV fallback (build even once API access exists):** export current price+quantity in TCGplayer's accepted CSV format for manual upload if API push is unavailable for a SKU; generate a "deduction CSV" for cross-channel sales (cards sold on eBay) to manually reflect on TCGplayer when API-based quantity push isn't available. TCGplayer's own sales already decrement its own listing — never double-deduct TCGplayer-originated sales through this path.
- **Order deduction (CSV, interim/fallback):** two matching strategies for a sold-orders CSV — by SKU/product ID when present, or by name+set+condition when the export only has names (typical of TCGplayer's own order export). This is the same "Deduction" import mode described in Section 3.
- **TCGplayer Direct:** flag and distinguish TCGplayer-fulfilled ("Direct") orders from standard seller-fulfilled orders in the order list/pick list — different fulfillment ownership and pick-sheet ordering.

#### Cross-Marketplace (general)

- **Incremental sync:** support syncing a single inventory record on demand from its detail view, and scoping a bulk sync/export to "items changed since last sync" rather than always reprocessing everything.
- **SKU strategy:** sync and order-matching key off the internal catalog SKU (card identity + condition + printing + bin), not a marketplace-assigned or user-typed SKU string, so renaming/relisting on a marketplace doesn't break inventory linkage.
- **One-directional sync:** this is local-inventory → marketplace only. There's no feature to import pre-existing live eBay/TCGplayer listings back into local inventory as seed data — CSV import (Section 3) is the seed path for a seller migrating from prior tooling.
- **Preorder (optional, v2):** mark inventory as preorder pre-release; auto-transition to normal listing state on the catalog's recorded set release date. Deferred — depends on catalog release-date data and isn't confirmed as part of the current workflow.

**Key eBay vs. TCGplayer differences:**

| Aspect | eBay | TCGplayer |
|---|---|---|
| Photo required | Yes — scan or catalog image uploaded per listing | No — marketplace uses its own card images |
| Listing format | Fixed price, Good Till Cancelled only; item specifics required; no draft state | Condition-based inventory model; no per-listing description |
| Fee structure | ~13.25% final value fee; offset in pricing rules | Seller fee varies; offset in pricing rules independently |
| Listing granularity | One listing per SKU (card+condition+printing) | One quantity entry per SKU in seller inventory |
| Order detection | Polling (no webhooks) | API push/pull once available; CSV fallback otherwise |

---

### 7. Bulk Lot Builder

Define reusable lot templates with:
- Lot name and description
- Card filter criteria: game, set(s), rarity tier(s), condition range, price range
- Lot size: fixed card count
- Pricing method: (a) total card value × margin %, or (b) fixed price
- Duplicate handling: max N copies of the same card in a lot

Lots are generated on demand from current inventory matching the template filters. Regenerating a lot after inventory changes produces a new lot from remaining stock.

Lots are listable as eBay listings (and TCGplayer bulk when supported). Cards in a lot are "reserved" in inventory until the lot sells or is dissolved.

---

### 8. Order Fulfillment

**Pick list:**
- For any open order (single or multiple orders), generate a pick list
- Pick list shows: card name, set, condition, printing, quantity, bin location
- Exportable as PDF or printable directly from the app (Windows printer dialog)
- Group by bin to minimize physical picking steps; recommended default sort is bin (A–Z) or bin/set (A–Z)
- Bin is resolved dynamically at pick-list-render time, so a bin rename/merge after a sale doesn't orphan an already-generated pick list
- Can show both a marketplace product ID and an internal SKU ID column where relevant (CSV-based order deduction matches on SKU ID, not product ID)
- Merge identical line items across multiple orders on one pick list, summing quantity

**Packing slips:**
- Generate a packing slip per order (buyer name, items, prices, order number)
- Print directly from the app to the connected printer (no emailing)

**Shipping labels:**
- For orders with total value > $25: generate a Shippo label automatically
- For orders ≤ $25: skip label generation (user handles manually)
- Label prints directly from the app to the connected printer
- Shippo label cost logged against the order

**Inventory Labels (separate from packing slips/shipping labels):** printable price/SKU/bin labels for physical inventory, with the item's Comment field (Section 3) available as a label field for physical-copy disambiguation (e.g. "signed"). Print a single label from an inventory row's action icon, or bulk-print for a filtered set (e.g. only recently added). Scope to 1–2 fixed layouts rather than a drag-and-drop template builder (no hardware label printer in this build).

**Post-ship:**
- After printing label: mark order as shipped on the originating marketplace (eBay / TCGplayer API call)
- Support manually entering a tracking number + carrier on an order (without buying a Shippo label) for postage purchased outside the app — this still triggers the mark-shipped API call and local tracking record
- Record tracking number against the order in the local DB
- Update inventory: decrement quantities for sold cards, trigger marketplace sync

**Refunds, Cancellations & Replay Safety:**
- **Replay safety:** reprocessing an order always reverses its previous deduction before reapplying, so a retried sync never double-deducts.
- **Refund/return:** provide a way to reverse an order's inventory deduction and drop it from any open pick list if a marketplace order is refunded/returned after being marked shipped.
- **Cancellation:** a buyer/seller cancellation before shipment restores the deducted quantity; the listing is not automatically re-pushed — treat it like any other quantity change and let the next sync pick it up.
- **Partial shipment:** not supported in v1 — multi-item orders ship as a single unit. Explicitly out of scope rather than silently unhandled.
- **Manual/offline sale:** support recording a deduction for a non-marketplace sale via the Manual Stock Adjustment path (Section 3).

---

### 9. P&L and Reporting

**Cost tracking:**
- Cost basis recorded per inventory unit at time of intake (what you paid)
- FIFO costing: when a unit sells, cost of oldest acquired unit of that card is used as COGS

**Realized P&L:**
- Per sale: revenue − COGS − shipping cost − marketplace fees
- Aggregated by day / week / month / game / set

**Inventory age:**
- Days in inventory per card
- Inventory aging report: cards by age bucket (0–30d, 31–60d, 61–90d, 90d+)
- Total inventory value at cost and at current market price

**Basic market analytics (v2 — not v1):**
- Price trend per card over time
- Market overview by game/set
- Flag cards where market price has moved significantly since intake

---

## Out of Scope

| Feature | Notes |
|---|---|
| Super Sorter / Simple Sifter hardware | No hardware integration |
| Point of Sale system | Not needed |
| In-store kiosk | Not needed |
| Buylist portal | Not needed |
| Consignment management | Not needed |
| Mobile app | Desktop/web only |
| Automatic condition detection from images | Too hard without ML; manual grading instead |
| Shopify, Square, ManaPool integrations | eBay + TCGplayer only for now |
| CardMarket integration | Design slot reserved; implement later |
| Multi-user / multi-tenant | Single user |
| Authentication / login | Not needed for personal local tool |
| Scheduled/automated repricing | Manual trigger only |
| Scheduled/automated inventory export | On-demand only, mirroring the manual-reprice philosophy |
| Partial shipment of multi-item orders | Orders ship as a single unit in v1 |
| Push notifications | Not needed |

---

## Open Items / Dependencies

| Item | Owner | Blocker? |
|---|---|---|
| TCGplayer API access | User to pursue via TCGplayer rep | Blocks TCGplayer live listing sync; pricing data available via TCGcsv regardless, and a CSV export/import fallback (Section 6) works without it |
| Physical scanner purchase | User | Blocks live scanning; CSV import and manual entry work in the meantime |
| CardMarket API approval | User (future) | Not v1 |
| One Piece catalog API coverage | Verify TCGcsv/YGOPRODECK coverage | Check before implementation |
| Shippo account setup | User | Blocks label generation |
| eBay Business Location | User | Must be configured in eBay Seller Hub before any listing rule can publish — common cause of listing failures if skipped |

---

## Migration / Import Notes

User has an existing inventory dataset from prior tooling with different naming conventions (e.g., "holo" used for both Pokémon and MTG cards, where MTG standard is "foil"). The CSV import feature must include:

- Column mapping UI: user maps their column headers to system fields
- Value remapping: user maps their condition/printing/rarity values to system-standard values (e.g., "holo" → "foil" for MTG, "holo" for Pokémon)
- A stable catalog identifier per row (Scryfall ID, TCGplayer product/SKU ID, or set+collector number) for reliable matching — name-only fuzzy matching is not sufficient and will queue ambiguous rows for manual disambiguation rather than silently guessing
- Preview before import: show sample rows with mapped values for confirmation
- Migration is a one-time effort handled separately from core development

This tool does not import pre-existing live marketplace listings — CSV import is the seed path for migrating existing inventory. Live eBay/TCGplayer listings predating this tool need to be rebuilt from local inventory once it's populated (see Section 6).

---

## Key Design Constraints

- **FIFO costing from day one** — data model must support it; retrofitting later is painful
- **Per-marketplace pricing configs** — eBay and TCGplayer rules are independent; don't conflate them
- **Catalog is local** — reference card data is fetched from APIs and stored in SQLite; recognition works offline after initial sync
- **No file mutation** — processed scan images are never moved or deleted; deduplication via SHA-256 hash stored in DB
- **Printer integration** — packing slips, labels, and inventory labels print via Windows OS printer dialog from the app; no email-to-printer
- **Normalization of printing variant names** — system uses canonical internal terms; user-facing display can use game-specific names; import/migration handles the mapping
- **Master quantity is authoritative** — marketplace listing quantities are derived, not independently tracked; reserve quantities and listing caps (Section 6.1) are the tools for holding back stock per channel, not manual quantity edits on each marketplace
- **Staging as a soft landing zone** — scan and CSV intake default through a review/approve step before live inventory, catching bad OCR reads and bad import mappings before they pollute inventory or push to marketplaces
- **Every mutation is logged** — the Audit / Adjustment Log (Section 3) and 15-minute undo windows for imports/orders exist because inventory corrections are routine, not exceptional; don't build destructive-only paths where a log-and-undo path is described above
