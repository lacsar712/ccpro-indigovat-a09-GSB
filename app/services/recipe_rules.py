"""染种配方档规则。

两个唯一约束：
- 同染种 + 版本号 唯一（模型层 UniqueConstraint）
- 同染种现行档至多一条（模型层部分唯一索引兜底，并发点现行也只会成功一条）

双入口联锁：染缸「保存建档」与「闲置改还原中」两处入口都调用
``assert_dyetype_has_current`` 这一个函数，禁止只拦一处。
缸位条缺档提示与配方专页现行列表共用 ``current_recipe_map`` 这一个数据源。
"""

from datetime import date
from typing import Optional

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import DyeRecipe


class RecipeRuleError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def is_lock_unavailable(exc: OperationalError) -> bool:
    """行锁冲突：Postgres NOWAIT 抢锁失败 (55P03)，或 SQLite 写锁被占。"""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(getattr(orig, "diag", None), "sqlstate", None)
    if sqlstate == "55P03":  # lock_not_available
        return True
    text_l = str(orig or exc).lower()
    return "database is locked" in text_l or "could not obtain lock" in text_l


def lock_conflict_error() -> RecipeRuleError:
    return RecipeRuleError(
        "该染种正有另一条配方在点现行，同染种至多保留一条现行，请刷新后重试。",
        status_code=409,
    )


# ---- 现行档查询：缸位条缺档提示与配方专页现行列表的唯一共同来源 ----

def current_recipe_map(db: Session) -> dict[str, DyeRecipe]:
    """返回 {染种名: 现行配方档}。全系统现行档的唯一读取口径。"""
    rows = db.query(DyeRecipe).filter(DyeRecipe.isCurrent.is_(True)).all()
    result: dict[str, DyeRecipe] = {}
    for row in rows:  # 兜底：理论上部分唯一索引已保证同染种仅一条
        kept = result.get(row.dyeType)
        if kept is None or row.id > kept.id:
            result[row.dyeType] = row
    return result


def assert_dyetype_has_current(db: Session, dye_type: str) -> DyeRecipe:
    """双入口共用的现行档检查：保存染缸 / 闲置改还原中都必须先过这里。"""
    key = (dye_type or "").strip()
    current = current_recipe_map(db).get(key)
    if current is None:
        raise RecipeRuleError(
            f"染种「{key}」尚无现行配方档：请先到染种配方专页由主管点现行，"
            f"再保存染缸或把状态改为还原中。"
        )
    return current


# ---- 建档 / 点现行 / 作废 ----

def _duplicate_version_exists(db: Session, dye_type: str, version: str) -> bool:
    return (
        db.query(DyeRecipe.id)
        .filter(DyeRecipe.dyeType == dye_type, DyeRecipe.version == version)
        .first()
        is not None
    )


def create_recipe(
    db: Session,
    *,
    dye_type: str,
    version: str,
    effective_date: date,
    prepared_by: str,
    set_current: bool = False,
    is_superuser: bool = False,
) -> DyeRecipe:
    """建档（染缸工可建档）。set_current=True 仅主管可用，建档与点现行同事务完成。"""
    dye_type = (dye_type or "").strip()
    version = (version or "").strip()
    prepared_by = (prepared_by or "").strip()
    if not dye_type:
        raise RecipeRuleError("染种名不能为空。")
    if not version:
        raise RecipeRuleError("版本号不能为空。")
    if not prepared_by:
        raise RecipeRuleError("编制人不能为空。")
    if set_current and not is_superuser:
        raise RecipeRuleError("只有主管可以点现行。", status_code=403)
    if _duplicate_version_exists(db, dye_type, version):
        raise RecipeRuleError(f"染种「{dye_type}」已存在版本 {version}，版本号须唯一。")

    recipe = DyeRecipe(
        dyeType=dye_type,
        version=version,
        effectiveDate=effective_date,
        isCurrent=False,
        preparedBy=prepared_by,
    )
    db.add(recipe)
    try:
        db.flush()  # 让同染种版本唯一约束在本事务内即生效
    except IntegrityError:
        db.rollback()
        raise RecipeRuleError(f"染种「{dye_type}」已存在版本 {version}，版本号须唯一。")

    if set_current:
        _activate_locked(db, recipe)
    return recipe


def _activate_locked(db: Session, recipe: DyeRecipe) -> None:
    """把同染种其它现行档关闭、目标档点现行，全部在同一事务内。

    并发两人同时点现行：先靠 NOWAIT 行锁串行化，抢锁失败者立即被拒；
    极端时序下再由部分唯一索引（同染种现行至多一条）兜底，flush 抛唯一冲突。
    """
    others = (
        db.query(DyeRecipe)
        .filter(
            DyeRecipe.dyeType == recipe.dyeType,
            DyeRecipe.isCurrent.is_(True),
            DyeRecipe.id != recipe.id,
        )
        .with_for_update(nowait=True)
        .all()
    )
    for other in others:
        other.isCurrent = False
    recipe.isCurrent = True
    try:
        db.flush()
    except OperationalError as exc:
        db.rollback()
        if is_lock_unavailable(exc):
            raise lock_conflict_error()
        raise
    except IntegrityError:
        db.rollback()
        raise RecipeRuleError(
            "该染种已存在现行配方档（并发点现行），同染种至多保留一条现行。",
            status_code=409,
        )


def activate_recipe(db: Session, recipe_id: int) -> DyeRecipe:
    recipe = db.get(DyeRecipe, recipe_id)
    if recipe is None:
        raise RecipeRuleError("配方档不存在。", status_code=404)
    if recipe.isCurrent:
        return recipe  # 幂等：已是现行
    _activate_locked(db, recipe)
    return recipe


def revoke_recipe(db: Session, recipe_id: int) -> DyeRecipe:
    """作废现行（仅主管，在路由层强制）。"""
    recipe = db.get(DyeRecipe, recipe_id)
    if recipe is None:
        raise RecipeRuleError("配方档不存在。", status_code=404)
    recipe.isCurrent = False
    db.flush()
    return recipe
