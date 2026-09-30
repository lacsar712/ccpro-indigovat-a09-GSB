import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "6120")
    name = os.environ.get("POSTGRES_DB", "indigovat")
    user = os.environ.get("POSTGRES_USER", "indigovat")
    password = os.environ.get("POSTGRES_PASSWORD", "indigovat")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


DATABASE_URL = os.environ.get("DATABASE_URL") or _database_url()

# sqlite 本地跑时给写锁一个等待窗口，并发点现行时后到的写事务等先到的提交，
# 再被唯一索引拒为 IntegrityError，而不是直接 database is locked
_connect_args = {"timeout": 15} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
