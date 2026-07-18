# Expenses

The **Expenses** page is a ledger of business overhead that isn't tied to any one
card — sleeves, top loaders, postage/stamps, software subscriptions, equipment,
etc. (Modeled on the Card Tracker Airtable "Expenses" table.) It's separate from
per-sale shipping and marketplace fees, which live on the order itself.

## An expense record

- **Date** — when you paid.
- **Name** — what it was (e.g. "Penny Sleeves", "Airtable Subscription").
- **Category** — a grouping for reporting (e.g. Supplies, Postage, Software,
  Equipment). Free text with autocomplete from what you've used before.
- **Class** — **opex** or **capex** (see below). Picking a category suggests a
  class (Equipment → capex, everything else → opex); you can override it.
- **Retailer** — where you bought it (Amazon, USPS, eBay…). Autocompletes too.
- **Qty** — count purchased (e.g. 1000 sleeves). Informational.
- **Subtotal** — the pre-tax total you paid.
- **Tax** — auto-calculated at the default rate (Settings → *Default expense tax
  rate*, e.g. 6%) of the subtotal, unless you enter an **override** for the exact
  amount.
- **Paid with** — the card/method used.
- **Notes**.

**Total = subtotal + tax.**

## Opex vs capex

Each expense is classified as one of two kinds:

- **Opex** (operating expense) — consumable overhead used up in normal operation:
  sleeves, top loaders, stamps/postage, mailers, software subscriptions, fees.
- **Capex** (capital expenditure) — a *durable asset* with a useful life beyond a
  year: a printer, scanner, paper cutter, loupe, shelving.

Why bother, if both hit net profit? Because the distinction is standard
accounting and keeps one-off equipment purchases from looking like recurring
overhead when you review a month. Reports show opex and capex on separate lines.

This tool applies the **IRS de minimis safe harbor**: a small business may expense
low-cost capital items (under $2,500 per item) in the period they're bought
rather than depreciating them over several years. Every asset here is well under
that threshold, so capex is expensed in-period just like opex — it's simply
labeled and reported separately. (If you ever buy something above the threshold,
that's when you'd want to depreciate it instead.)

## How it feeds reporting

Expenses are overhead, so they're subtracted from your sales profit to get **net
profit**:

```
net profit = sales profit (Reports P&L) − opex − capex
```

The **Reports** page shows sales profit, operating expenses (opex), capital
expenditures (capex), and net at the top. Use the date range on the Expenses page
to total expenses for a period, and the category / class / retailer breakdowns to
see where the money goes.
