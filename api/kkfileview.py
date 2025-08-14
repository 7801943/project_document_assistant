# --- KKFileView 代理路由 ---

import base64
import urllib.parse

import uuid
import os
from typing import Optional, List, cast, Union
from urllib.parse import quote
import httpx
from fastapi import (APIRouter, Depends, HTTPException, Request, WebSocket,
                    Form,status)
from starlette.datastructures import UploadFile
from fastapi.responses import (HTMLResponse, Response, FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from loguru import logger
from starlette.background import BackgroundTask
from core.data_model import ProjectUploadForm, SpecUploadForm # 导入新模型

from core.data_model import UploadType
from config import settings
from core import app_state
from core.auth import get_current_user, get_current_verified_user


router = APIRouter()

@router.get("/kkfileview/onlinePreview", summary="KKFileView 预览编码代理")
async def kkfileview_preview_encoder_proxy(request: Request, file_url: str, user: str = Depends(get_current_verified_user)):

    logger.debug(f"用户 '{user}' 请求预览文件，原始 kkFileView 目标 URL (来自前端): {file_url}")
    if not file_url:
        logger.warning("kkFileView 预览请求缺少 'file_url' 参数。")
        raise HTTPException(status_code=400, detail="缺少 'file_url' 参数。")
    if not app_state.http_client:
        logger.error("HTTPX 客户端未初始化，无法代理 kkFileView 请求。")
        raise HTTPException(status_code=503, detail="服务尚未准备就绪 (HTTPX Client missing)")
    try:
        # for kkfileview 必须有一个不同的文件名，否则就会使用缓存，导致不同路径同文件名预览的是同一个文
        # 使用 UUID 生成 8 位 token（从 uuid4 中截取前8位
        token = uuid.uuid4().hex[:8]  # hex 是32位小写十六进制字符
        # 修改文件名加上 token
        parsed_url = urllib.parse.urlparse(file_url)
        path_parts = parsed_url.path.split('/')
        filename = path_parts[-1]
        name, ext = os.path.splitext(filename)
        new_filename = f"{name}_{token}{ext}"
        path_parts[-1] = new_filename
        new_path = '/'.join(path_parts)

        # 构造新 URL
        new_url = parsed_url._replace(path=new_path)
        file_url = urllib.parse.urlunparse(new_url)
        # ----------------------------------------------------------------------------------


        base64_encoded_url = base64.b64encode(file_url.encode('utf-8')).decode('utf-8')
        final_encoded_url_for_kk = quote(base64_encoded_url)

        # 转发url来自kkfileview文档
        # 在4.3 版本中支持 forceUpdatedCache=True 强制更新缓存 ，
        # 即f"{settings.KKFILEVIEW_BASE_URL}/onlinePreview?forceUpdatedCache=True?url={final_encoded_url_for_kk}
        kk_target_url = f"{settings.KKFILEVIEW_BASE_URL}/onlinePreview?url={final_encoded_url_for_kk}"
        # logger.debug(f"[KKFileView 编码代理] 用户 '{user}' 编码并转发至: {kk_target_url}")
        headers_to_forward = {"host": httpx.URL(kk_target_url).netloc.decode('ascii')}
        proxy_req = app_state.http_client.build_request("GET", kk_target_url, headers=headers_to_forward)
        response = await app_state.http_client.send(proxy_req, stream=True)
        return StreamingResponse(response.aiter_bytes(), status_code=response.status_code, headers=response.headers, background=BackgroundTask(response.aclose))
    except httpx.RequestError as e:
        logger.error(f"代理 kkFileView 预览请求失败 (用户 '{user}', 原始目标 URL: {file_url}): {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"kkFileView 代理请求失败: {e}")
    except Exception as e:
        logger.error(f"处理 kkFileView 预览请求时发生未知错误 (用户 '{user}', 原始目标 URL: {file_url}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="处理预览请求时发生内部错误")

@router.api_route("/kkfileview/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], summary="KKFileView 通用资源代理")
async def kkfileview_generic_proxy(request: Request, full_path: str, user: Optional[str] = Depends(get_current_user)):
    """
    kkfileview 资源转发，route路径依赖于kkfile的配置参数
    """
    if not app_state.http_client:
        logger.error("HTTPX 客户端未初始化，无法代理 kkFileView 静态资源请求。")
        raise HTTPException(status_code=503, detail="服务尚未准备就绪 (HTTPX Client missing)")
    target_url = httpx.URL(url=f"{settings.KKFILEVIEW_BASE_URL}/{full_path}", query=str(request.query_params).encode("utf-8"))
    # logger.debug(f"[KKFileView 通用代理] 用户 '{user or '匿名'}' 转发静态资源: {target_url}")
    try:
        headers_to_forward = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'cookie', 'authorization', 'connection', 'upgrade-insecure-requests']}
        headers_to_forward["host"] = target_url.netloc.decode('ascii')
        proxy_req = app_state.http_client.build_request(method=request.method, url=target_url, headers=headers_to_forward, content=await request.body())
        response = await app_state.http_client.send(proxy_req, stream=True)
        return StreamingResponse(response.aiter_bytes(), status_code=response.status_code, headers=response.headers, background=BackgroundTask(response.aclose))
    except httpx.RequestError as e:
        logger.error(f"代理 kkFileView 静态资源请求失败 (用户 '{user or '匿名'}', Path: {full_path}): {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"kkFileView 代理请求失败: {e}")
    except Exception as e:
        logger.error(f"处理 kkFileView 静态资源请求时发生未知错误 (用户 '{user or '匿名'}', Path: {full_path}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="处理静态资源请求时发生内部错误")
