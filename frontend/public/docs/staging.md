# Staging

Staging is the review buffer between intake and live inventory. Confirmed scans,
CSV imports, and manual adds land here so nothing hits inventory unreviewed.

Nothing in staging exists as stock yet. **Approve** commits it; **Reject**
permanently discards the row (it is never added to inventory).

## Fixing rows

Every field is editable in place — condition, printing, language, bin, quantity,
cost, acquired date, price, comment. Edits save when you leave the field.

**Acquired** is the original purchase date, not today. It drives FIFO age, so
setting it correctly on migrated or late-entered stock keeps aging reports honest.
Left blank, the row ages from the moment you approve it.

## Setting a field across a batch

Tick the rows you want (or the header checkbox for all), and a **Set on N
selected** bar appears above the table: acquired date, cost, price, bin,
condition, printing, language, quantity, bulk lot.

Fill only the fields you want to change and hit **Apply** — blanks are left
alone, so this never wipes a value. It's the fast path for the usual case: a box
of cards bought together on one date at one per-card cost.

The bar keeps your values after applying, so you can select the next group and
hit Apply again. **Clear form** empties it.

> To *clear* a field rather than set it, edit that row directly — a blank in the
> bulk bar means "don't touch", not "erase". The one exception is **bulk lot**:
> picking *— fresh stock —* there clears the source on every selected row.

## Cards pulled from a bulk pile

The **From bulk** column names the pile a row came out of. On approve such a row
is *pulled out* of that pile — decrementing it and carrying its per-card cost and
purchase date across — instead of being counted as a fresh purchase. Set it here
on any row (or clear it back to *— fresh —*), whether it arrived from a scan, a
manual add, or an import that didn't know about the pile.

Those rows show `(from bulk)` instead of a cost box, and the pile's purchase date
instead of an **Acquired** box: the card inherits the day you bought the bulk, not
the day you sifted it out. A batch-wide cost *or* date from the bulk bar skips
these rows on purpose, since the approve would ignore it either way. Every other
field still applies.

Piles with nothing on hand aren't offered — there's nothing left to pull. A row
already pointing at an emptied pile still shows it, marked *empty*.

A row whose pile can't be pulled from — emptied out since you staged it, or
deleted — is **left in staging** with the reason, not approved. Fix the pile (or
clear the source to book it as fresh stock) and approve again.

## Approving

- **Approve selected** — partial approval. Push some rows live now, leave the
  rest staged.
- **Approve all** — everything currently listed, ignoring your selection.
- **Reject selected** — discards permanently, after a confirmation.

**Reprice preview (eBay)** runs the pricing engine in simulation mode so you can
see what your rules would do before anything goes live. See *Pricing rules*.

## Adding cards by hand

**+ Add cards** searches the catalog and builds rows one card at a time, with its
own *apply to all* shortcut for printing / language / quantity / price / cost /
bin / acquired date. Tick **skip staging** to write straight to live inventory
when the intake is already trusted.

**Pull from bulk lot** at the top of that dialog is the manual counterpart to a
scanning session's: pick the pile you're sifting and every card you add from then
on comes *out* of it — cost and purchase date inherited, pile decremented — rather
than being booked as a fresh purchase. It applies to the rows already in the list
too, and any single row can be overridden in its own **From bulk** column. Cost
and acquired boxes go read-only on those rows, since the pile supplies both.
