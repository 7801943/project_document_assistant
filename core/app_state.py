# app_state.py
"""
一个中心化的模块，用于持有所有应用级别的共享状态实例。

这个文件只定义变量“占位符”，不进行任何复杂的实例化，以避免循环导入问题。
所有实例的实际赋值都在 server_v7.py 的 lifespan 管理器中进行。
"""

from typing import Optional
import httpx
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
# from database.filebase import AsyncFileDatabaseWatcher
# from database.specbase import SpecBase
from database.document_service import DocumentQueryService
from core.file_service import FileService
# 导入类型提示，而不是实例
from core.session import SessionStateManager
from my_mcp_tools.mcp_tools import project_mcp

# --- 全局共享实例的占位符 ---
# 这些变量将在 main.py 的 lifespan 中被实际赋值

app: Optional[FastAPI] = None
session_manager: Optional[SessionStateManager] = None
http_client: Optional[httpx.AsyncClient] = None
scheduler: Optional[AsyncIOScheduler] = None
document_service: Optional[DocumentQueryService] = None # 新的统一服务
project_file_service: Optional[FileService] = None
spec_file_service: Optional[FileService] = None
# project_database: Optional[AsyncFileDatabaseWatcher] = None # 旧服务
# spec_database: Optional[SpecBase] = None # 旧服务

# mcp_server 实例在导入时就已经创建好了，可以直接赋值
mcp_server = project_mcp
