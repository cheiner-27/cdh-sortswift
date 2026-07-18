"""Business-expense ledger CRUD + summary."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Expense
from ..services import expenses as exp_svc

router = APIRouter(prefix="/api/expenses", tags=["expenses"])


@router.get("")
def list_expenses(date_from: str = "", date_to: str = "", db: Session = Depends(get_db)):
    rows = exp_svc.list_expenses(db, date_from or None, date_to or None)
    return [exp_svc.to_dict(db, e) for e in rows]


@router.get("/summary")
def summary(date_from: str = "", date_to: str = "", db: Session = Depends(get_db)):
    return exp_svc.summary(db, date_from or None, date_to or None)


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db)):
    return exp_svc.suggestions(db)


@router.post("")
def create_expense(payload: dict = Body(...), db: Session = Depends(get_db)):
    e = exp_svc.create_expense(db, payload)
    return exp_svc.to_dict(db, e)


@router.put("/{expense_id}")
def update_expense(expense_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    e = db.get(Expense, expense_id)
    if not e:
        raise HTTPException(404)
    exp_svc.update_expense(db, e, payload)
    return exp_svc.to_dict(db, e)


@router.delete("/{expense_id}")
def delete_expense(expense_id: int, db: Session = Depends(get_db)):
    e = db.get(Expense, expense_id)
    if not e:
        raise HTTPException(404)
    db.delete(e)
    db.commit()
    return {"ok": True}
