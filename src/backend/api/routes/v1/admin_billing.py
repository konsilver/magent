"""Admin token billing API routes.

Provides billing summary, model pricing CRUD, and export endpoints
for tracking per-user token consumption and costs.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import require_config
from api.routes.v1.admin_usage_logs import _extract_usage_int
from core.db.engine import get_db
from core.db.models import ChatMessage, ChatSession, ModelPricing, UserShadow
from core.infra.responses import success_response

router = APIRouter(prefix="/v1/admin/billing", tags=["Admin Billing"])
logger = logging.getLogger(__name__)


# ── Pydantic schemas ────────────────────────────────────────────

class PricingCreate(BaseModel):
    model_name: str = Field(..., max_length=255)
    display_name: Optional[str] = None
    input_price: float = Field(0, ge=0)
    output_price: float = Field(0, ge=0)
    currency: str = Field("CNY", max_length=10)

class PricingUpdate(BaseModel):
    display_name: Optional[str] = None
    input_price: Optional[float] = Field(None, ge=0)
    output_price: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = None
    is_active: Optional[bool] = None


# ── Helpers ─────────────────────────────────────────────────────

def _serialize_pricing(p: ModelPricing) -> dict:
    return {
        "pricing_id": p.pricing_id,
        "model_name": p.model_name,
        "display_name": p.display_name,
        "input_price": float(p.input_price),
        "output_price": float(p.output_price),
        "currency": p.currency,
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


# ── Billing summary ────────────────────────────────────────────

@router.get("/summary", dependencies=[Depends(require_config)])
def billing_summary(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    user_id: Optional[str] = Query(None),
    group_by: str = Query("user", regex="^(user|model|day)$"),
    db: Session = Depends(get_db),
):
    """Aggregate billing with cost calculations from model_pricing."""
    pt = _extract_usage_int(ChatMessage.usage, "prompt_tokens")
    ct = _extract_usage_int(ChatMessage.usage, "completion_tokens")

    if group_by == "day":
        group_col = func.date(ChatMessage.created_at)
    elif group_by == "model":
        group_col = ChatMessage.model
    else:
        group_col = ChatSession.user_id

    query = (
        db.query(
            group_col.label("group_key"),
            func.count().label("total_requests"),
            func.sum(pt).label("prompt_tokens"),
            func.sum(ct).label("completion_tokens"),
            func.sum(pt + ct).label("total_tokens"),
            # Cost calculation: tokens / 1000 * price
            func.sum(pt * func.coalesce(ModelPricing.input_price, 0) / 1000).label("prompt_cost"),
            func.sum(ct * func.coalesce(ModelPricing.output_price, 0) / 1000).label("completion_cost"),
        )
        .select_from(ChatMessage)
        .join(ChatSession, ChatMessage.chat_id == ChatSession.chat_id)
        .outerjoin(ModelPricing, ChatMessage.model == ModelPricing.model_name)
        .filter(ChatMessage.role == "assistant")
        .group_by(group_col)
    )

    if group_by == "user":
        query = query.join(UserShadow, ChatSession.user_id == UserShadow.user_id)
        query = query.add_columns(UserShadow.username.label("display_name"))
        query = query.group_by(UserShadow.username)

    if user_id:
        query = query.filter(ChatSession.user_id == user_id)
    if date_from:
        query = query.filter(ChatMessage.created_at >= date_from)
    if date_to:
        query = query.filter(ChatMessage.created_at <= date_to)

    rows = query.order_by(group_col).all()

    items = []
    for r in rows:
        p_cost = float(r.prompt_cost or 0)
        c_cost = float(r.completion_cost or 0)
        item = {
            "group_key": str(r.group_key) if r.group_key else "unknown",
            "total_requests": r.total_requests or 0,
            "prompt_tokens": r.prompt_tokens or 0,
            "completion_tokens": r.completion_tokens or 0,
            "total_tokens": r.total_tokens or 0,
            "prompt_cost": round(p_cost, 6),
            "completion_cost": round(c_cost, 6),
            "total_cost": round(p_cost + c_cost, 6),
            "currency": "CNY",
        }
        if group_by == "user" and hasattr(r, "display_name"):
            item["display_name"] = r.display_name
        items.append(item)

    return success_response(data=items)


@router.get("/export", dependencies=[Depends(require_config)])
def export_billing(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    """Export billing data as CSV."""
    pt = _extract_usage_int(ChatMessage.usage, "prompt_tokens")
    ct = _extract_usage_int(ChatMessage.usage, "completion_tokens")

    query = (
        db.query(
            UserShadow.username,
            ChatSession.user_id,
            ChatMessage.model,
            func.count().label("total_requests"),
            func.sum(pt).label("prompt_tokens"),
            func.sum(ct).label("completion_tokens"),
            func.sum(pt + ct).label("total_tokens"),
            func.sum(pt * func.coalesce(ModelPricing.input_price, 0) / 1000).label("prompt_cost"),
            func.sum(ct * func.coalesce(ModelPricing.output_price, 0) / 1000).label("completion_cost"),
        )
        .select_from(ChatMessage)
        .join(ChatSession, ChatMessage.chat_id == ChatSession.chat_id)
        .join(UserShadow, ChatSession.user_id == UserShadow.user_id)
        .outerjoin(ModelPricing, ChatMessage.model == ModelPricing.model_name)
        .filter(ChatMessage.role == "assistant")
        .group_by(UserShadow.username, ChatSession.user_id, ChatMessage.model)
    )

    if date_from:
        query = query.filter(ChatMessage.created_at >= date_from)
    if date_to:
        query = query.filter(ChatMessage.created_at <= date_to)

    rows = query.order_by(UserShadow.username, ChatMessage.model).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["用户名", "用户ID", "模型", "请求数", "输入Token", "输出Token", "总Token", "输入费用", "输出费用", "总费用"])
    for r in rows:
        p_cost = float(r.prompt_cost or 0)
        c_cost = float(r.completion_cost or 0)
        writer.writerow([
            r.username, r.user_id, r.model or "",
            r.total_requests or 0, r.prompt_tokens or 0, r.completion_tokens or 0, r.total_tokens or 0,
            f"{p_cost:.6f}", f"{c_cost:.6f}", f"{p_cost + c_cost:.6f}",
        ])

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=billing_export.csv"},
    )


# ── Pricing CRUD ────────────────────────────────────────────────

@router.get("/pricing", dependencies=[Depends(require_config)])
def list_pricing(db: Session = Depends(get_db)):
    """List all model pricing entries."""
    rows = db.query(ModelPricing).order_by(ModelPricing.model_name).all()
    return success_response(data=[_serialize_pricing(r) for r in rows])


@router.post("/pricing", dependencies=[Depends(require_config)])
def create_pricing(body: PricingCreate, db: Session = Depends(get_db)):
    """Create a new model pricing entry."""
    existing = db.query(ModelPricing).filter(ModelPricing.model_name == body.model_name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Pricing for model '{body.model_name}' already exists")

    pricing = ModelPricing(
        pricing_id=f"mp_{uuid.uuid4().hex[:16]}",
        model_name=body.model_name,
        display_name=body.display_name,
        input_price=body.input_price,
        output_price=body.output_price,
        currency=body.currency,
    )
    db.add(pricing)
    db.commit()
    db.refresh(pricing)
    return success_response(data=_serialize_pricing(pricing))


@router.put("/pricing/{pricing_id}", dependencies=[Depends(require_config)])
def update_pricing(pricing_id: str, body: PricingUpdate, db: Session = Depends(get_db)):
    """Update an existing model pricing entry."""
    pricing = db.query(ModelPricing).filter(ModelPricing.pricing_id == pricing_id).first()
    if not pricing:
        raise HTTPException(status_code=404, detail="Pricing not found")

    for field, value in body.dict(exclude_unset=True).items():
        setattr(pricing, field, value)
    pricing.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(pricing)
    return success_response(data=_serialize_pricing(pricing))


@router.delete("/pricing/{pricing_id}", dependencies=[Depends(require_config)])
def delete_pricing(pricing_id: str, db: Session = Depends(get_db)):
    """Delete a model pricing entry."""
    pricing = db.query(ModelPricing).filter(ModelPricing.pricing_id == pricing_id).first()
    if not pricing:
        raise HTTPException(status_code=404, detail="Pricing not found")
    db.delete(pricing)
    db.commit()
    return success_response(data=None)
