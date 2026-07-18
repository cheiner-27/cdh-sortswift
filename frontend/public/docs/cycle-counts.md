# Cycle counts

A **cycle count** is a spot audit of a single bin. Instead of recounting your
whole inventory at once, you recount one bin at a time and reconcile it against
what the system expects — a standard warehouse practice for keeping counts
accurate as small errors creep in (miscounts, mis-pulls, damage, theft).

## How it works

1. **Pick a bin** and start a count. The app snapshots every card it expects in
   that bin and its expected quantity.
2. **Count physically** and enter the actual quantity for each line. Each row is
   marked ✓ green (matches expected), Δ yellow (discrepancy), or — red (not yet
   counted). Progress **auto-saves**; you can stop and resume anytime.
3. **Review & approve.** Only on approval are the differences written to
   inventory, each as a logged **adjustment** (cause = `cycle_count`). Nothing
   changes before you approve.

## Tips

- **Export expected** produces a CSV of what should be in the bin, for counting
  offline / on paper.
- Because every adjustment is logged, a cycle count leaves a clean audit trail of
  what changed and why.
