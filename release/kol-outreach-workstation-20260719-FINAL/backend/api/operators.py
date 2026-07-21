"""运营人员接口"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import Operator

router = APIRouter()


class OperatorOut(BaseModel):
    id: int
    name: str
    email: str
    role: str

    class Config:
        from_attributes = True


class OperatorCreate(BaseModel):
    name: str
    email: str
    role: str = "operator"


@router.get("", response_model=list[OperatorOut])
def list_operators(db: Session = Depends(get_db)):
    """列出所有运营(分配下拉用)"""
    return db.query(Operator).filter(Operator.is_active == True).all()


@router.post("", response_model=OperatorOut)
def create_operator(body: OperatorCreate, db: Session = Depends(get_db)):
    """创建运营人员"""
    exists = db.query(Operator).filter(Operator.email == body.email).first()
    if exists:
        raise HTTPException(400, "邮箱已存在")
    op = Operator(name=body.name, email=body.email, role=body.role)
    db.add(op)
    db.commit()
    db.refresh(op)
    return op


@router.post("/seed", response_model=list[OperatorOut])
def seed_demo_operators(db: Session = Depends(get_db)):
    """一键创建演示运营人员(开发联调用)"""
    demos = [
        ("Alice", "alice@getcompany.com"),
        ("Bob", "bob@getcompany.com"),
        ("Carol", "carol@getcompany.com"),
    ]
    created = []
    for name, email in demos:
        if not db.query(Operator).filter(Operator.email == email).first():
            op = Operator(name=name, email=email, role="operator")
            db.add(op)
            created.append(op)
    db.commit()
    return db.query(Operator).filter(Operator.is_active == True).all()
