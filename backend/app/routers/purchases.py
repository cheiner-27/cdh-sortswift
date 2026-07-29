"""Purchases: what each buy cost and how much of it is still on the shelf.

Reconstructed from the FIFO acquisition batches — see reports.purchase_lots.
Distinct from /api/lots, which is the sell side (bundles you list).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import reports as report_svc

router = APIRouter(prefix="/api/purchases", tags=["purchases"])


@router.get("/lots")
def lots(db: Session = Depends(get_db)):
    rows = report_svc.purchase_lots(db)
    return {
        "lots": rows,
        "totals": {
            "purchases": len(rows),
            "units": sum(l["units"] for l in rows),
            "paid": round(sum(l["paid"] for l in rows), 2),
            "left": sum(l["left"] for l in rows),
            "ask": round(sum(l["ask"] for l in rows), 2),
        },
    }
