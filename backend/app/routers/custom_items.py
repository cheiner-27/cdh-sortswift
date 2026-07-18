"""Custom / non-catalog items: catalog builder, UPC lookup, sealed breakdown."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CustomProduct, CustomSku, InventoryItem
from ..services import inventory as inv_svc

router = APIRouter(prefix="/api/custom", tags=["custom"])


def product_dict(p: CustomProduct) -> dict:
    return {
        "id": p.id, "category": p.category, "group": p.group, "name": p.name,
        "item_type": p.item_type, "description": p.description,
        "images": p.images, "upc": p.upc,
        "breakdown_components": p.breakdown_components,
        "skus": [{
            "id": s.id, "condition": s.condition, "language": s.language,
            "printing": s.printing, "grading_company": s.grading_company,
            "grade_value": s.grade_value, "cert_number": s.cert_number,
        } for s in p.skus],
    }


@router.get("/products")
def products(q: str = "", item_type: str = "", db: Session = Depends(get_db)):
    query = select(CustomProduct)
    if q:
        query = query.where(CustomProduct.name.ilike(f"%{q}%"))
    if item_type:
        query = query.where(CustomProduct.item_type == item_type)
    return [product_dict(p) for p in db.execute(query).scalars()]


@router.post("/products")
def create_product(payload: dict = Body(...), db: Session = Depends(get_db)):
    p = CustomProduct(
        category=payload.get("category", "Other"),
        group=payload.get("group", ""),
        name=payload["name"],
        item_type=payload.get("item_type", "other"),
        description=payload.get("description", ""),
        images=payload.get("images", []),
        upc=payload.get("upc") or None,
        breakdown_components=payload.get("breakdown_components", []))
    db.add(p)
    db.flush()
    for s in payload.get("skus", [{}]):
        db.add(CustomSku(
            product_id=p.id, condition=s.get("condition"),
            language=s.get("language"), printing=s.get("printing"),
            grading_company=s.get("grading_company"),
            grade_value=s.get("grade_value"), cert_number=s.get("cert_number")))
    db.commit()
    return product_dict(p)


@router.put("/products/{product_id}")
def update_product(product_id: int, payload: dict = Body(...),
                   db: Session = Depends(get_db)):
    p = db.get(CustomProduct, product_id)
    if not p:
        raise HTTPException(404)
    for f in ("category", "group", "name", "item_type", "description",
              "images", "upc", "breakdown_components"):
        if f in payload:
            setattr(p, f, payload[f])
    db.commit()
    return product_dict(p)


@router.post("/products/{product_id}/skus")
def add_sku(product_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    p = db.get(CustomProduct, product_id)
    if not p:
        raise HTTPException(404)
    s = CustomSku(product_id=p.id, condition=payload.get("condition"),
                  language=payload.get("language"), printing=payload.get("printing"),
                  grading_company=payload.get("grading_company"),
                  grade_value=payload.get("grade_value"),
                  cert_number=payload.get("cert_number"))
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.get("/upc/{upc}")
def upc_lookup(upc: str, db: Session = Depends(get_db)):
    """Barcode intake path for sealed/accessory items."""
    p = db.execute(select(CustomProduct).where(
        CustomProduct.upc == upc)).scalars().first()
    if not p:
        raise HTTPException(404, "no custom product with that UPC")
    return product_dict(p)


@router.post("/breakdown/{inventory_id}")
def break_down_sealed(inventory_id: int, payload: dict = Body(default={}),
                      db: Session = Depends(get_db)):
    """Break Down Sealed Product: deduct 1 parent, create component inventory
    records with cost carried across proportionally."""
    parent = db.get(InventoryItem, inventory_id)
    if not parent or not parent.custom_sku:
        raise HTTPException(404, "not a custom-product inventory record")
    product = parent.custom_sku.product
    components = payload.get("components") or product.breakdown_components
    if not components:
        raise HTTPException(400, "no breakdown components defined on this product")
    if parent.quantity < 1:
        raise HTTPException(400, "no sealed units in stock")

    parent_cost = inv_svc.fifo_unit_cost(db, parent) or 0.0
    total_units = sum(int(c.get("count", 0)) for c in components) or 1
    markup_pct = float(payload.get("markup_pct", 0))
    created = []

    inv_svc.apply_delta(db, parent, -1, type="deduction", cause="breakdown",
                        comment=f"broke down 1x {product.name}")
    inv_svc.consume_fifo(db, parent, 1)

    for comp in components:
        comp_product_id = comp.get("component_product_id")
        count = int(comp.get("count", 1))
        if comp_product_id:
            comp_product = db.get(CustomProduct, comp_product_id)
        else:
            comp_product = CustomProduct(
                category=product.category, group=product.group,
                name=comp.get("name", f"{product.name} component"),
                item_type="sealed", description=f"component of {product.name}")
            db.add(comp_product)
            db.flush()
            db.add(CustomSku(product_id=comp_product.id))
            db.flush()
        sku = comp_product.skus[0] if comp_product.skus else None
        if sku is None:
            sku = CustomSku(product_id=comp_product.id)
            db.add(sku)
            db.flush()
        item = inv_svc.find_or_create_item(db, custom_sku_id=sku.id,
                                           condition="NM", bin=parent.bin)
        unit_cost = round(parent_cost / total_units, 4)
        unit_price = comp.get("unit_price")
        if unit_price is None and markup_pct:
            unit_price = round(unit_cost * (100 + markup_pct) / 100.0, 2)
        if unit_price is not None:
            item.current_price = unit_price
        inv_svc.add_stock(db, item, count, unit_cost, cause="breakdown",
                          comment=f"from breakdown of {product.name}")
        created.append({"inventory_id": item.id, "name": comp_product.name,
                        "quantity": count, "unit_cost": unit_cost,
                        "unit_price": unit_price})
    db.commit()
    return {"created": created}
