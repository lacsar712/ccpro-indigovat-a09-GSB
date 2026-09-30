import hashlib
import hmac
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import DipLot, DyeRecipe, User, Vat, Workshop

_PWD_SALT = os.environ.get("PWD_SALT", "indigovat-dev-salt").encode("utf-8")


def hash_password(password: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), _PWD_SALT, 120000
    )
    return digest.hex()


def verify_password(plain: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(plain), hashed)


def _ensure_recipe_seed(db: Session) -> None:
    """幂等补齐配方专页演示数据：

    - 土靛/合成靛/板蓝根靛 各有现行档（土靛另有一条被替代旧版）
    - 蓼蓝草靛只有染缸工拟的未现行草稿，并挂一台闲置缸 V-21
    """
    w2 = db.query(Workshop).filter_by(name="清水江二号坊").first()
    if w2 is not None and not db.query(Vat).filter_by(code="V-21").first():
        db.add(
            Vat(
                workshop_id=w2.id,
                code="V-21",
                dyeType="蓼蓝草靛",
                volumeL=Decimal("500.00"),
                status=Vat.STATUS_IDLE,
            )
        )

    if db.query(DyeRecipe).first():
        return
    db.add_all(
        [
            DyeRecipe(
                dyeType="土靛", version="v2025.1",
                effectiveDate=date(2025, 3, 1), isCurrent=False, preparedBy="老杨",
            ),
            DyeRecipe(
                dyeType="土靛", version="v2026.1",
                effectiveDate=date(2026, 1, 15), isCurrent=True, preparedBy="老杨",
            ),
            DyeRecipe(
                dyeType="合成靛", version="v2026.1",
                effectiveDate=date(2026, 2, 1), isCurrent=True, preparedBy="阿芬",
            ),
            DyeRecipe(
                dyeType="板蓝根靛", version="v2025.2",
                effectiveDate=date(2025, 11, 20), isCurrent=True, preparedBy="老杨",
            ),
            DyeRecipe(
                dyeType="蓼蓝草靛", version="v2026-draft1",
                effectiveDate=date(2026, 9, 1), isCurrent=False, preparedBy="worker",
            ),
        ]
    )


def ensure_seed_data(db: Session) -> None:
    """幂等种子：账号 + 蓝靛湾/清水江样例缸位与电位序列。"""
    if not db.query(User).filter_by(username="admin").first():
        db.add(
            User(
                username="admin",
                password_hash=hash_password("123456"),
                is_superuser=True,
            )
        )
    if not db.query(User).filter_by(username="worker").first():
        db.add(
            User(
                username="worker",
                password_hash=hash_password("123456"),
                is_superuser=False,
            )
        )
    db.commit()

    if db.query(Workshop).first():
        _ensure_recipe_seed(db)
        db.commit()
        return

    w1 = Workshop(name="蓝靛湾一号坊", region="黔东南", notes="晨露还原较快")
    w2 = Workshop(name="清水江二号坊", region="黔南", notes="缸体较深，保温好")
    db.add_all([w1, w2])
    db.flush()

    v1 = Vat(
        workshop_id=w1.id,
        code="V-01",
        dyeType="土靛",
        volumeL=Decimal("800.00"),
        status=Vat.STATUS_REDUCING,
    )
    v2 = Vat(
        workshop_id=w1.id,
        code="V-02",
        dyeType="合成靛",
        volumeL=Decimal("600.00"),
        status=Vat.STATUS_IDLE,
    )
    v3 = Vat(
        workshop_id=w2.id,
        code="V-11",
        dyeType="土靛",
        volumeL=Decimal("900.00"),
        status=Vat.STATUS_REDUCING,
    )
    v4 = Vat(
        workshop_id=w2.id,
        code="V-12",
        dyeType="板蓝根靛",
        volumeL=Decimal("750.00"),
        status=Vat.STATUS_READY,
    )
    # 闲置缸挂在「无现行配方档」的染种上：只有未现行草稿，建档/改还原中都应被联锁拦下
    v5 = Vat(
        workshop_id=w2.id,
        code="V-21",
        dyeType="蓼蓝草靛",
        volumeL=Decimal("500.00"),
        status=Vat.STATUS_IDLE,
    )
    db.add_all([v1, v2, v3, v4, v5])
    db.flush()

    now = datetime.now(timezone.utc)

    def lots(vat_id: int, series):
        """series: (hours_ago, meters, redox or None)"""
        rows = []
        for hours, meters, redox in series:
            rows.append(
                DipLot(
                    vat_id=vat_id,
                    dippedAt=now - timedelta(hours=hours),
                    clothMeters=Decimal(meters),
                    redoxMv=Decimal(redox) if redox is not None else None,
                )
            )
        return rows

    db.add_all(
        lots(
            v1.id,
            [
                (36, "18.00", "-410.00"),
                (28, "22.50", "-455.00"),
                (20, "30.00", "-490.00"),
                (12, "40.00", "-510.00"),
                (8, "45.00", "-520.00"),
            ],
        )
    )
    db.add_all(
        lots(
            v2.id,
            [
                (6, "8.00", None),
                (1, "12.00", None),
            ],
        )
    )
    db.add_all(
        lots(
            v3.id,
            [
                (40, "25.00", "-390.00"),
                (30, "35.00", "-430.00"),
                (22, "48.00", "-460.00"),
                (14, "60.00", "-480.00"),
            ],
        )
    )
    db.add_all(
        lots(
            v4.id,
            [
                (48, "20.00", "-420.00"),
                (32, "28.00", "-470.00"),
                (20, "33.00", "-505.00"),
                (10, "38.50", "-530.00"),
            ],
        )
    )
    _ensure_recipe_seed(db)
    db.commit()
