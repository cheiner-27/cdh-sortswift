"""eBay adapter — Sell Inventory API + Fulfillment API.

Model: inventory item -> offer -> published listing; all three IDs persisted
(`ebay_sku`, `ebay_offer_id`, `ebay_listing_id`). A populated offer ID with a
blank listing ID means publish failed -> surfaced as a listing error.

Platform constraints honored: Fixed Price / GTC only, no draft state,
condition fixed at 4000 (Ungraded) with the actual grade in the
"Card Condition" aspect, photo required per listing, Business Policies and
merchant location referenced by ID.

Credentials (MarketplaceAccount.credentials JSON):
  {client_id, client_secret, refresh_token, dry_run: bool}
Set dry_run=true to exercise the full flow without hitting eBay.
"""
import base64
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import CONDITION_LABELS
from ...models import InventoryItem, ListingRule, MarketplaceAccount, MarketplaceListing
from .. import inventory as inv_svc
from ..exporting import internal_sku
from ..settings import get_setting
from .base import ListingError, MarketplaceAdapter

log = logging.getLogger(__name__)

API = "https://api.ebay.com"
TOKEN_URL = f"{API}/identity/v1/oauth2/token"
SCOPES = "https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.fulfillment"


class EbayAdapter(MarketplaceAdapter):
    marketplace = "ebay"

    def __init__(self):
        self._token: str | None = None
        self._token_expiry: float = 0

    # -- auth ---------------------------------------------------------------

    def _account(self, db: Session) -> MarketplaceAccount:
        acct = db.execute(select(MarketplaceAccount).where(
            MarketplaceAccount.marketplace == "ebay")).scalars().first()
        if acct is None or acct.status == "disconnected":
            raise ListingError("not_connected", "eBay account is not connected")
        return acct

    def _dry_run(self, db: Session) -> bool:
        return bool(self._account(db).credentials.get("dry_run"))

    def _access_token(self, db: Session) -> str:
        acct = self._account(db)
        creds = acct.credentials
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        basic = base64.b64encode(
            f"{creds.get('client_id', '')}:{creds.get('client_secret', '')}".encode()
        ).decode()
        r = httpx.post(TOKEN_URL, headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, data={
            "grant_type": "refresh_token",
            "refresh_token": creds.get("refresh_token", ""),
            "scope": SCOPES,
        }, timeout=30)
        if r.status_code != 200:
            raise ListingError("auth_failed", f"eBay token refresh failed: {r.text[:300]}")
        data = r.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 7200))
        return self._token

    def _headers(self, db: Session) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token(db)}",
            "Content-Type": "application/json",
            "Content-Language": "en-US",
        }

    def _request(self, db: Session, method: str, path: str, **kw) -> httpx.Response:
        r = httpx.request(method, f"{API}{path}", headers=self._headers(db),
                          timeout=60, **kw)
        return r

    # -- payload builders -----------------------------------------------------

    def _title(self, item: InventoryItem) -> str:
        if item.card:
            bits = [item.card.name, item.card.set_name or item.card.set_code,
                    f"#{item.card.collector_number}",
                    CONDITION_LABELS.get(item.condition, item.condition)]
            if item.printing != "normal":
                bits.insert(1, item.printing.replace("_", " ").title())
        elif item.custom_sku:
            p = item.custom_sku.product
            bits = [p.name]
            if item.custom_sku.grading_company:
                bits.append(f"{item.custom_sku.grading_company} {item.custom_sku.grade_value}")
        else:
            bits = [f"Item {item.id}"]
        return " ".join(str(b) for b in bits if b)[:80]

    def _images(self, item: InventoryItem) -> list[str]:
        """Photo required: scan image if available, else catalog reference.
        Double-faced cards push both faces."""
        urls: list[str] = []
        # Local scan files can't be referenced by eBay directly; catalog URLs can.
        # Scan-image upload requires a hosted URL — fall back to catalog reference.
        if item.card and item.card.image_url:
            urls.append(item.card.image_url)
            if item.card.is_double_faced and item.card.back_image_url:
                urls.append(item.card.back_image_url)
        if item.custom_sku and item.custom_sku.product.images:
            urls.extend(item.custom_sku.product.images[:2])
        if not urls:
            raise ListingError("missing_field", "no image available (eBay requires a photo)")
        return urls

    def _aspects(self, item: InventoryItem) -> dict:
        aspects = {"Card Condition": [CONDITION_LABELS.get(item.condition, item.condition)]}
        if item.card:
            aspects["Game"] = [item.card.game.upper()]
            aspects["Set"] = [item.card.set_name or item.card.set_code]
            aspects["Card Number"] = [item.card.collector_number]
            if item.card.rarity:
                aspects["Rarity"] = [item.card.rarity.title()]
            aspects["Language"] = [item.language.upper()]
            if item.printing != "normal":
                aspects["Finish"] = [item.printing.replace("_", " ").title()]
        sku = item.custom_sku
        if sku and sku.grading_company and sku.grading_company != "Raw":
            aspects["Professional Grader"] = [sku.grading_company]
            if sku.grade_value:
                aspects["Grade"] = [sku.grade_value]
            if sku.cert_number:
                aspects["Certification Number"] = [sku.cert_number]
        return aspects

    def _best_offer(self, db: Session, item: InventoryItem, rule: ListingRule,
                    price: float) -> dict | None:
        bo = rule.best_offer or {}
        if not bo.get("enabled"):
            return None
        cogs = inv_svc.fifo_unit_cost(db, item) or 0.0
        auto_accept = price * bo.get("auto_accept_pct", 90) / 100.0
        auto_decline = price * bo.get("auto_decline_pct", 60) / 100.0
        # hard floor: auto-accept can never fall below COGS
        auto_accept = max(auto_accept, cogs)
        auto_decline = min(auto_decline, auto_accept)
        return {
            "bestOfferEnabled": True,
            "autoAcceptPrice": {"currency": "USD", "value": f"{auto_accept:.2f}"},
            "autoDeclinePrice": {"currency": "USD", "value": f"{auto_decline:.2f}"},
        }

    # -- listing lifecycle ----------------------------------------------------

    def create_listing(self, db: Session, item: InventoryItem,
                       listing: MarketplaceListing, rule: ListingRule,
                       price: float, quantity: int) -> None:
        sku = internal_sku(item)
        listing.ebay_sku = sku
        if self._dry_run(db):
            listing.ebay_offer_id = f"dry-offer-{uuid.uuid4().hex[:8]}"
            listing.ebay_listing_id = f"dry-listing-{uuid.uuid4().hex[:8]}"
            return

        location_key = get_setting(db, "ebay_merchant_location_key")
        if not location_key:
            raise ListingError(
                "missing_field",
                "eBay merchant location key not set (configure a Business "
                "Location in Seller Hub, then set it in Settings)")

        # 1. inventory item
        r = self._request(db, "PUT", f"/sell/inventory/v1/inventory_item/{sku}", json={
            "availability": {"shipToLocationAvailability": {"quantity": quantity}},
            # In trading-card categories eBay maps LIKE_NEW to condition 4000
            # ("Ungraded"); actual grade goes in the Card Condition aspect.
            "condition": "LIKE_NEW",
            "product": {
                "title": self._title(item),
                "aspects": self._aspects(item),
                "imageUrls": self._images(item),
            },
        })
        if r.status_code >= 400:
            raise ListingError("marketplace_rejection", f"inventory_item: {r.text[:300]}")

        # 2. offer
        offer_payload = {
            "sku": sku,
            "marketplaceId": get_setting(db, "ebay_marketplace_id"),
            "format": "FIXED_PRICE",  # always Fixed Price / GTC
            "availableQuantity": quantity,
            "categoryId": rule.ebay_category_id or "183454",  # CCG Individual Cards
            "listingPolicies": {
                "fulfillmentPolicyId": rule.ebay_fulfillment_policy_id,
                "paymentPolicyId": rule.ebay_payment_policy_id,
                "returnPolicyId": rule.ebay_return_policy_id,
            },
            "pricingSummary": {"price": {"currency": "USD", "value": f"{price:.2f}"}},
            "merchantLocationKey": location_key,
        }
        bo = self._best_offer(db, item, rule, price)
        if bo:
            offer_payload["listingPolicies"]["bestOfferTerms"] = bo
        r = self._request(db, "POST", "/sell/inventory/v1/offer", json=offer_payload)
        if r.status_code >= 400:
            raise ListingError("marketplace_rejection", f"offer: {r.text[:300]}")
        listing.ebay_offer_id = r.json().get("offerId")

        # 3. publish — offer id without listing id = publish failure (6.4)
        r = self._request(db, "POST",
                          f"/sell/inventory/v1/offer/{listing.ebay_offer_id}/publish")
        if r.status_code >= 400:
            raise ListingError("publish_failed", f"publish: {r.text[:300]}")
        listing.ebay_listing_id = r.json().get("listingId")

    def update_listing(self, db: Session, item: InventoryItem,
                       listing: MarketplaceListing,
                       price: float, quantity: int) -> None:
        if not listing.ebay_offer_id:
            raise ListingError("no_listing", "no stored eBay offer id")
        if self._dry_run(db):
            return
        r = self._request(db, "POST", "/sell/inventory/v1/bulk_update_price_quantity", json={
            "requests": [{
                "sku": listing.ebay_sku,
                "shipToLocationAvailability": {"quantity": quantity},
                "offers": [{
                    "offerId": listing.ebay_offer_id,
                    "availableQuantity": quantity,
                    "price": {"currency": "USD", "value": f"{price:.2f}"},
                }],
            }]
        })
        if r.status_code >= 400:
            raise ListingError("marketplace_rejection", f"update: {r.text[:300]}")

    def end_listing(self, db: Session, listing: MarketplaceListing) -> None:
        if self._dry_run(db):
            listing.ebay_listing_id = None
            return
        if listing.ebay_offer_id:
            r = self._request(db, "POST",
                              f"/sell/inventory/v1/offer/{listing.ebay_offer_id}/withdraw")
            if r.status_code >= 400 and r.status_code != 404:
                raise ListingError("marketplace_rejection", f"withdraw: {r.text[:300]}")
        listing.ebay_listing_id = None

    # -- orders ---------------------------------------------------------------

    def fetch_orders(self, db: Session) -> list[dict]:
        if self._dry_run(db):
            return []
        orders: list[dict] = []
        url = ("/sell/fulfillment/v1/order?filter=orderfulfillmentstatus:"
               "%7BNOT_STARTED%7CIN_PROGRESS%7D&limit=50")
        while url:
            r = self._request(db, "GET", url)
            if r.status_code >= 400:
                raise ListingError("order_fetch_failed", r.text[:300])
            data = r.json()
            for o in data.get("orders", []):
                pricing = o.get("pricingSummary", {})
                addr = ((o.get("fulfillmentStartInstructions") or [{}])[0]
                        .get("shippingStep", {}).get("shipTo", {}))
                orders.append({
                    "external_order_id": o["orderId"],
                    "buyer_name": addr.get("fullName", o.get("buyer", {}).get("username", "")),
                    "ship_to": addr,
                    "total": float(pricing.get("total", {}).get("value", 0) or 0),
                    "fees": float(o.get("totalMarketplaceFee", {}).get("value", 0) or 0),
                    "is_direct": False,
                    "ordered_at": o.get("creationDate"),
                    "items": [{
                        "sku": li.get("sku", ""),
                        "marketplace_product_id": li.get("legacyItemId", ""),
                        "description": li.get("title", ""),
                        "quantity": li.get("quantity", 1),
                        "unit_price": float(li.get("lineItemCost", {}).get("value", 0) or 0),
                    } for li in o.get("lineItems", [])],
                })
            url = data.get("next")
            if url and url.startswith(API):
                url = url[len(API):]
        return orders

    def mark_shipped(self, db: Session, external_order_id: str,
                     tracking_number: str, carrier: str) -> None:
        if self._dry_run(db):
            return
        r = self._request(
            db, "POST",
            f"/sell/fulfillment/v1/order/{external_order_id}/shipping_fulfillment",
            json={
                "trackingNumber": tracking_number,
                "shippingCarrierCode": carrier or "USPS",
                "shippedDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            })
        if r.status_code >= 400:
            raise ListingError("mark_shipped_failed", r.text[:300])
