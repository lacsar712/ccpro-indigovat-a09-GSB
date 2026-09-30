"""染种配方档业务规则：现行唯一与双入口联锁。

缸位条缺档提示、配方专页现行列表、染缸染种保存、闲置进还原中
四处都读本模块的同一批函数，禁止各自另写查询。
"""

from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models import DyeRecipe
from app.services.vat_rules import VatRuleError


def current_recipe_map(
    db: Session, dye_names: Optional[Iterable[str]] = None
) -> dict[str, DyeRecipe]:
    """染种 -> 现行档 的唯一数据源（缸位条缺档提示与专页现行列表同源）。"""
    q = db.query(DyeRecipe).filter(DyeRecipe.is_current.is_(True))
    if dye_names is not None:
        names = list(dye_names)
        if not names:
            return {}
        q = q.filter(DyeRecipe.dye_name.in_(names))
    return {r.dye_name: r for r in q.all()}


def list_dye_names(db: Session) -> list[str]:
    """全部已建档染种名（去重排序），供染缸改染种下拉与专页缺档清单使用。"""
    rows = db.query(DyeRecipe.dye_name).distinct().order_by(DyeRecipe.dye_name).all()
    return [r[0] for r in rows]


def get_current_recipe(db: Session, dye_name: str) -> Optional[DyeRecipe]:
    return current_recipe_map(db, [dye_name]).get(dye_name)


def require_current_recipe(db: Session, dye_name: str) -> DyeRecipe:
    """现行档检查：染缸染种保存与闲置进还原中两处入口共用本函数。"""
    recipe = get_current_recipe(db, dye_name)
    if recipe is None:
        raise VatRuleError(
            f"染种「{dye_name}」没有现行配方档：请先在染种配方档专页由主管点现行。"
        )
    return recipe


def mark_current(db: Session, recipe: DyeRecipe) -> None:
    """点现行：同事务关闭同染种其它现行，再置本档现行。

    并发双点由 uniq_recipe_current_per_dye 部分唯一索引兜底，
    后提交者在 flush/commit 时抛 IntegrityError，至多一条保持现行。
    """
    db.flush()  # 先落本事务已有改动，保证下面的批量更新基于最新状态
    others = (
        db.query(DyeRecipe)
        .filter(
            DyeRecipe.dye_name == recipe.dye_name,
            DyeRecipe.is_current.is_(True),
            DyeRecipe.id != recipe.id,
        )
        .all()
    )
    for other in others:
        other.is_current = False
    recipe.is_current = True
    db.flush()


def void_current(db: Session, recipe: DyeRecipe) -> None:
    """作废现行（仅主管调用）：取消本档的现行标记。"""
    if not recipe.is_current:
        raise VatRuleError(f"染种「{recipe.dye_name}」版本 {recipe.version} 不是现行档。")
    recipe.is_current = False
    db.flush()
