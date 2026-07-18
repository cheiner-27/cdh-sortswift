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
- **Retailer** — where you bought it (Amazon, USPS, eBay…). Autocompletes too.
- **Qty** — count purchased (e.g. 1000 sleeves). Informational.
- **Subtotal** — the pre-tax total you paid.
- **Tax** — auto-calculated at the default rate (Settings → *Default expense tax
  rate*, e.g. 6%) of the subtotal, unless you enter an **override** for the exact
  amount.
- **Paid with** — the card/method used.
- **Notes**.

**Total = subtotal + tax.**

## How it feeds reporting

Expenses are overhead, so they're subtracted from your sales profit to get **net
profit**:

```
net profit = sales profit (Reports P&L) − operating expenses
```

The **Reports** page shows sales profit, total operating expenses, and net at the
top. Use the date range on the Expenses page to total expenses for a period, and
the category/retailer breakdown to see where the money goes.
