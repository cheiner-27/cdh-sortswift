# Glossary

**Printing** — the only thing distinguishing two otherwise-identical cards (same
name, set, collector number). The set is intentionally small: `normal`, `holo`,
`foil`, `first_edition`, `reverse_holo`. Full-art, alternate-art, textured,
serialized, etc. are *separate catalog cards* with their own numbers, not
printings. Language is tracked on its own field, not as a printing.

**Tier** — a band of a card's *current price* that selects which pricing rule
applies (e.g. `$1–5`). See *Pricing rules*.

**Baseline / source** — the market number a price is built from: TCG Market, Mid,
Low, or Direct Low. Rules use an ordered fallback among them.

**Modifier** — a percentage in a pricing tier (condition/printing/language/age)
that multiplies with the others.

**Offset** — a per-platform `%`/flat `$` adjustment applied after modifiers, so
eBay and TCGplayer can differ.

**Max move %** — pricing guard: cap how far a reprice can move a card from its
current price in one go.

**Tier-movement lock** — pricing guard: keep a card's new price inside its
current tier band, with independent "can't move up" / "can't move down" toggles.

**Large-move flag %** (Settings) — *display only*: in the reprice **Simulate**
preview, any card whose new price differs from its current price by at least this
percent gets a red badge so you can spot surprises. It does not cap prices — the
per-tier *max move %* does that.

**Phash / phash max distance** (Settings) — a perceptual hash is an image
fingerprint. During scanning, when OCR fails, the scan's phash is compared to
catalog card phashes; two images within the *max distance* (Hamming distance) are
treated as a possible match. **Lower = stricter.** ~10–14 is reasonable.

**Confidence threshold** (Settings) — recognition scores below this route a scan
to "needs review" instead of auto-accepting the match.

**Min resolution** (Settings) — scans whose shorter edge is under this many
pixels are flagged low-res (OCR tends to fail on them). A warning, not a block.

**Bin** — a physical storage location label. Inventory records, pick lists, and
cycle counts are organized by bin.

**Reserve / listing cap** — per-marketplace controls on an inventory record.
*Reserve* holds units back exclusively for one marketplace; *cap* limits how many
you'll list there (cap 0 = exclude, "in-store only").

**FIFO** — first-in-first-out costing. Cost basis and inventory age come from
acquisition batches, consumed oldest-first when you sell.

**Purchase / market / listed** — the three money totals on the Inventory screen,
all for whatever the current filter matches. *Purchase* is what you paid
(remaining FIFO cost basis), *market* is TCGplayer market value × quantity (a
reference number that ignores condition and your rules), *listed* is what you're
asking (override, else the auto price) × quantity.

**Staging** — the review buffer between intake (scan/import/manual add) and live
inventory. Nothing is live until approved.

**Bulk pile** — cards bought and sold *by the count* without tracking each one
(commons, lands, "500 assorted"). Tracks a card count + FIFO cost, sold in
chunks, and sifted on the Scan page by picking it as the *pull-from* source.
Distinct from a **Bulk lot** (the lot builder), which bundles specific tracked
cards into one listing.
