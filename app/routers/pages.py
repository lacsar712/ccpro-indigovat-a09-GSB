from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2.utils import markupsafe
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import DipLot, Vat, Workshop
from app.services.recipe_rules import (
    current_recipe_map,
    list_dye_names,
    require_current_recipe,
)
from app.services.vat_rules import VatRuleError, validate_vat_status_change

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _tojson(value):
    return markupsafe.Markup(json.dumps(value, ensure_ascii=False))


templates.env.filters["tojson"] = _tojson

STATUS_LABELS = {
    Vat.STATUS_IDLE: "闲置",
    Vat.STATUS_REDUCING: "还原中",
    Vat.STATUS_READY: "可染色",
}


def render(request: Request, name: str, context: dict, status_code: int = 200):
    ctx = {k: v for k, v in context.items() if k != "request"}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _need_login(request: Request, db: Session):
    return get_current_user(request, db)


def _spark_points(lots: list[DipLot], width: int = 72, height: int = 28) -> list[dict]:
    """把 redox 序列压成 sparkline 坐标（无有效读数则空）。"""
    vals = [float(l.redoxMv) for l in lots if l.redoxMv is not None]
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = 0 if n == 1 else round(i * (width - 1) / (n - 1), 2)
        y = round(height - 1 - ((v - lo) / span) * (height - 1), 2)
        pts.append({"x": x, "y": y})
    return pts


def _vat_payload(vat: Vat, recipe_map: dict) -> dict:
    lots = sorted(vat.lots, key=lambda x: (x.dippedAt, x.id))
    chronological = lots
    latest = lots[-1] if lots else None
    recent = list(reversed(lots[-8:]))  # 展开区展示近几笔
    current_recipe = recipe_map.get(vat.dyeType)
    return {
        "id": vat.id,
        "code": vat.code,
        "dyeType": vat.dyeType,
        "volumeL": float(vat.volumeL),
        "status": vat.status,
        "statusLabel": STATUS_LABELS.get(vat.status, vat.status),
        "workshopId": vat.workshop_id,
        "workshopName": vat.workshop.name if vat.workshop else "",
        "hasCurrentRecipe": current_recipe is not None,
        "currentRecipeVersion": current_recipe.version if current_recipe else None,
        "lastRedox": float(latest.redoxMv) if latest and latest.redoxMv is not None else None,
        "lastMeters": float(latest.clothMeters) if latest else None,
        "lastDippedAt": latest.dippedAt.strftime("%Y-%m-%d %H:%M") if latest else None,
        "spark": _spark_points(chronological),
        "recentLots": [
            {
                "id": l.id,
                "dippedAt": l.dippedAt.strftime("%Y-%m-%d %H:%M"),
                "clothMeters": float(l.clothMeters),
                "redoxMv": float(l.redoxMv) if l.redoxMv is not None else None,
            }
            for l in recent
        ],
    }


def _bay_context(
    request: Request,
    db: Session,
    user,
    workshop_id: Optional[int] = None,
    selected_vat: Optional[int] = None,
    error: Optional[str] = None,
):
    # 始终下发全部缸位；工坊仅作前端 chip 筛选，避免切回「全部」时缺数据
    workshops = db.query(Workshop).order_by(Workshop.name).all()
    vats = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.lots))
        .order_by(Vat.code)
        .all()
    )
    # 缸位条缺档提示与配方专页现行列表同源：都读 current_recipe_map
    recipe_map = current_recipe_map(db, {v.dyeType for v in vats})
    return {
        "request": request,
        "user": user,
        "workshops": [{"id": w.id, "name": w.name, "region": w.region} for w in workshops],
        "vats": [_vat_payload(v, recipe_map) for v in vats],
        "dye_names": list_dye_names(db),
        "filter_workshop": workshop_id,
        "selected_vat": selected_vat,
        "error": error,
        "status_labels": STATUS_LABELS,
        "active": "bay",
    }


@router.get("/", response_class=HTMLResponse)
async def bay(
    request: Request,
    workshop: Optional[int] = None,
    vat: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "bay.html", _bay_context(request, db, user, workshop, vat))


@router.post("/bay/vats/{pk}/status", response_class=HTMLResponse)
async def bay_vat_status(
    pk: int,
    request: Request,
    status: str = Form(...),
    workshop: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    item = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.lots))
        .filter(Vat.id == pk)
        .first()
    )
    ws = int(workshop) if workshop.strip() else None
    if not item:
        return RedirectResponse("/", status_code=303)
    error = None
    try:
        latest = item.latest_lot()
        validate_vat_status_change(item, status, latest)
        # 入口二：闲置进还原中，须当前染种存在现行配方档（与染种保存同一检查函数）
        if status == Vat.STATUS_REDUCING and item.status != Vat.STATUS_REDUCING:
            require_current_recipe(db, item.dyeType)
        item.status = status
        db.commit()
        return RedirectResponse(f"/?vat={pk}" + (f"&workshop={ws}" if ws else ""), status_code=303)
    except VatRuleError as exc:
        error = exc.message
        db.rollback()
    return render(
        request,
        "bay.html",
        _bay_context(request, db, user, ws, pk, error),
        status_code=400,
    )


@router.post("/bay/vats/{pk}/dye", response_class=HTMLResponse)
async def bay_vat_dye(
    pk: int,
    request: Request,
    dyeType: str = Form(...),
    workshop: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    item = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.lots))
        .filter(Vat.id == pk)
        .first()
    )
    ws = int(workshop) if workshop.strip() else None
    if not item:
        return RedirectResponse("/", status_code=303)
    error = None
    new_dye = dyeType.strip()
    try:
        if not new_dye:
            raise VatRuleError("染种不能为空。")
        # 入口一：染缸染种字段保存，新染种须有现行配方档（与改状态同一检查函数）
        require_current_recipe(db, new_dye)
        item.dyeType = new_dye
        db.commit()
        return RedirectResponse(f"/?vat={pk}" + (f"&workshop={ws}" if ws else ""), status_code=303)
    except VatRuleError as exc:
        error = exc.message
        db.rollback()
    return render(
        request,
        "bay.html",
        _bay_context(request, db, user, ws, pk, error),
        status_code=400,
    )


@router.post("/bay/vats/{pk}/lots", response_class=HTMLResponse)
async def bay_log_lot(
    pk: int,
    request: Request,
    dippedAt: str = Form(...),
    clothMeters: str = Form(...),
    redoxMv: str = Form(""),
    workshop: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    item = db.get(Vat, pk)
    ws = int(workshop) if workshop.strip() else None
    if not item:
        return RedirectResponse("/", status_code=303)
    error = None
    try:
        lot = DipLot(
            vat_id=pk,
            dippedAt=datetime.fromisoformat(dippedAt),
            clothMeters=Decimal(clothMeters),
            redoxMv=Decimal(redoxMv) if redoxMv.strip() else None,
        )
        db.add(lot)
        db.commit()
        return RedirectResponse(f"/?vat={pk}" + (f"&workshop={ws}" if ws else ""), status_code=303)
    except (ValueError, InvalidOperation) as exc:
        error = f"浸染记录无效：{exc}"
        db.rollback()
    return render(
        request,
        "bay.html",
        _bay_context(request, db, user, ws, pk, error),
        status_code=400,
    )


# 旧顶栏 CRUD 路径一律回到还原台，避免「换皮表页」残留入口
@router.get("/workshops")
@router.get("/vats")
@router.get("/lots")
@router.get("/home")
async def legacy_redirect():
    return RedirectResponse("/", status_code=303)
