# Scanning

Scanning turns folders of card images into recognized inventory. It's a
**two-step** process, matching how SortSwift works:

1. **Pull & recognize** — you point at a folder; every image is hashed
   (duplicates skipped), run through recognition, and dropped into a review
   queue.
2. **Confirm** — you review what it identified, fix any wrong matches, then
   confirm. Confirmed cards move to **Staging**, then to live inventory. Cost
   and acquired date are usually easiest to set there in one go, across the whole
   batch — see *Staging*.

## What the "Scan folder" field wants

A **full path to a folder** of image files, e.g.
`C:\Users\chrsh\Scans\2026-07-10`. It reads the image files sitting *directly*
in that folder. The field pre-fills from your default scan folder in Settings,
but you can point it anywhere per session.

- **"each subfolder is a bin"** — instead of reading images in the folder
  itself, treat every immediate subfolder as its own bin and pull the images
  inside each. Handy if you pre-sort scans into bin-named folders.
- **"pair front/back images"** — matches files by name, not folder order.
  Front/back images must share the same name except for a trailing `F` or `B`,
  e.g. `20260718-image-0007F.jpg` pairs with `20260718-image-0007B.jpg` (the
  `B` file becomes that card's back image; only the `F` file is run through
  recognition). A file with no `F`/`B` suffix, or a back with no matching
  front, is just kept as its own front-only card rather than dropped. A
  trailing `(1)`-style OS duplicate suffix (from a scanner naming glitch) is
  ignored when matching. *(Leave this off if your scanner doesn't name files
  this way.)*

Files are **never moved, renamed, or deleted.** Re-pulling the same folder is
safe — already-seen images (by SHA-256 hash) are skipped.

## Session defaults vs per-card values

The session **condition / language / bin / unit cost** are just defaults stamped
on every card in the batch. You can override any of them per card in the review
queue.

There is **no "session printing"** — printing (foil / holo / 1st edition /
reverse holo) is a property of the individual card, and any batch mixes them, so
you set printing per card during review.

**Pull from bulk lot** is the other session default, and it changes what intake
*means*: cards from that session are pulled OUT of the pile rather than bought
fresh, carrying the pile's per-card cost and purchase date. The **Source** column
in the review queue sets it per card. Only piles with cards on hand are listed —
each shows its count and purchase date, which is what the card will inherit. When
a pile is set, the session unit cost greys out, because the cost comes from the
pile. See [Bulk](/docs/bulk.md).

## How recognition works

1. **OCR** reads the set code + collector number from game-specific regions of
   the image and looks them up in the local catalog. This resolves most cards.
2. **Perceptual-hash fallback** — if OCR can't read the card and you've built
   reference phashes for that set, the scan's image fingerprint is compared to
   catalog images for near matches.
3. **Manual** — anything still unresolved: open "alternatives" and pick from
   candidates, or search the catalog by hand.

Low-confidence or unreadable cards are flagged **needs review** so you don't
accidentally confirm a wrong match. Use the "flagged only" filter to focus on
them.

> Recognition quality depends on catalog coverage (sync the catalog first) and,
> for the phash fallback, on having built phashes for the set. OCR also needs
> Tesseract installed and its path set in Settings; without it, recognition
> degrades to phash/manual.
