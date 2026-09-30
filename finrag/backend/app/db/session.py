"""MySQL 连接与会话管理。

对外提供三种使用方式：
* :func:`get_db` —— FastAPI 依赖注入用的会话生成器；
* :func:`session_scope` —— 脚本/服务层用的上下文管理器（自动提交/回滚）；
* :data:`SessionLocal` —— 需要手动控制事务时直接使用。

生产环境建表以 ``sql/01_schema.sql`` 为准（含字符集、注释、索引），
:func:`init_db` 仅用于测试和快速本地初始化。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import DatabaseError
from app.core.logging_conf import get_logger
from app.db.models import Base

logger = get_logger(__name__)

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def _build_engine(settings: Settings) -> Engine:
    """按配置构造引擎，并针对 SQLite / MySQL 分别设置连接参数。"""
    url = settings.database_url
    kwargs: Dict[str, Any] = {"echo": settings.mysql_echo, "future": True}

    if url.startswith("sqlite"):
        # 单测场景：内存库需共享同一连接
        from sqlalchemy.pool import StaticPool

        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("sqlite://"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)
        _enable_sqlite_foreign_keys(engine)
        return engine

    kwargs.update(
        pool_size=settings.mysql_pool_size,
        max_overflow=settings.mysql_max_overflow,
        pool_pre_ping=True,      # 断线自动重连，避免长连接失效
        pool_recycle=3600,
    )
    return create_engine(url, **kwargs)


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """为 SQLite 连接开启外键约束。

    SQLite 默认 **不** 强制外键，因此 ``ON DELETE CASCADE`` 会被静默忽略，
    删除文档后条文会变成孤儿行。开启该 PRAGMA 后，SQLite 的行为与
    MySQL InnoDB 一致，测试环境才能真正反映生产语义。
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:  # pragma: no cover
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def get_engine(settings: Optional[Settings] = None) -> Engine:
    """获取（惰性创建）全局引擎。"""
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        _engine = _build_engine(cfg)
        logger.info("数据库引擎已创建：%s", _safe_url(cfg.database_url))
    return _engine


def get_session_factory(settings: Optional[Settings] = None) -> sessionmaker:
    """获取（惰性创建）全局会话工厂。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(settings),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _session_factory


def _safe_url(url: str) -> str:
    """隐藏连接串中的密码，便于安全地打日志。"""
    if "@" not in url:
        return url
    head, tail = url.split("@", 1)
    if ":" in head:
        scheme_user = head.rsplit(":", 1)[0]
        return f"{scheme_user}:***@{tail}"
    return url


def reset_engine() -> None:
    """释放并清空全局引擎（测试/配置热更新时使用）。"""
    global _engine, _session_factory
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:  # pragma: no cover - dispose 失败不影响后续
            logger.warning("引擎释放异常", exc_info=True)
    _engine = None
    _session_factory = None


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每请求一个会话，结束自动关闭。"""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """事务上下文：正常提交，异常回滚。

    用法::

        with session_scope() as db:
            db.add(obj)
    """
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(drop: bool = False) -> None:
    """建表（测试与本地快速初始化用；生产请执行 sql/01_schema.sql）。

    :param drop: 为 True 时先删除所有表。
    """
    engine = get_engine()
    try:
        if drop:
            Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        logger.info("数据表初始化完成，drop=%s", drop)
    except Exception as exc:  # pragma: no cover - 依赖真实数据库
        raise DatabaseError(f"初始化数据表失败：{exc}") from exc


def check_connection() -> Dict[str, Any]:
    """健康检查：返回数据库连通性与版本信息。"""
    info: Dict[str, Any] = {"connected": False, "dialect": "", "version": ""}
    try:
        engine = get_engine()
        info["dialect"] = engine.dialect.name
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if engine.dialect.name == "mysql":
                row = conn.execute(text("SELECT VERSION()")).scalar()
                info["version"] = str(row)
        info["connected"] = True
    except Exception as exc:
        info["error"] = str(exc)
        logger.warning("数据库健康检查失败：%s", exc)
    return info


__all__ = [
    "get_engine",
    "get_session_factory",
    "get_db",
    "session_scope",
    "init_db",
    "reset_engine",
    "check_connection",
    "Session",
    "SessionLocal",
]


def __getattr__(name: str) -> Any:
    """支持 ``from app.db.session import SessionLocal`` 的惰性访问。"""
    if name == "SessionLocal":
        return get_session_factory()
    raise AttributeError(name)
