"""染种配方档专页：建档（工人可）、点现行/作废现行（仅主管）。

现行唯一由 mark_current 同事务关闭同染种其它现行 + 部分唯一索引兜底；
并发双点至多一条保持现行，被拒方拿到 409 页面而非异常。
"""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import DyeRecipe
from app.services.recipe_rules import (
    current_recipe_map,
    list_dye_names,
    mark_current,
    void_current,
)
from app.services.vat_rules import VatRuleError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _need_login(request: Request, db: Session):
    return get_current_user(request, db)


def _recipes_context(
    request: Request,
    db: Session,
    user,
    view: str = "all",
    error: str | None = None,
):
    q = db.query(DyeRecipe)
    if view == "current":
        q = q.filter(DyeRecipe.is_current.is_(True))
    recipes = q.order_by(
        DyeRecipe.dye_name, DyeRecipe.effective_date.desc(), DyeRecipe.id.desc()
    ).all()
    # 专页现行列表与缸位条缺档提示同源：都读 current_recipe_map
    current_map = current_recipe_map(db)
    dye_names = list_dye_names(db)
    return {
        "request": request,
        "user": user,
        "recipes": recipes,
        "current_list": sorted(current_map.values(), key=lambda r: r.dye_name),
        "missing_dyes": [d for d in dye_names if d not in current_map],
        "view": view if view in ("all", "current") else "all",
        "error": error,
        "active": "recipes",
    }


def _render(request: Request, db: Session, user, view: str, error: str, status_code: int):
    return templates.TemplateResponse(
        request,
        "recipes.html",
        _recipes_context(request, db, user, view, error),
        status_code=status_code,
    )


@router.get("/recipes", response_class=HTMLResponse)
async def recipes_page(
    request: Request,
    view: str = "all",
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "recipes.html", _recipes_context(request, db, user, view)
    )


@router.post("/recipes", response_class=HTMLResponse)
async def recipe_create(
    request: Request,
    dye_name: str = Form(...),
    version: str = Form(...),
    effective_date: str = Form(...),
    author: str = Form(...),
    view: str = Form("all"),
    db: Session = Depends(get_db),
):
    """建档：染缸工与主管都可；新档一律非现行，点现行只能走主管入口。"""
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    dye = dye_name.strip()
    ver = version.strip()
    who = author.strip() or user.username
    try:
        if not dye or not ver:
            raise VatRuleError("染种名与版本号不能为空。")
        try:
            eff = date.fromisoformat(effective_date.strip())
        except ValueError:
            raise VatRuleError("生效日格式无效，应为 YYYY-MM-DD。")
        db.add(
            DyeRecipe(
                dye_name=dye,
                version=ver,
                effective_date=eff,
                is_current=False,
                author=who,
            )
        )
        db.commit()
        return RedirectResponse(f"/recipes?view={view}", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return _render(request, db, user, view, exc.message, 400)
    except IntegrityError:
        db.rollback()
        return _render(
            request, db, user, view, f"染种「{dye}」的版本 {ver} 已建档，版本号不可重复。", 409
        )


@router.post("/recipes/{rid}/current", response_class=HTMLResponse)
async def recipe_mark_current(
    rid: int,
    request: Request,
    view: str = Form("all"),
    db: Session = Depends(get_db),
):
    """点现行（仅主管）：同事务关闭同染种其它现行；并发双点至多一条成功。"""
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    recipe = db.get(DyeRecipe, rid)
    if not recipe:
        return RedirectResponse("/recipes", status_code=303)
    if not user.is_superuser:
        return _render(request, db, user, view, "仅主管可点现行；染缸工只能建档。", 403)
    try:
        mark_current(db, recipe)
        db.commit()
        return RedirectResponse(f"/recipes?view={view}", status_code=303)
    except IntegrityError:
        # 并发双点现行：唯一索引只放行一笔，本笔被拒，页面与还原台照常可用
        db.rollback()
        return _render(
            request,
            db,
            user,
            view,
            f"染种「{recipe.dye_name}」已被他人点现行，本笔未生效；每染种至多一条现行。",
            409,
        )


@router.post("/recipes/{rid}/void", response_class=HTMLResponse)
async def recipe_void_current(
    rid: int,
    request: Request,
    view: str = Form("all"),
    db: Session = Depends(get_db),
):
    """作废现行（仅主管）。"""
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    recipe = db.get(DyeRecipe, rid)
    if not recipe:
        return RedirectResponse("/recipes", status_code=303)
    if not user.is_superuser:
        return _render(request, db, user, view, "作废现行仅主管可操作。", 403)
    try:
        void_current(db, recipe)
        db.commit()
        return RedirectResponse(f"/recipes?view={view}", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return _render(request, db, user, view, exc.message, 400)
