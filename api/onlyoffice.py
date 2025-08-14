
import uuid
from typing import Optional, List, cast, Union
from pathlib import Path
from urllib.parse import quote
import shutil
import httpx
import json
import os
import tempfile
from fastapi import (APIRouter, Depends, HTTPException, Request, WebSocket,
                    Form,status)
from fastapi.responses import (HTMLResponse, Response, FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from loguru import logger
from config import settings
from core import app_state
from utils.utils import get_host_ipv6_addr
import jwt # 使用 pyjwt
from core.auth import get_current_user, get_current_verified_user, verify_active_session

from core.data_model import DocType

router = APIRouter()

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
    if filepath and token:
        # --- 协同编辑流程 ---
        just_the_filename = Path(filepath).name
        file_ext, document_type = get_document_type(just_the_filename)

         # 1. 注册编辑文件，获取协同编辑所需的 user_id 和 file_key
        if app_state.session_manager:
            user_id, file_key = await app_state.session_manager.register_editing_file(user, filepath, DocType.PROJECT)
        else:
            return HTMLResponse("无法获取用户会话，请稍后重试。", status_code=500)

        if not user_id or not file_key:
            logger.error(f"为用户 '{user}' 注册文件 '{filepath}' 失败。")
            return HTMLResponse("无法初始化编辑会话，请稍后重试。", status_code=500)

        logger.info(f"OnlyOffice协同配置: user='{user}', user_id='{user_id}', file_key='{file_key}', title='{just_the_filename}'")

        # 2. 构建OnlyOffice配置
        ONLYOFFICE_SECRET = settings.ONLYOFFICE_JWT_SECRET.get_secret_value()
        final_js_config = {
            "document": {
                "fileType": file_ext,
                "key": file_key,  # 使用共享的 file_key
                "title": just_the_filename,
                "url": f"http://host.docker.internal:8888/download/{token}/{just_the_filename}",
                "permissions": {
                    "edit": True,
                    "download": True,
                    "comment": True, # 允许多人评论
                }
            },
            "documentType": document_type,
            "editorConfig": {
                "callbackUrl": "http://host.docker.internal:8888/onlyoffice/callback",
                "user": {
                    "id": user_id,  # 使用唯一的 user_id
                    "name": user
                },
                "customization": {
                    "autosave": True,
                    "forcesave": True, # 强制保存以触发回调
                    "close": {
                        "visible": True,
                        "text": "关闭文档"
                    }
                },
                "lang": 'zh-CN'
            },
            "events": {
                "onRequestClose": "function() { window.close(); }"
            }
        }
        # 3. 生成JWT令牌
        jwt_token = jwt.encode(final_js_config, ONLYOFFICE_SECRET, algorithm="HS256")
        final_js_config['token'] = jwt_token

    else:
        logger.error(f"无效的下载token '{token}' 或文件路径 '{filepath}'")
        return HTMLResponse(f"错误: 无效的token或文件路径", status_code=400)

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


# OnlyOffice 编辑完成后回调（测试用）
@router.post("/onlyoffice/callback")
async def onlyoffice_callback(request: Request):
    logger.debug("接收到OnlyOffice回调请求...")
    try:
        data = await request.json()
    except json.JSONDecodeError:
        logger.error("回调请求体不是有效的JSON")
        return JSONResponse({"error": 1, "message": "Invalid JSON body"}, status_code=400)

    status = data.get("status")
    download_url = data.get("url")
    file_key = data.get("key")

    logger.debug(f"回调数据: status={status}, file_key='{file_key}', url='{download_url}'")

    # status=2: 文档已准备好保存
    # status=6: 强制保存文档
    if status not in [2, 6]:
        logger.debug(f"状态码为 {status}，非保存操作，忽略。")
        return JSONResponse({"error": 0})

    if not download_url or not file_key:
        logger.error(f"回调缺少关键信息: download_url或file_key为空。")
        return JSONResponse({"error": 1, "message": "Missing download URL or file key"}, status_code=400)

    # 根据file_key获取原始文件路径
    # 注意：此时可能有多个用户在编辑，但他们共享同一个file_key，路径也应该相同
    if not app_state.session_manager:
        logger.error(f"错误 app_state.session_manager 为空。")
        return JSONResponse({"error": 0})

    path = await app_state.session_manager.get_editing_file(file_key)
    if not path:
        logger.error(f"根据file_key '{file_key}' 未找到对应的文件路径，无法保存。")
        return JSONResponse({"error": 1, "message": "File key not found or expired"}, status_code=404)

    TARGET_SAVE_PATH = settings.PROJECTS_ROOT_DIR / path
    logger.info(f"准备将文件保存到: {TARGET_SAVE_PATH}")

    temp_file_path = None # 初始化变量
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(download_url, timeout=30.0)
            response.raise_for_status()

            # 使用原子化写入，先写入临时文件，成功后再移动
            with tempfile.NamedTemporaryFile(dir=TARGET_SAVE_PATH.parent, delete=False) as tmp:
                tmp.write(response.content)
                temp_file_path = tmp.name

            shutil.move(temp_file_path, TARGET_SAVE_PATH)
            logger.success(f"文档 '{TARGET_SAVE_PATH.name}' 已通过回调成功保存。")

            # 可选：当文档最终保存后，可以考虑清理所有与该file_key相关的编辑状态
            # await app_state.session_manager.remove_edited_file(file_key)
            # 当前保留状态，以便后续用户继续编辑

            return JSONResponse({"error": 0})
    except httpx.RequestError as e:
        logger.exception(f"从OnlyOffice下载文件失败: {e}")
        return JSONResponse({"error": 1, "message": f"Failed to download file from OnlyOffice: {e}"}, status_code=500)
    except Exception as e:
        logger.exception(f"保存文档到 '{TARGET_SAVE_PATH}' 时发生未知错误: {e}")
        # 确保临时文件被清理
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        return JSONResponse({"error": 1, "message": f"An unexpected error occurred during save: {e}"}, status_code=500)
