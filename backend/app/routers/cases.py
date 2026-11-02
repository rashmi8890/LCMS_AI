from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Case
from ..schemas import CaseCreate, CaseUpdate, CaseOut

router = APIRouter(prefix="/api/cases", tags=["cases"])

@router.post("", response_model=CaseOut)
def create_case(payload: CaseCreate, db: Session = Depends(get_db)):
    if db.query(Case).filter_by(case_number=payload.case_number).first():
        raise HTTPException(409, "Case number already exists")
    obj = Case(**payload.model_dump()); db.add(obj); db.commit(); db.refresh(obj); return obj

@router.get("", response_model=list[CaseOut])
def list_cases(db: Session = Depends(get_db)):
    return db.query(Case).order_by(Case.created_at.desc()).all()

@router.get("/{case_id}", response_model=CaseOut)
def get_case(case_id: int, db: Session = Depends(get_db)):
    obj = db.get(Case, case_id)
    if not obj: raise HTTPException(404, "Case not found")
    return obj

@router.patch("/{case_id}", response_model=CaseOut)
def update_case(case_id: int, payload: CaseUpdate, db: Session = Depends(get_db)):
    obj = db.get(Case, case_id)
    if not obj: raise HTTPException(404, "Case not found")
    for k, v in payload.model_dump(exclude_unset=True).items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj

@router.delete("/{case_id}")
def delete_case(case_id: int, db: Session = Depends(get_db)):
    obj = db.get(Case, case_id)
    if not obj: raise HTTPException(404, "Case not found")
    db.delete(obj); db.commit(); return {"message": "Case deleted"}
