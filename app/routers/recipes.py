from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import DyeRecipe, Vat
from app.services.recipe_rules import (
    RecipeRuleError,
    activate_recipe,
    create_recipe,
    current_recipe_map,
    revoke_recipe,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _recipe_payload(r: DyeRecipe) -> dict:
    return {
        "id": r.id,
        "dyeType": r.dyeType,
        "version": r.version,
        "effectiveDate": r.effectiveDate.strftime("%Y-%m-%d"),
        "isCurrent": bool(r.isCurrent),
        "preparedBy": r.preparedBy,
    }


def _recipes_context(
    request: Request,
    db: Session,
    user,
    *,
    only_current: bool = False,
    dye_filter: Optional[str] = None,
    error: Optional[str] = None,
    form: Optional[dict] = None,
):
    # 现行列表与缸位条缺档提示同源：current_recipe_map
    current_map = current_recipe_map(db)
    query = db.query(DyeRecipe).order_by(
        DyeRecipe.dyeType, DyeRecipe.effectiveDate.desc(), DyeRecipe.id.desc()
    )
    rows = query.all()
    all_dyetypes = sorted({r.dyeType for r in rows})
    shown = rows
    if only_current:
        shown = [r for r in shown if r.isCurrent]
    if dye_filter:
        shown = [r for r in shown if r.dyeType == dye_filter]

    in_use = {v for (v,) in db.query(Vat.dyeType).distinct().all()}
    with_current = set(current_map.keys())
    missing_dyetypes = sorted(in_use - with_current)

    return {
        "request": request,
        "user": user,
        "recipes": [_recipe_payload(r) for r in shown],
        "missing_dyetypes": missing_dyetypes,
        "all_dyetypes": all_dyetypes,
        "only_current": only_current,
        "dye_filter": dye_filter or "",
        "error": error,
        "form": form or {},
        "today": date.today().strftime("%Y-%m-%d"),
        "active": "recipes",
    }


@router.get("/recipes", response_class=HTMLResponse)
async def recipes_page(
    request: Request,
    current: Optional[int] = None,
    dye: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ctx = _recipes_context(
        request, db, user, only_current=bool(current), dye_filter=(dye or "").strip()
    )
    return templates.TemplateResponse(request, "recipes.html", ctx)


@router.post("/recipes", response_class=HTMLResponse)
async def recipes_create(
    request: Request,
    dyeType: str = Form(...),
    version: str = Form(...),
    effectiveDate: str = Form(...),
    preparedBy: str = Form(...),
    isCurrent: str = Form(""),
    current: str = Form(""),
    dye: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    only_current = current.strip() == "1"
    dye_filter = dye.strip()
    form = {
        "dyeType": dyeType,
        "version": version,
        "effectiveDate": effectiveDate,
        "preparedBy": preparedBy,
        "isCurrent": isCurrent == "1",
    }
    try:
        eff = datetime.strptime(effectiveDate, "%Y-%m-%d").date()
        create_recipe(
            db,
            dye_type=dyeType,
            version=version,
            effective_date=eff,
            prepared_by=preparedBy,
            set_current=isCurrent == "1",
            is_superuser=bool(user.is_superuser),
        )
        db.commit()
        return RedirectResponse(
            "/recipes" + ("?current=1" if only_current else "")
            + (f"&dye={dye_filter}" if dye_filter else ""),
            status_code=303,
        )
    except RecipeRuleError as exc:
        db.rollback()
        ctx = _recipes_context(
            request, db, user, only_current=only_current, dye_filter=dye_filter,
            error=exc.message, form=form,
        )
        return templates.TemplateResponse(
            request, "recipes.html", ctx, status_code=exc.status_code
        )
    except ValueError:
        db.rollback()
        ctx = _recipes_context(
            request, db, user, only_current=only_current, dye_filter=dye_filter,
            error="生效日格式无效，应为 YYYY-MM-DD。", form=form,
        )
        return templates.TemplateResponse(request, "recipes.html", ctx, status_code=400)


def _redirect_recipes(only_current: bool, dye_filter: str) -> RedirectResponse:
    qs = []
    if only_current:
        qs.append("current=1")
    if dye_filter:
        qs.append(f"dye={dye_filter}")
    return RedirectResponse("/recipes" + ("?" + "&".join(qs) if qs else ""), status_code=303)


@router.post("/recipes/{pk}/activate", response_class=HTMLResponse)
async def recipes_activate(
    pk: int,
    request: Request,
    current: str = Form(""),
    dye: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    only_current = current.strip() == "1"
    dye_filter = dye.strip()
    # 点现行仅主管；被拒后配方专页仍可打开（带错误提示，不 500）
    if not user.is_superuser:
        ctx = _recipes_context(
            request, db, user, only_current=only_current, dye_filter=dye_filter,
            error="只有主管可以点现行。",
        )
        return templates.TemplateResponse(request, "recipes.html", ctx, status_code=403)
    try:
        activate_recipe(db, pk)  # 同事务关闭同染种其它现行
        db.commit()
    except RecipeRuleError as exc:
        db.rollback()
        ctx = _recipes_context(
            request, db, user, only_current=only_current, dye_filter=dye_filter,
            error=exc.message,
        )
        return templates.TemplateResponse(
            request, "recipes.html", ctx, status_code=exc.status_code
        )
    return _redirect_recipes(only_current, dye_filter)


@router.post("/recipes/{pk}/revoke", response_class=HTMLResponse)
async def recipes_revoke(
    pk: int,
    request: Request,
    current: str = Form(""),
    dye: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    only_current = current.strip() == "1"
    dye_filter = dye.strip()
    # 作废现行也仅主管
    if not user.is_superuser:
        ctx = _recipes_context(
            request, db, user, only_current=only_current, dye_filter=dye_filter,
            error="只有主管可以作废现行。",
        )
        return templates.TemplateResponse(request, "recipes.html", ctx, status_code=403)
    try:
        revoke_recipe(db, pk)
        db.commit()
    except RecipeRuleError as exc:
        db.rollback()
        ctx = _recipes_context(
            request, db, user, only_current=only_current, dye_filter=dye_filter,
            error=exc.message,
        )
        return templates.TemplateResponse(
            request, "recipes.html", ctx, status_code=exc.status_code
        )
    return _redirect_recipes(only_current, dye_filter)
