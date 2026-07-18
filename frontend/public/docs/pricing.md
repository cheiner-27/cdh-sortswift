# Pricing rules

Pricing is configured **per game** (the games differ too much to share one set
of rules) and produces a price **per platform** (eBay vs TCGplayer). You build
rules on the **Pricing** page; nothing reprices on a schedule — you trigger it.

## The shape of a rule set

For a game you define:

### 1. Baseline price — fallback order

An ordered list of price sources: TCG Market, TCG Mid, TCG Low, TCG Direct Low.
The engine walks the list top-to-bottom and uses the **first source that has a
value**. So `[Market → Mid → Low]` means "use Market; if there's no market
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
                   × printing%
                   × language%     (e.g. JP 50%)
                   × age factor     (in stock ≥ N days → reduce X%)
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

## Simulate before you commit

On the Pricing page, pick a platform and hit **Simulate** to preview every
card's old vs new price with a full trace of how it was computed. Cards moving
by more than the *large-move flag %* (Settings) get a red badge so mistakes are
obvious. When it looks right, **Reprice now** commits it.

## Scope overrides (advanced)

`set_overrides` and `card_overrides` are JSON escape hatches — e.g. suppress
repricing for a whole set (`{"MH3": {"suppress": true}}`) or pin a fixed price on
a specific catalog card.
