"""文件上传入库接口（依赖 ``python-multipart``）。

单独成模块的原因
----------------
FastAPI 在**注册**带 ``File`` / ``Form`` 的路由时就会校验
``python-multipart`` 是否安装，缺失时直接抛 ``RuntimeError``。若把该路由写在
主模块里，一个可选依赖的缺失会导致**整个服务无法启动**。

因此这里把上传路由隔离到独立模块，由 :mod:`app.api.v1.router` 做能力探测后
决定是否挂载——未安装依赖时其余接口照常工作，并给出明确的安装提示。
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from app.api.deps import DbSession, KbService
from app.schemas.common import ApiResponse
from app.schemas.knowledge import IngestResultOut

router = APIRouter(prefix="/knowledge", tags=["知识库"])


@router.post(
    "/documents/upload",
    response_model=ApiResponse[IngestResultOut],
    summary="上传制度文件（txt/pdf/docx/图片）",
)
def upload_document(
    db: DbSession,
    service: KbService,
    file: UploadFile = File(..., description="制度文件"),
    source_url: str = Form(default="", description="公开来源链接"),
    replace: bool = Form(default=True, description="同文件是否覆盖旧版本"),
    force: bool = Form(default=False, description="内容未变是否强制入库"),
    keep_file: bool = Form(default=True, description="入库后是否保留上传文件"),
) -> ApiResponse[IngestResultOut]:
    """上传并入库制度文件。

    处理链路：上传落盘 -> 白名单与体积校验 -> 解析（图片/扫描版 PDF 走 OCR）
    -> 清洗 -> 元数据抽取 -> 条文切分 -> MySQL 落库 -> 向量化写入 Milvus。

    仅允许公开制度数据，禁止上传涉密或企业敏感材料。
    ``GET /knowledge/ocr/status`` 可查询 OCR 是否就绪。
    """
    result, _ = service.ingest_upload(
        db,
        file.file,
        file.filename or "upload",
        size=getattr(file, "size", 0) or 0,
        source_url=source_url,
        replace=replace,
        force=force,
        keep_file=keep_file,
    )
    return ApiResponse.ok(IngestResultOut(**result.to_dict()), message=result.message)


__all__ = ["router"]
