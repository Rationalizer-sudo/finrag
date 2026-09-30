"""FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated, Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.services.container import Container, get_container
from app.services.kb_service import KnowledgeBaseService
from app.services.qa_service import QaService


def get_settings_dep() -> Settings:
    """配置依赖。"""
    return get_settings()


def get_container_dep() -> Container:
    """依赖容器。"""
    return get_container()


def get_db_dep() -> Generator[Session, None, None]:
    """数据库会话依赖。"""
    yield from get_db()


def get_kb_service(
    container: Annotated[Container, Depends(get_container_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> KnowledgeBaseService:
    """知识库服务依赖。"""
    return KnowledgeBaseService(container, settings)


def get_qa_service(
    container: Annotated[Container, Depends(get_container_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> QaService:
    """问答服务依赖。"""
    return QaService(container, settings)


DbSession = Annotated[Session, Depends(get_db_dep)]
KbService = Annotated[KnowledgeBaseService, Depends(get_kb_service)]
QaServiceDep = Annotated[QaService, Depends(get_qa_service)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ContainerDep = Annotated[Container, Depends(get_container_dep)]

__all__ = [
    "get_settings_dep",
    "get_container_dep",
    "get_db_dep",
    "get_kb_service",
    "get_qa_service",
    "DbSession",
    "KbService",
    "QaServiceDep",
    "SettingsDep",
    "ContainerDep",
]
