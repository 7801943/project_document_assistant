
import base64
import urllib.parse
import time
import uuid
import os
from typing import Optional, List, cast, Union
from pathlib import Path
from urllib.parse import quote
import shutil
import httpx
import json
import tempfile
from fastapi import (APIRouter, Depends, HTTPException, Request, WebSocket,
                    Form,status)
from starlette.datastructures import UploadFile

from fastapi.responses import (HTMLResponse, Response, FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)


from loguru import logger
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketState

from core.data_model import ProjectUploadForm, SpecUploadForm # 导入新模型

from core.data_model import UploadType
from config import settings
from core import app_state
from sse_proxy.sse2websocket import SSEWebSocketProxy
from sse_proxy.sse2websocket1 import OpenAIWebSocketProxy
from utils.utils import get_host_ipv6_addr
import jwt # 使用 pyjwt
from datetime import datetime, timedelta

router = APIRouter()


# --- 认证依赖 (从 main.py 移动过来) ---
async def get_current_user(request: Request) -> Optional[str]:
    '''
    获取用户名，会话id
    '''
    user = request.session.get("user")
    session_id = request.session.get("session_id")
    if not user or not session_id:
        return None
    return user

async def verify_active_session(request: Request) -> str:
    username = request.session.get("user")
    http_session_id = request.session.get("session_id")
    if not username or not http_session_id:
        logger.debug("verify_active_session: HTTP session中缺少用户名或session_id。")
        raise HTTPException(status_code=401, detail="用户未登录或会话标识无效")
    if not app_state.session_manager:
        logger.error("verify_active_session: SessionManager 未初始化。")
        raise HTTPException(status_code=500, detail="服务器内部错误 (SessionManager missing)")
    user_data = await app_state.session_manager.get_user_data(username)
    if not user_data:
        logger.warning(f"verify_active_session: 用户 '{username}'"
                       "在SessionStateManager中未找到。可能是会话已过期被清理。")
        request.session.clear()
        raise HTTPException(status_code=401, detail="会话已过期或无效，请重新登录 (user not in SM)")
    if user_data.session_id != http_session_id:
        logger.warning(f"verify_active_session: 用户 '{username}' 的HTTP session_id ({http_session_id})"
                       "与SessionStateManager中的 ({user_data.session_id}) 不匹配。")
        request.session.clear()
        raise HTTPException(status_code=401, detail="会话冲突或已失效，请重新登录 (session_id mismatch)")
    current_time = time.time()
    if (current_time - user_data.last_activity_time) >= settings.SESSION_OVERALL_INACTIVITY_TIMEOUT_SECONDS:
        logger.info(f"verify_active_session: 用户 '{username}' (会话 {user_data.session_id})"
                    "因HTTP不活动超时 (最后活动: {user_data.last_activity_time_str})。")
        await app_state.session_manager.logout_user(username)
        request.session.clear()
        raise HTTPException(status_code=401, detail="会话因长时间不活动已过期，请重新登录")
    await app_state.session_manager.set_http_activity(username)
    logger.trace(f"verify_active_session: 用户 '{username}' 会话有效，活动时间已更新。")
    return username

async def get_current_verified_user(user: str = Depends(verify_active_session)) -> str:
    return user

# --- 登录/注销路由 ---
@router.post("/login")
async def login(request: Request):
    form = await request.form()
    username_val = form.get("username")
    password_val = form.get("password")
    client_ip = request.client.host if request.client else "unknown"

    # 增加类型检查，确保收到的值是字符串
    if not isinstance(username_val, str) or not isinstance(password_val, str):
        logger.warning(f"登录尝试失败: 表单字段类型不正确 (来自 IP: {client_ip})")
        return JSONResponse({"status": "error", "message": "无效的登录请求"}, status_code=400)

    username = username_val
    password = password_val

    if not username or not password:
        logger.warning(f"登录尝试失败: 缺少用户名或密码 (来自 IP: {client_ip})")
        return JSONResponse({"status": "error", "message": "请输入用户名和密码"}, status_code=400)
    # 2025-6-26 如果有环境变量FAKE_USERS_DB_JSON存在，会导致覆盖.env中的配置
    user_db_entry = settings.FAKE_USERS_DB.get(username)
    if not user_db_entry or user_db_entry["password"] != password:
        logger.warning(f"登录尝试失败: 用户名或密码错误 for '{username},{password} user_db_entry:{user_db_entry}' (来自 IP: {client_ip})")
        return JSONResponse({"status": "error", "message": "用户名或密码错误"}, status_code=401)
    if not app_state.session_manager:
        logger.error("SessionManager 未初始化，无法处理登录。")
        return JSONResponse({"status": "error", "message": "服务器内部错误，请稍后再试"}, status_code=500)
    if "session_id" not in request.session or not request.session["session_id"]:
        request.session["session_id"] = str(uuid.uuid4())
    current_session_id = request.session["session_id"]
    login_successful = await app_state.session_manager.attempt_login(username, client_ip, current_session_id)
    if login_successful:
        request.session["user"] = username
        logger.info(f"用户 '{username}' (IP: {client_ip}, Session: {current_session_id}) 登录成功。")
        return JSONResponse({"status": "ok", "message": "登录成功"})
    else:
        logger.warning(f"登录尝试失败: 用户 '{username}' (IP: {client_ip}) 因排他性登录控制被拒绝。")
        return JSONResponse({"status": "error", "message": "用户已在其他地方登录或活动，请先登出。"}, status_code=409)

@router.get("/logout")
async def logout(request: Request, user: Optional[str] = Depends(get_current_user)):
    if user and app_state.session_manager:
        await app_state.session_manager.logout_user(user)
    request.session.clear()
    logger.info(f"HTTP会话已清除 (用户: {user or '未知'})。")
    return RedirectResponse(url="/static/login.html", status_code=302)

# --- 主页路由 ---
@router.get("/")
async def read_root(user: Optional[str] = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/static/login.html", status_code=302)
    return FileResponse("frontend/index7.html")

@router.get("/frontend/login.html")
async def serve_login_page():
    return FileResponse("frontend/login.html")


@router.get("/api/user/status")
async def user_status(request: Request, user: str = Depends(get_current_verified_user)):
    session_id_from_http_session = request.session.get("session_id")
    if not session_id_from_http_session:
        logger.error(f"用户 '{user}' 通过验证后，HTTP session中仍未找到session_id。这是一个异常情况。")
        raise HTTPException(status_code=500, detail="内部会话错误 (missing session_id post-verification)")
    return JSONResponse({"username": user, "session_id": session_id_from_http_session})

@router.get("/api/dify-agent-api")
async def get_dify_agent_api(user: str = Depends(get_current_verified_user)):
    full_url = f"http://[{get_host_ipv6_addr()}]{settings.DIFY_AGENT_BASE_URL}"
    api_key = settings.DIFY_AGENT_APIKEY.get_secret_value()
    return JSONResponse({"url": full_url,"apikey": api_key})

@router.get("/api/upload-info", summary="获取上传所需的规程分类")
async def get_upload_info(user: str = Depends(get_current_verified_user)):
    """
    提供在.env文件中配置的规程专业目录列表。
    HTML表单现在由前端作为静态文件直接加载。
    """
    try:
        # 获取规程专业目录列表
        categories = settings.SPEC_DIRS
        return JSONResponse(content={"categories": categories})
    except Exception as e:
        logger.error(f"获取规程分类时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误，无法加载规程分类。")

# dify agent interface
@router.websocket("/ws_chat_stream")
async def websocket_chat_endpoint(websocket: WebSocket):
    '''
    websocket聊天和实时指令端点 (Dify-Agent)
    '''
    session_id = websocket.query_params.get("session_id")

    if not session_id:
        await websocket.accept()
        await websocket.close(code=1008, reason="session_id is required.")
        return

    if not app_state.session_manager:
        await websocket.accept()
        await websocket.close(code=1011, reason="Server not ready.")
        return

    username = await app_state.session_manager.get_username_by_session_id(session_id)
    if not username:
        await websocket.accept()
        await websocket.close(code=1008, reason="Invalid or expired session_id.")
        return

    # 将连接请求委托给 SessionStateManager
    await app_state.session_manager.connect_websocket(websocket, username, session_id)

    # 只有在 connect_websocket 成功 (即没有关闭连接) 后才继续
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            # 实例化转发代理
            proxy = SSEWebSocketProxy(
                websocket=websocket,
                upstream_url=str(settings.UPSTREAM_CHAT_URL),
                headers={"Authorization": f"Bearer {settings.DIFY_AGENT_APIKEY.get_secret_value()}", "Content-Type": "application/json"},
                username=username,
                session_id=session_id,
            )
            await proxy.run()
        finally:
            # 确保无论如何都调用断开连接的逻辑
            await app_state.session_manager.disconnect_websocket(username)


# openai completion stream interface
@router.websocket("/ws/v2/chat")
async def websocket_chat_endpoint_v2(websocket: WebSocket):
    '''
    websocket聊天和实时指令端点 (OpenAI-Compatible)
    '''
    session_id = websocket.query_params.get("session_id")

    if not session_id:
        await websocket.accept()
        await websocket.close(code=1008, reason="session_id is required.")
        return

    if not app_state.session_manager:
        await websocket.accept()
        await websocket.close(code=1011, reason="Server not ready.")
        return

    username = await app_state.session_manager.get_username_by_session_id(session_id)
    if not username:
        await websocket.accept()
        await websocket.close(code=1008, reason="Invalid or expired session_id.")
        return

    # 将连接请求委托给 SessionStateManager
    await app_state.session_manager.connect_websocket(websocket, username, session_id)

    # 只有在 connect_websocket 成功 (即没有关闭连接) 后才继续
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            # 实例化新的OpenAI转发代理
            proxy = OpenAIWebSocketProxy(
                websocket=websocket,
                username=username,
                session_id=session_id,system_prompt= settings.SYSTEM_PROMPT
            )
            await proxy.run()
            await proxy._save_history_to_file()
        finally:
            # 确保无论如何都调用断开连接的逻辑
            await app_state.session_manager.disconnect_websocket(username)


@router.get("/download/{token}/{filename_in_path:path}")
async def download_file_via_token(token: str, filename_in_path: str, request: Request):
    """
    下载端点，仅检查token，文件名是转发给kkfileview所需要的。
    """
    logger.debug(f"接收到下载请求，Token: '{token}', URL中的文件名: '{filename_in_path}'")
    if not app_state.session_manager:
        logger.error("SessionManager 未初始化，无法处理下载请求。")
        raise HTTPException(status_code=503, detail="服务暂时不可用 (SessionManager mfrom typing import castissing)")
    file_info = await app_state.session_manager.get_downloadable_file_info(token)
    if not file_info:
        logger.warning(f"下载token '{token}' 无效、未找到或已过期。")
        raise HTTPException(status_code=404, detail="下载链接无效、已过期或文件未找到。")

    file_path_to_serve_str = file_info.get("absolute_path")
    actual_filename_to_serve = file_info.get("filename")

    # 检查文件路径可用性
    if not file_path_to_serve_str or not actual_filename_to_serve:
        logger.error(f"Token '{token}' 关联的文件信息不完整。数据: {file_info}")
        raise HTTPException(status_code=500, detail="服务器内部错误，token数据不完整。")
    if filename_in_path != actual_filename_to_serve:
        logger.warning(f"下载请求中URL文件名 '{filename_in_path}' 与Token关联文件名 '{actual_filename_to_serve}' 不匹配。将使用Token关联文件名。")
    file_path_to_serve = Path(file_path_to_serve_str)
    if not file_path_to_serve.exists() or not file_path_to_serve.is_file():
        logger.error(f"Token '{token}' 指向的文件路径不存在或不是文件: '{file_path_to_serve_str}' (关联文件名: {actual_filename_to_serve})")
        raise HTTPException(status_code=404, detail="服务器上的文件未找到 (file missing on server)。")

    logger.debug(f"Token '{token}' 验证成功。准备下载文件: '{actual_filename_to_serve}' (路径: '{file_path_to_serve_str}', URL文件名: '{filename_in_path}')")
    return FileResponse(file_path_to_serve, media_type='application/octet-stream', filename=actual_filename_to_serve)

# --- 新增调试接口 ---
@router.get("/debug/session-states")
async def debug_get_session_states(request: Request):
    if not app_state.session_manager:
        logger.error("SessionManager 未初始化，无法获取调试信息。")
        raise HTTPException(status_code=500, detail="SessionManager未初始化")
    client_ip = request.client.host if request.client else "unknown"
    logger.info(f"调试接口 /debug/session-states 被访问 (来自 IP: {client_ip})。")
    all_states = await app_state.session_manager.get_all_user_data_for_debug()
    return JSONResponse(content=all_states)


async def get_upload_form(request: Request) -> Union[SpecUploadForm, ProjectUploadForm]:
    """
    依赖项函数，根据 'upload_target' 字段来决定使用哪个表单模型。
    通过在实例化前进行类型检查和转换来解决类型安全问题。
    """
    form_data = await request.form()
    upload_target = form_data.get("upload_target", "spec")

    # 从表单安全地提取和转换通用字段
    try:
        upload_type_str = form_data.get("upload_type")
        if not upload_type_str or upload_type_str not in [item.value for item in UploadType]:
            raise HTTPException(status_code=400, detail="无效的 'upload_type' 值。")
        upload_type = UploadType(upload_type_str)


        overwrite = form_data.get("overwrite") == 'true'

        files_raw = form_data.getlist("files")
        if not all(isinstance(f, UploadFile) for f in files_raw):
             raise HTTPException(status_code=400, detail="无效的文件上传数据。")
        files = cast(List[UploadFile], files_raw)

        file_paths_raw = form_data.getlist("file_paths")
        if not all(isinstance(p, str) for p in file_paths_raw):
            raise HTTPException(status_code=400, detail="无效的文件路径数据。")
        file_paths = cast(List[str], file_paths_raw)

    except Exception as e:
        logger.error(f"解析通用上传字段时出错: {e}")
        raise HTTPException(status_code=400, detail=f"表单数据不完整或格式错误: {e}")


    if upload_target == "project":
        year = form_data.get("year")
        project_name = form_data.get("project_name")
        project_type = form_data.get("project_type")
        if not isinstance(year, str) or not isinstance(project_name, str) or not isinstance(project_type, str):
            raise HTTPException(status_code=400, detail="项目上传必须提供 'year'、'project_name' 和 'project_type'。")
        logger.debug(f"上传字段year:{year} project_name:{project_name} project_type:{project_type} upload_type:{upload_type} overwrite:{overwrite} files:{files} file_paths{file_paths}")
        return ProjectUploadForm(
            year=year,
            project_name=project_name,
            project_type=project_type,
            upload_type=upload_type,
            overwrite=overwrite,
            files=files,
            file_paths=file_paths
        )
    else: # 默认为规程上传
        category = form_data.get("category")
        spec_name = form_data.get("spec_name")
        if not isinstance(category, str) or not isinstance(spec_name, str):
            raise HTTPException(status_code=400, detail="规程上传必须提供 'category' 和 'spec_name'。")

        return SpecUploadForm(
            category=category,
            spec_name=spec_name,
            upload_type=upload_type,
            overwrite=overwrite,
            files=files,
            file_paths=file_paths
        )


@router.post("/upload-directory/")
async def upload_directory(
    form: Union[SpecUploadForm, ProjectUploadForm] = Depends(get_upload_form),
    user: str = Depends(get_current_verified_user)
):
    """
    统一处理规程文件和项目文件的上传。
    通过检查form的实例类型来区分处理逻辑。
    """
    if isinstance(form, SpecUploadForm):
        return await handle_spec_upload(form, user)
    elif isinstance(form, ProjectUploadForm):
        return await handle_project_upload(form, user)
    else:
        raise HTTPException(status_code=400, detail="无法识别的上传表单类型。")


async def handle_spec_upload(form: SpecUploadForm, user: str):
    """处理规程文件上传的逻辑"""
    target_base = settings.SPEC_ROOT_DIR / form.category / form.spec_name
    logger.info(f"用户 '{user}' 开始上传规程 '{form.spec_name}'。目标: '{target_base}' (覆盖: {form.overwrite})。")

    if not app_state.spec_database:
        raise HTTPException(status_code=503, detail="规程数据库服务未就绪。")

    if target_base.exists() and not form.overwrite:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"message": f"规程 '{form.category}/{form.spec_name}' 已存在。"}
        )

    try:
        if target_base.exists() and form.overwrite:
            logger.info(f"覆盖规程: 删除旧目录 '{target_base}'...")
            await app_state.spec_database.remove_spec_directory(form.category, form.spec_name)
            shutil.rmtree(target_base)

        target_base.mkdir(parents=True, exist_ok=True)
        await save_files_to_disk(form.files, form.file_paths, target_base, form.spec_name if form.upload_type.value == "directory" else None)

        logger.info(f"规程文件已上传至 '{target_base}'。开始索引...")
        await app_state.spec_database.add_spec_directory(target_base)

        return JSONResponse({
            "message": f"规程 '{form.category}/{form.spec_name}' 上传并索引成功。",
            "file_count": len(form.files)
        })
    except Exception as e:
        logger.error(f"上传规程 '{form.category}/{form.spec_name}' 失败: {e}", exc_info=True)
        # 清理逻辑
        if target_base.exists():
            shutil.rmtree(target_base, ignore_errors=True)
        await app_state.spec_database.remove_spec_directory(form.category, form.spec_name)
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


async def handle_project_upload(form: ProjectUploadForm, user: str):
    """处理项目文件上传的逻辑"""
    target_base = settings.PROJECTS_ROOT_DIR / form.year / form.project_name / form.project_type
    logger.info(f"用户 '{user}' 开始上传项目 '{form.project_name}'。目标: '{target_base}' (覆盖: {form.overwrite})。")

    if not app_state.project_database:
        raise HTTPException(status_code=503, detail="项目数据库服务未就绪。")

    if target_base.exists() and not form.overwrite:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"message": f"项目 '{form.year}/{form.project_name}/{form.project_type}' 已存在。"}
        )

    try:
        if target_base.exists() and form.overwrite:
            logger.info(f"覆盖项目: 删除旧目录 '{target_base}'...")
            # 注意：项目数据库的删除逻辑可能不同，这里假设FileDatabaseWatcher会自动处理
            shutil.rmtree(target_base)

        target_base.mkdir(parents=True, exist_ok=True)
        await save_files_to_disk(form.files, form.file_paths, target_base, form.project_name if form.upload_type.value == "directory" else None)

        logger.info(f"项目文件已上传至 '{target_base}'。文件监视器将自动索引。")
        # FileDatabaseWatcher 会自动检测并处理新文件，无需手动调用索引

        return JSONResponse({
            "message": f"项目 '{form.project_name}' 上传成功。",
            "file_count": len(form.files)
        })
    except Exception as e:
        logger.error(f"上传项目 '{form.project_name}' 失败: {e}", exc_info=True)
        if target_base.exists():
            shutil.rmtree(target_base, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


async def save_files_to_disk(files: List[UploadFile], file_paths: List[str], target_base: Path, top_level_dir_name: Optional[str] = None):
    """通用函数，用于将上传的文件保存到磁盘"""
    for file, rel_path_str in zip(files, file_paths):
        if ".." in rel_path_str or Path(rel_path_str).is_absolute():
            logger.warning(f"检测到不安全的相对路径，已跳过: '{rel_path_str}'")
            continue

        rel_path = Path(rel_path_str)

        # 如果是目录上传，移除路径中重复的顶级目录部分
        if top_level_dir_name and rel_path.parts and rel_path.parts[0] == top_level_dir_name:
            rel_path = Path(*rel_path.parts[1:])

        dest_path = target_base / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(dest_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
        finally:
            file.file.close()


# 自定义静态文件服务 GET 接口
# @router.get("/docs/{filename}", response_class=FileResponse)
# async def get_document(filename: str):
#     '''
#     测试下载接口
#     '''

#     DOCUMENT_PATH = 'docs'
#     file_path = os.path.join(DOCUMENT_PATH, filename)
#     if not os.path.exists(file_path):
#         raise HTTPException(status_code=404, detail="File not found")
#     return FileResponse(path=file_path, filename=filename, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

# 生成 JWT token
# def generate_token(user, file_ext, document_type, document_key:str, download_token, document_name:str):
#     '''
#     测试onlyoffice jwt
#     '''
#     # ONLYOFFICE_SECRET = '6955cd123336471f917122f777e35f227f97d2c7bfa1f54612cbac3d73d11995'
#     ONLYOFFICE_SECRET = settings.ONLYOFFICE_JWT_SECRET.get_secret_value()
#     payload = {
#         "document": {
#             "fileType": file_ext,
#             "key": document_key,
#             "title": document_name,
#             "url": f"http://host.docker.internal:8888/download/{download_token}/{document_name}"
#         },
#         "editorConfig": {
#             "callbackUrl": "http://host.docker.internal:8888/onlyoffice/callback",
#             "user": {
#                 "id": "user-1",
#                 "name": user
#             },
#             "autosave": True,  # ✅ 启用自动保存
#             "customization": {
#                 "forcesave": True  # ✅ 启用保存按钮真正触发回调
#             },
#             "lang": 'zh-CN'
#         },
#         "permissions": {
#             "edit": True,
#             "download": True
#         },
#         "iat": int(time.time())
#     }

#     token = jwt.encode(payload, ONLYOFFICE_SECRET, algorithm="HS256")
#     return token

# 主页面：嵌入 OnlyOffice 编辑器
@router.get("/onlyoffice/editor", response_class=HTMLResponse)
async def open_document(
    request: Request,
    filepath: Optional[str] = None,
    token: Optional[str] = None,
    user: str = Depends(get_current_verified_user)
):
    """
    动态生成 OnlyOffice 编辑器页面。
    如果提供了 filepath 和 token，则为特定文件生成配置。
    否则，使用默认的 helloworld.docx 进行测试。
    """
    # 动态获取当前服务器的主机和端口
    def get_document_type(filename: str) -> tuple[str,str]:
        ext = filename.rsplit(".", 1)[-1].lower()
        # --- 文档说明 来自 https://api.onlyoffice.com/zh-CN/docs/docs-api/usage-api/config/---
        # 定义查看或编辑的源文档的文件类型。必须是小写。以下文件类型可用：
        # .csv、.djvu、.doc、.docm、.docx、.docxf、.dot、.dotm、.dotx、
        # .epub、.fb2、.fodp、.fods、.fodt、.htm、.html、.key、.mht、
        # .numbers、.odp、.ods、.odt、.oform、.otp、.ots、.ott、.oxps、
        # .pages、.pdf、.pot、.potm、.potx、.pps、.ppsm、.ppsx、.ppt、
        # .pptm、.pptx、.rtf、.txt、 .xls、.xlsb、.xlsm、.xlsx、.xlt、
        # .xltm、.xltx、.xml、.xps.
        if ext in ["doc", "docx", "docm", "dot", "dotm", "dotx", "epub", "fb2",
                "fodt", "htm", "html", "mht", "mhtml", "odt", "ott", "pages",
                "rtf", "stw", "sxw", "txt", "wps", "wpt", "xml"]:
            return ext, "word"
        elif ext in ["csv", "et", "ett", "fods", "numbers", "ods", "ots", "sxc",
                    "xls", "xlsb", "xlsm", "xlsx", "xlt", "xltm", "xltx", "xml"]:
            return ext, "cell"
        elif ext in ["dps", "dpt", "fodp", "key", "odp", "otp", "pot", "potm",
                    "potx", "pps", "ppsm", "ppsx", "ppt", "pptm", "pptx", "sxi"]:
            return ext, "slide"
        elif ext in ["djvu", "docxf", "oform", "oxps", "pdf", "xps"]:
            return ext, "pdf"
        else:
            return ext, "word"  # 默认兜底

    final_js_config = None
    # encoded_token = ""
    if filepath and token:
        # 生产流程：为指定文件生成配置
        just_the_filename = Path(filepath).name
        # file_ext = just_the_filename.split('.')[-1].lower()
        doc_key = uuid.uuid4().hex # 保证每次编辑的key是唯一的
        file_ext, document_type = get_document_type(just_the_filename)

        # 判断 documentType
        # encoded_token = generate_token(user, file_ext,document_type, doc_key, token, just_the_filename)
        logger.info(f"onlyoffice confg: filie_type:{document_type}, key:{doc_key},title:{just_the_filename}.")


        ONLYOFFICE_SECRET = settings.ONLYOFFICE_JWT_SECRET.get_secret_value()
        final_js_config = {
            "document": {
                "fileType": file_ext,
                "key": doc_key,
                "title": just_the_filename,
                "url": f"http://host.docker.internal:8888/download/{token}/{just_the_filename}",
                "permissions": {  # ✅ 权限应该在document下
                    "edit": True,
                    "download": True
                }
            },
            "documentType": document_type,
            "editorConfig": {
                "callbackUrl": "http://host.docker.internal:8888/onlyoffice/callback",
                "user": {
                    "id": "user-1",
                    "name": user
                },
                "customization": {
                    "autosave": True,  # ✅ 启用自动保存，似乎与下面是u冲突的，以后再排查
                    "forcesave": True,  # ✅ 启用保存按钮真正触发回调
                    "close": {  # ✅ 正确的close配置，显示关闭按钮
                        "visible": True,
                        "text": "关闭文档"
                    }
                },
                "lang": 'zh-CN'
            },
            # ✅ 必须添加events配置，否则关闭按钮不会显示！
            "events": {
                "onRequestClose": "function() { window.close(); }"
            }
        }
        token = jwt.encode(final_js_config, ONLYOFFICE_SECRET, algorithm="HS256")
        final_js_config['token'] = token
        # 注册编辑状态
        # filepath 是相对路径
        await app_state.session_manager.set_edited_file(user, doc_key, filepath)
    else:
        logger.error(f"无效的token{token}或filepath{filepath}")
        return HTMLResponse(f"error:无效的token{token}或filepath{filepath}")

    #  注意端口号为8080
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>OnlyOffice - {filepath}</title>
        <meta charset="utf-8">
        <script type="text/javascript" src="http://[{get_host_ipv6_addr()}]:8080/web-apps/apps/api/documents/api.js"></script>
        <style>
            html, body {{ margin: 0; padding: 0; height: 100%; overflow: hidden; }}
            #placeholder {{ width: 100%; height: 100%; }}
        </style>
    </head>
    <body>
        <div id="placeholder"></div>
        <script type="text/javascript">
            var config = {json.dumps(final_js_config)};
            function onRequestClose() {{
                docEditor.destroyEditor();
                document.getElementById("placeholder").innerHTML =
                "<div style='text-align:center;padding-top:40px;font-size:20px;color:#666;'>📄 文档已关闭</div>";
            }}

            config.events = {{
                onRequestClose: onRequestClose
            }};
            var docEditor = new DocsAPI.DocEditor("placeholder", config);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@router.get("/test_button_clicked")
async def test_button_clicked(user: str = Depends(get_current_verified_user)):
    '''
    测试端点
    '''
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession # Import ToolCallResult for type hinting if needed
    SERVER_URL = "http://localhost:8888/mcp/mcp" # <--- 更新端口号
    try:
        async with streamablehttp_client(SERVER_URL) as (read_stream, write_stream, _):
            print("成功连接到服务器并获取读写流。")

            async with ClientSession(read_stream, write_stream) as session:
                # async def query_project_files(user:str, project_name: str, year: Optional[str] = None) -> str:
                print("ClientSession 已创建。正在初始化会话...")
                await session.initialize()
                print("会话已初始化。")
                params = {}
                params["user"] = user
                params['project_name'] = "船闸"
                tool_name = "query_project_files"
                print(f"\n正在使用参数调用 '{tool_name}': {params}")
                call_result = await session.call_tool(tool_name, params)
                logger.debug(f"test_button_clicked 调用结果:{call_result}")

            print("ClientSession 已关闭。")
    except ConnectionRefusedError:
        print(f"错误: 无法连接到服务器 {SERVER_URL}。请确保服务器正在运行且路径正确。")
    except Exception as e:
        print(f"发生未知客户端错误: {e}")
    finally:
        print("交互式MCP客户端已关闭。")

# OnlyOffice 编辑完成后回调（测试用）
@router.post("/onlyoffice/callback")
async def onlyoffice_callback(request: Request):
    logger.debug(f"in.../onlyoffice/callback")
    data = await request.json()
    status = data.get("status")
    download_url = data.get("url")
    file_key = data.get("key")  # 可以用于识别文档
    # logger.info(f"接收到回调：status={status}, key={file_key}, url={download_url}")

    # 所有的文件都在项目路径中
    path = await app_state.session_manager.get_edited_file(file_key)
    if path is not "":
        TARGET_SAVE_PATH = settings.PROJECTS_ROOT_DIR / path
        logger.info(f"接收到回调：status={status}, key={file_key}, url={download_url}, TARGET_SAVE_PATH：{TARGET_SAVE_PATH}")
        if status in [2, 6] and download_url and file_key:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(download_url)
                    response.raise_for_status()

                    # 使用临时文件避免写入失败破坏原文件
                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        tmp.write(response.content)
                        temp_file_path = tmp.name

                    # 使用 shutil 覆盖原文件
                    shutil.move(temp_file_path, TARGET_SAVE_PATH)
                    logger.success(f"文档保存成功: {TARGET_SAVE_PATH}")
                    # 删除临时文件
                    # shutil.rmtree(temp_file_path)
                    await app_state.session_manager.remove_edited_file(file_key)
                    return JSONResponse({"error": 0})
            except Exception as e:
                logger.exception(f"文档保存失败: {e}")
                return JSONResponse({"error": 1, "message": str(e)})
        else:
            logger.info(f"状态码为 {status}，无需保存")
            return JSONResponse({"error": 0})
    else:
        logger.exception(f"返回路径为空,文件保存失败")
        return JSONResponse({"error": 1, "message": "未找到目标文件"})


# --- KKFileView 代理路由 ---
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
