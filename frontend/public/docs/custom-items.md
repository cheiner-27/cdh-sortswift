# Custom items

Custom items are things that aren't plain catalog singles: **graded cards**,
**sealed product**, and **accessories**. You define a *product* (with one or more
*SKU variants*), then stock it like anything else.

## Category vs Type — they answer different questions

- **Category** = the *product line* (mirrors TCGplayer): Magic, Pokémon, One
  Piece, Yu-Gi-Oh, Supplies, Other. It's a fixed dropdown so custom items line
  up with the same categories as your singles.
- **Type** = the *physical kind of thing*: `graded_card`, `sealed`, `accessory`,
  `other`.
- **Group** (optional) = a free-text sub-grouping like a set or brand.

Example: a sealed Pokémon booster box → **Category** "Pokémon", **Type**
"sealed", **Group** e.g. the set name.

## SKU variants

Each product has one or more SKUs (the actual stockable unit):

- **Graded** cards capture grading company (PSA/BGS/CGC/SGC/Raw), grade, and cert
  number instead of condition/printing.
- **Sealed / accessory / other** SKUs capture condition / printing / language as
  relevant.

## Stocking & UPC

Defining a product doesn't stock it. Add stock via **Staging → Add cards** (pick
the custom SKU) or the **UPC lookup** path for barcoded sealed/accessories.

## Sealed breakdown

A sealed product can define **breakdown components** (what one unit opens into).
"Break Down Sealed Product" from an inventory row deducts one sealed unit and
creates the component records, carrying the cost across proportionally.
