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
import mimetypes
from fastapi import (APIRouter, Depends, HTTPException, Request, WebSocket,
                    Form,status)
from fastapi.responses import (HTMLResponse, Response, FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)


from loguru import logger
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketState

from config import settings
from core import app_state
from sse_proxy.sse2websocket import SSEWebSocketProxy
from sse_proxy.sse2websocket1 import OpenAIWebSocketProxy
from utils.utils import get_host_ipv6_addr
import jwt # 使用 pyjwt
from datetime import datetime, timedelta
from core.auth import get_current_user, get_current_verified_user, verify_active_session
from pydantic import BaseModel # 导入 BaseModel

# 定义请求体模型
class ProjectSearchRequest(BaseModel):
    project_name: str # project_name 不能为空
    project_year: Optional[str] = None # project_year 可以为空


router = APIRouter()


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
    # 2025-6-26 如果有环境变量USERS_DB_JSON存在，会导致覆盖.env中的配置
    user_db_entry = settings.USERS_DB.get(username)
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
    # logger.debug(f"接收到下载请求，Token: '{token}', URL中的文件名: '{filename_in_path}'")
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

        # 自动获取 MIME 类型
    mime_type, _ = mimetypes.guess_type(file_path_to_serve_str)
    if not mime_type:
        mime_type = "application/octet-stream"  # 默认二进制流

    logger.debug(f"Token '{token}' 验证成功。准备下载文件: '{actual_filename_to_serve}' (路径: '{file_path_to_serve_str}', URL文件名: '{filename_in_path}')")
    return FileResponse(file_path_to_serve, media_type=mime_type, filename=actual_filename_to_serve)


@router.get("/spec_images/{image_name:path}")
async def get_spec_image(image_name: str, user: str = Depends(get_current_verified_user)):
    """
    根据图片文件名从规范文件数据库中检索并返回图片。
    """
    if not app_state.document_service:
        raise HTTPException(status_code=503, detail="服务未就绪。")

    logger.debug(f"收到图片请求: {image_name}")
    
    # 在数据库中查找文件
    search_results = await app_state.document_service.find_documents(
        document_type='规范文件',
        file_name=image_name
    )

    if not search_results:
        logger.warning(f"在规范文件库中未找到图片: {image_name}")
        raise HTTPException(status_code=404, detail="Image not found in spec database")

    # 获取第一个匹配项的相对路径
    # 注意：这里假设文件名是唯一的。如果存在同名图片，将返回第一个找到的。
    file_to_serve = search_results[0]
    relative_path = file_to_serve.get("relative_path")

    if not relative_path:
        logger.error(f"数据库记录 {file_to_serve} 缺少 'relative_path' 字段。")
        raise HTTPException(status_code=500, detail="Internal server error: malformed file record")

    # 构建绝对路径
    absolute_path = settings.SPEC_ROOT_DIR / relative_path
    
    if not absolute_path.is_file():
        logger.error(f"数据库指向的文件不存在: {absolute_path}")
        raise HTTPException(status_code=404, detail="File not found on disk")

    # 自动获取 MIME 类型
    mime_type, _ = mimetypes.guess_type(absolute_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    logger.info(f"成功找到并返回图片: {absolute_path}")
    return FileResponse(absolute_path, media_type=mime_type)


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


# @router.get("/images/{file}")
# async def query_spec_images(user: str = Depends(get_current_verified_user)):
#     '''
#     获取规范md文件的图片
#     '''
#     file_path_to_server = ""
#     document_service = app_state.document_service
#     return FileResponse(file_path_to_serve, media_type=mime_type, filename=actual_filename_to_serve)

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

@router.post("/api/projects/search")
async def search_project_files(
    request_body: ProjectSearchRequest, # 使用请求体模型
    user: str = Depends(get_current_verified_user)
):
    """
    搜索项目文件。
    - project_name: 模糊搜索项目名称 (必需)。
    - project_year: 精确过滤项目年份 (可选，为空则查询所有年份)。
    """
    if not app_state.document_service or not app_state.session_manager:
        raise HTTPException(status_code=503, detail="服务未就绪。")

    project_name_query = request_body.project_name
    project_year_query = request_body.project_year

    if not project_name_query:
        raise HTTPException(status_code=400, detail="项目名称不能为空。")

    try:
        # 调试日志：检查WebSocket连接状态
        user_data = await app_state.session_manager.get_user_data(user)
        if user_data:
            logger.debug(f"在 /api/projects/search 中，用户 '{user}' 的WebSocket连接状态: connected={user_data.is_websocket_connected}, websocket_obj_exists={user_data.websocket is not None}")
        else:
            logger.warning(f"在 /api/projects/search 中，未找到用户 '{user}' 的会话数据。")

        # 构建查询参数
        find_params = {
            'document_type': '项目文件',
            'project_name': f'%{project_name_query}%' # 模糊搜索
        }
        if project_year_query:
            find_params['year'] = project_year_query # 精确过滤年份

        search_results = await app_state.document_service.find_documents(**find_params)
        logger.debug(f"find_params={find_params}, find_document nums:{len(search_results)}")
        unique_projects = {}
        for file_dict in search_results:
            meta_str = file_dict.get("metadata", "{}")
            try:
                meta = json.loads(meta_str)
                project_year = meta.get('year')
                project_name = meta.get('project_name')
                project_identifier = (project_year, project_name)
                if project_identifier not in unique_projects:
                    unique_projects[project_identifier] = {
                        "year": project_year,
                        "project_name": project_name
                        }
            except (json.JSONDecodeError, TypeError):
                continue

        project_list = list(unique_projects.values())

        if len(project_list) > 1:
            # 返回多个项目时，返回项目列表和状态
            logger.info(f"len(project_list) > 1 内容：{project_list}")
            return JSONResponse(content={"status": "multiple_projects", "projects": project_list})
        elif len(project_list) == 1:
            # 查询到唯一项目，返回项目信息和状态
            logger.info("len(project_list) == 1")
            selected_project = project_list[0]
            project_files = await app_state.document_service.find_documents(
                document_type='项目文件',
                project_name=selected_project['project_name']
            )
            # logger.debug(f"查询到的项目文件：{project_files}")
            # 构造文件路径列表，并确保类型为str
            file_paths = [str(f.get("relative_path")) for f in project_files if f.get("relative_path")]

            # 构造目录路径
            dir_path = f"{selected_project['year']}/{selected_project['project_name']}"
            logger.debug(f"即将为用户 '{user}' 更新工作目录 '{dir_path}'，包含 {len(file_paths)} 个文件。")

            # 使用新的签名调用 session.update_opened_dir
            await app_state.session_manager.update_opened_dir(user, dir_path, file_paths)

            return JSONResponse(content={"status": "single_project", "project": selected_project}, status_code=200)
        else:
            # 没有找到项目时，返回状态
            return JSONResponse(content={"status": "no_project_found"})

    except Exception as e:
        logger.error(f"处理项目文件请求时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")
