# Pricing rules

Pricing is configured **per game** (the games differ too much to share one set
of rules) and produces a price **per platform** (eBay vs TCGplayer). You build
rules on the **Pricing** page; nothing reprices on a schedule — you trigger it.

## The shape of a rule set

For a game you define:

### 1. Baseline price — fallback order

An ordered list of price sources: TCG Market, TCG Mid, TCG Low, TCG Direct Low.
The engine walks the list top-to-bottom and uses the **first source that has a
value for the card's printing**. Foil, holo and reverse-holo prices come from their own price rows; missing finishes are reported as no source, never chosen by row order. So `[Market → Mid → Low]` means "use Market; if there's no market
price, fall back to Mid; then Low."

### 2. Tiers (bands of the card's current price)

A tier is a rule that applies to cards whose **current price** falls in a band,
e.g. bulk `$0–1`, low `$1–5`, mid `$5–20`, high `$20+`. Tiers must not overlap;
only the last may be open-ended. A brand-new card with no price yet is tiered by
its freshly-computed baseline.

Each tier defines:

**Modifiers (stack multiplicatively).** Condition, printing, language, and an
age-decay factor are all percentages that multiply together:

```
final (pre-guards) = base
                   × condition%    (e.g. LP 85%)
                   × extra printing%
                   × language%     (e.g. JP 50%)
                   × age factor     (highest reached ladder step)
```

Example: an **LP, Japanese** card, base $10, with LP = 85%, JP = 50%:
`$10 × 0.85 × 0.50 = $4.25`. This is exactly the "is it 64%?" question — yes,
percentages compound.

**Offset by platform.** After the modifiers, apply a `%` and/or flat `$` offset,
set **separately per platform**. This is how eBay and TCGplayer end up at
different prices from the same rules (e.g. +13% on eBay to cover fees, 0 on
TCGplayer).

**Guards** (applied in this order):

- **Max move %** — don't move more than X% away from the card's *current* price
  on that platform in a single reprice (velocity limit).
- **Tier-movement lock** — keep the price inside the current tier's band. Two
  independent toggles: *can't move up* / *can't move down*. Example: a $1–5 card
  with "can't move down" checked will never be priced below $1, even if the math
  says lower — but it's still free to move up.
- **Rarity floor** — a minimum price per rarity.
- **Never below FIFO cost** — clamp up to your cost basis so you never list at a
  loss.

**Rounding** — round the final price to the nearest `0.01`, `0.05`, `0.10`,
`0.49`, `0.95`, `0.99`, or `$1`. (`.49/.95/.99` mean "nearest number ending in
those cents.")

## Order of operations

```
source (fallback) → tier (by current price) → modifiers (×) →
platform offset → guards (max-move → tier-lock → floors) → rounding →
platform minimum (e.g. eBay $0.99, applied to the listed price only)
```

## Per-item overrides

Rules are the default. Any individual card can override them from the
**Inventory → Detail** panel:

- **Price override** — a fixed price that bypasses all rules. This is also your
  "do not auto-reprice this card" switch.
- **Price floor** — a per-item minimum the rules can't go under.

## Age ladder and clock start

Each tier has an editable list of day thresholds and percentage reductions.
Only the highest reached threshold applies; steps do not compound. For example,
30 days → 5%, 60 → 10%, 120 → 20%, 240 → 30%.

The optional **Clock starts** date caps the age used for decay:
`min(actual acquisition age, days since clock start)`. Set it to the day you
begin using pricing to grandfather migrated stock. Newer acquisitions retain
their younger age. A blank clock date uses true acquisition age. No acquisition
history means no age reduction. Future clock dates hold effective age at zero.

**Start today with suggested ladder** fills today's date and those four steps
for that tier. Save the rules before simulating. Existing single-step settings
are converted without changing their meaning; existing disabled decay stays
disabled until you configure it.

Simulation shows both actual and effective age. Repricing never resets the
clock. Max movement still uses the current platform price, so the initial
baseline should come from your reviewed TCGplayer CSV.

## Printing modifiers

**Extra printing %** is an additional multiplier on a baseline that already
reflects the finish. Leave it blank (100%) unless you want an extra adjustment.
The trace names the price subtype used.

## Simulate before you commit

On the Pricing page, pick a platform and hit **Simulate** to preview every
card's old vs new price with a full trace of how it was computed. Cards moving
by more than the *large-move flag %* (Settings) get a red badge so mistakes are
obvious. Save edited rules first. When it looks right, **Reprice now** commits it.

**Ignore manual overrides** applies to both Simulate and Reprice. It bypasses
item fixed prices and advanced per-card fixed prices, while preserving floors
and set suppression. The table shows the stored override beside the current
platform price and proposed price.

Repricing with this switch never deletes overrides. Clear selected item
overrides deliberately through Inventory → Bulk edit; remove advanced fixed
prices under Scope overrides. A later reprice with the switch off respects
those retained overrides again.

For TCGplayer, use Cycle Counts to reconcile first, then Simulate → Reprice →
**Export pricing CSV**. The file uses the applied TCGplayer target price and
zero quantity deltas, including when you ignored an override for that run.
Only learned SKU identities with a price are exported; the download message
reports omitted rows. Complete their matches in Cycle Counts before pricing
them on TCGplayer.

## Scope overrides (advanced)

`set_overrides` and `card_overrides` are JSON escape hatches — e.g. suppress
repricing for a whole set (`{"MH3": {"suppress": true}}`) or pin a fixed price on
a specific catalog card.
