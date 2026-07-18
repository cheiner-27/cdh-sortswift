# Catalog & price sync

Before scanning or pricing works, the app needs a local copy of each game's card
catalog and current market prices. That's what the **Catalog** page is for.

## Sync entire catalog

The big button pulls **every set and every card** for the selected game in one
pass and stores them locally:

- **MTG** — Scryfall's bulk-data feed (streamed to disk and parsed, never held
  fully in memory; only image URLs are stored, not the images themselves).
- **Pokémon** and **One Piece** — TCGcsv (TCGplayer's data feed).
- **Yu-Gi-Oh!** — a single whole-catalog call.

This can take several minutes for MTG/Pokémon (tens of thousands of cards).
Leave the tab open. It's safe to re-run: existing cards are updated in place, not
duplicated. Importantly, it stores each card's **TCGplayer product id**, which is
what links a card to its prices and to a TCGplayer export.

Run it once per game up front, then again occasionally when big new sets drop.

### Advanced: sync a single set

For MTG/Pokémon you can expand "sync a single set" to top up one freshly-released
set without re-pulling the whole game. This is also the fast loop for testing.

## Sync prices (TCGcsv)

Pulls current market/mid/low/direct-low prices for the game from TCGcsv and
stores them per TCGplayer product id. These are the numbers the pricing engine
uses as its baseline. Re-run whenever you want fresh prices before a reprice.

## Build reference phashes

Optional. Perceptual hashes ("phashes") are image fingerprints of catalog cards,
used as a **fallback** when OCR can't read a scanned card. Building them
downloads each catalog image once, hashes it, and discards the image (only the
hash is kept).

This is the slow part — roughly ~10 cards/second, so a whole game is a long job.
**Recommendation:** don't pre-build the entire multi-game set. OCR (set code +
collector number) resolves most cards on its own; build phashes per set only for
the sets you're actively scanning, where OCR is coming up short.

## Politeness / rate limits

All catalog and price HTTP goes through a shared rate-limited client with a
descriptive User-Agent and a minimum interval between requests. This is
required, not cosmetic: TCGcsv blocks generic/missing user-agents and throttles
high request rates, and Scryfall's image CDN rejects the default user-agent.
Don't strip the User-Agent or throttle if you extend the sync code.
