"""Cache exact TCGplayer product identities for CSV matching.

Only sets present in the uploaded CSV are fetched, at most once per day.
Public requests contain category/group IDs, never inventory rows or CSV data.
"""
from collections import defaultdict
from datetime import timedelta, timezone

import httpx
from sqlalchemy import select

from ..models import TcgCatalogGroup, collector_number_key, name_key, utcnow
from .catalog import TCGCSV_BASE, TCGPLAYER_CATEGORIES, _norm_set_name
from .httpclient import client

MAX_AGE = timedelta(hours=24)


def number_key(number):
    # Keep double-sided token numbers distinct; leading zeros are immaterial.
    value = str(number or "").strip()
    if "//" in value:
        return "//".join(collector_number_key(n.strip()) or "" for n in value.split("//"))
    return collector_number_key(value) or ""


def _fresh(when):
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return utcnow() - when < MAX_AGE


def _results(response):
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or data.get("success") is False or not isinstance(data.get("results"), list):
        raise ValueError("TCGCSV returned an invalid catalog response")
    return data["results"]


def ensure_for_lines(db, lines):
    from .order_intake import _game_code
    wanted = defaultdict(set)
    for line in lines:
        game = _game_code(line["game_label"])
        if game and not line["sealed"]:
            wanted[game].add(_norm_set_name(line["set_name"]))
    summary = {"sets_loaded": 0, "sets_cached": 0, "warnings": []}
    if not wanted:
        return summary
    with client(timeout=20) as http:
        for game, names in wanted.items():
            groups = db.execute(select(TcgCatalogGroup).where(TcgCatalogGroup.game == game)).scalars().all()
            # Missing set names are retried at the next daily refresh, not once per row.
            if not groups or not all(_fresh(g.groups_updated_at) for g in groups):
                try:
                    raw_groups = _results(http.get(f"{TCGCSV_BASE}/{TCGPLAYER_CATEGORIES[game]}/groups"))
                    if not raw_groups:
                        raise ValueError("TCGCSV returned no sets")
                    parsed_groups = [(int(raw["groupId"]), str(raw["name"]), raw.get("abbreviation")) for raw in raw_groups]
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    summary["warnings"].append(f"{game}: unable to refresh TCGCSV sets; using available cached names")
                else:
                    existing = {g.group_id: g for g in groups}
                    for gid, name, abbreviation in parsed_groups:
                        group = existing.get(gid)
                        if group is None:
                            group = TcgCatalogGroup(game=game, group_id=gid)
                            db.add(group)
                            groups.append(group)
                            existing[gid] = group
                        group.name, group.name_norm = name, _norm_set_name(name)
                        group.abbreviation = abbreviation
                        group.groups_updated_at = utcnow()
                    db.flush()
            selected = [g for g in groups if g.name_norm in names]
            missing = names - {g.name_norm for g in selected}
            if missing:
                summary["warnings"].append(f"{game}: {len(missing)} CSV set name(s) not found in TCGCSV")
            failures = 0
            for group in selected:
                if group.products is not None and _fresh(group.products_updated_at):
                    summary["sets_cached"] += 1
                    continue
                if failures >= 3:
                    summary["warnings"].append(f"{game}: further downloads paused after three failures; some sets use older or fallback matches")
                    break
                try:
                    products = _results(http.get(
                        f"{TCGCSV_BASE}/{TCGPLAYER_CATEGORIES[game]}/{group.group_id}/products"))
                    # Retain exactly what identifies a product; prices belong to PriceData.
                    aliases = []
                    for product in products:
                        extra = {e["name"]: e["value"] for e in product.get("extendedData", [])}
                        aliases.append({"product_id": int(product["productId"]),
                                        "name": str(product["name"]),
                                        "number": str(extra.get("Number") or "")})
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    failures += 1
                    summary["warnings"].append(f"{group.name}: TCGCSV unavailable; using cached names if present")
                else:
                    group.products = aliases
                    group.products_updated_at = utcnow()
                    summary["sets_loaded"] += 1
                    failures = 0
    db.flush()
    return summary


class ProductIndex:
    """One lookup index for an entire import, including vintage blank numbers."""

    def __init__(self, db, lines):
        from .order_intake import _game_code
        wanted = {(_game_code(l["game_label"]), _norm_set_name(l["set_name"]))
                  for l in lines if not l["sealed"]}
        self.by_name_set = defaultdict(list)
        self.covered_sets = set()
        self.product_set_codes = defaultdict(set)
        games = {g for g, _ in wanted if g}
        for group in db.execute(select(TcgCatalogGroup).where(TcgCatalogGroup.game.in_(games))).scalars():
            if (group.game, group.name_norm) not in wanted:
                continue
            if group.products is not None:
                self.covered_sets.add((group.game, group.name_norm))
            for product in group.products or []:
                key = (group.game, group.name_norm, name_key(product["name"]))
                self.by_name_set[key].append(product)
                if group.abbreviation:
                    self.product_set_codes[(group.game, group.name_norm, product["product_id"])].add(group.abbreviation.casefold())

    def set_codes(self, line, product_id):
        from .order_intake import _game_code
        key = (_game_code(line["game_label"]), _norm_set_name(line["set_name"]), product_id)
        return self.product_set_codes.get(key, set())

    def covers(self, line):
        from .order_intake import _game_code
        return (_game_code(line["game_label"]), _norm_set_name(line["set_name"])) in self.covered_sets

    def lookup(self, line):
        from .order_intake import _game_code
        key = (_game_code(line["game_label"]), _norm_set_name(line["set_name"]), name_key(line["card_name"]))
        candidates = self.by_name_set.get(key, [])
        number = number_key(line.get("collector_number"))
        if number:
            candidates = [p for p in candidates if number_key(p["number"]) == number]
        # A blank CSV number is absence of evidence, not evidence of mismatch.
        return sorted({p["product_id"] for p in candidates})
