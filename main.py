# server_new.py
from contextlib import asynccontextmanager, AsyncExitStack
import os
from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler # 新增: 用于定时任务
from loguru import logger
import uvicorn
import httpx # 确保 httpx 已导入

from utils.utils import calculate_md5, get_host_ipv6_addr, check_embedding_service_health
from my_mcp_tools.mcp_tools import project_mcp # 导入新的 project_mcp

from config import settings # 导入配置
from core.session import SessionStateManager # 导入新的会话管理器
from core import app_state # 导入新的app_state模块
# from core.routing import router
from api import kkfileview, onlyoffice, route, admin, upload
from core.file_service import FileService

from database.document_service import DocumentQueryService # 导入我们统一的新服务

# --- Loguru 日志配置 ---
# 配置日志记录器，将日志输出到文件，并按周轮换
logger.add(
    "server_v4.log",
    rotation="1 week",
    retention="1 month", # 保留最近一个月的日志
    level="DEBUG", # 设置日志级别
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
)

logger.info("Loguru 日志系统已初始化。")
logger.info(f"项目根目录配置为: {settings.PROJECTS_ROOT_DIR}")
logger.info(f"数据库路径配置为: {settings.DOCUMENT_DB_PATH}")
logger.info(f"表格列读取配置已加载: {settings.SHEET_COLUMN_CONFIG}")
logger.info(f"kkFileView 服务地址配置为: {settings.KKFILEVIEW_BASE_URL}")
# logger.info(f"Dify Agent API Key: {'*' * 8}{settings.DIFY_AGENT_APIKEY.get_secret_value()[-4:] if isinstance(settings.DIFY_AGENT_APIKEY, SecretStr) else 'Not Set'}") # 仅显示部分以保护密钥
# logger.info(f"Session Secret Key: {'*' * 8}{settings.SESSION_SECRET_KEY.get_secret_value()[-4:] if isinstance(settings.SESSION_SECRET_KEY, SecretStr) else 'Not Set'}") # 仅显示部分以保护密钥
logger.info(f"Dify Agent API Key: {'*' * 8}{settings.DIFY_AGENT_APIKEY.get_secret_value()[-4:] if settings.DIFY_AGENT_APIKEY else 'Not Set'}") # 仅显示部分以保护密钥
logger.info(f"Session Secret Key: {'*' * 8}{settings.SESSION_SECRET_KEY.get_secret_value()[-4:]}") # 仅显示部分以保护密钥
logger.info(f"服务器ipv6地址为: http://[{get_host_ipv6_addr()}]:{settings.SERVER_PORT}")



# --- 全局实例 ---
# 全局变量现在由 app_state 模块管理

# --- 创建ASGI ---
# project_mcp 实例现在从 my_mcp_tools.mcp_tools 导入
# MCP_MOUNT_PATH 仍然在此处定义，因为它与 app.mount 相关
MCP_MOUNT_PATH = "/mcp/"
project_mcp_app = project_mcp.http_app(path=MCP_MOUNT_PATH) # project_mcp is now imported

# --- FastAPI 应用的 Lifespan 管理器 (Combined) ---
@asynccontextmanager
async def combined_lifespan(app_instance: FastAPI):
    '''
    fastapi生命期管理
    '''
    pid = os.getpid()
    logger.info(f"COMBINED LIFESPAN START - PID: {pid} - 服务器启动中...")
    async with AsyncExitStack() as stack:
        try:
            # --- 实例化并注册所有共享状态 ---
            app_state.http_client = await stack.enter_async_context(httpx.AsyncClient(timeout=settings.KKFILEVIEW_HTTP_TIMEOUT))
            logger.info("HTTPX 客户端已创建并注册到 app_state。")

            # --- 执行嵌入服务健康检查 ---
            settings.EMBEDDING_AVAILABLE = await check_embedding_service_health(app_state.http_client)
            # logger.info(f"嵌入模型服务可用性: {settings.EMBEDDING_AVAILABLE}")


            app_state.session_manager = SessionStateManager(inactivity_timeout=settings.SESSION_OVERALL_INACTIVITY_TIMEOUT_SECONDS)
            logger.info("SessionStateManager 已实例化并注册到 app_state。")

            # --- 统一文档查询服务初始化 ---
            # 使用单例模式获取实例
            app_state.document_service = DocumentQueryService()
            await app_state.document_service.start()
            logger.info("统一文档查询服务 (DocumentQueryService) 已启动。")

            # --- 文件服务初始化 ---
            app_state.project_file_service = FileService(root_dir=settings.PROJECTS_ROOT_DIR)
            app_state.spec_file_service = FileService(root_dir=settings.SPEC_ROOT_DIR)
            logger.info("项目和规程的文件服务已初始化。")


            # --- 定时任务调度器 (用于非文件扫描任务) ---
            app_state.scheduler = AsyncIOScheduler()
            logger.info("AsyncIOScheduler 已实例化并注册到 app_state。")

            if hasattr(project_mcp_app, 'lifespan') and project_mcp_app.lifespan:
                await stack.enter_async_context(project_mcp_app.lifespan(app_instance))
                logger.info("FastMCP lifespan context entered.")

            # --- 配置剩余的定时任务 ---
            scheduler = app_state.scheduler
            session_manager = app_state.session_manager
            # spec_scanner = app_state.spec_database # 旧的扫描器实例

            # # 添加规程规范定时扫描任务 (旧代码)
            # # 新的服务在启动时会进行全量扫描，并且可以配置实时监控，
            # # 如果仍然需要定时全量扫描，可以调用新服务的 full_scan 方法。
            # scheduler.add_job(
            #     spec_scanner.scan_specs,
            #     'cron',
            #     hour=settings.SPEC_SCAN_CRON_HOUR,
            #     minute=settings.SPEC_SCAN_CRON_MINUTE,
            #     id="scan_spec_files"
            # )
            # logger.info(f"规程规范定时扫描任务已添加到APScheduler (每天 {settings.SPEC_SCAN_CRON_HOUR}:{settings.SPEC_SCAN_CRON_MINUTE})。")

            scheduler.add_job(session_manager.process_inactive_sessions, 'interval', seconds=settings.SESSION_CLEANUP_INTERVAL_SECONDS, id="process_inactive_user_sessions")
            logger.info(f"处理不活动用户会话任务已添加到APScheduler (每 {settings.SESSION_CLEANUP_INTERVAL_SECONDS}s)。")

            scheduler.add_job(session_manager.cleanup_expired_opened_files, 'interval', seconds=settings.SESSION_CLEANUP_INTERVAL_SECONDS * 2, id="cleanup_session_opened_files")
            logger.info(f"清理用户会话中已打开文件token任务已添加到APScheduler (每 {settings.SESSION_CLEANUP_INTERVAL_SECONDS * 2}s)。")

            scheduler.start()
            logger.info("APScheduler 调度器已启动。")
            logger.info("所有初始化完成... yield")

            yield
        except Exception as e:
            logger.critical(f"Combined Lifespan 启动过程中发生严重错误: {e}", exc_info=True)
        finally:
            logger.info(f"COMBINED LIFESPAN END - PID: {pid} - 服务器关闭中...")
            # 安全关闭统一文档查询服务
            if hasattr(app_state, 'document_service') and app_state.document_service:
                await app_state.document_service.shutdown()
                logger.info("统一文档查询服务已关闭。")

            # 安全关闭调度器
            if app_state.scheduler and app_state.scheduler.running:
                app_state.scheduler.shutdown(wait=False)
                logger.info("APScheduler 调度器已关闭。")
            logger.info("Custom application resources cleaned up.")

# --- FastAPI 应用实例化 ---
app = FastAPI(
    title="项目文件查找器 MCP 服务",
    description="一个集成了 MCP 服务器的 FastAPI 应用，用于查找和管理项目文件。",
    version="1.1.0",
    lifespan=combined_lifespan
)
# --- 注册 app 实例 ---
app_state.app = app
logger.info("FastAPI 应用已实例化并注册到 app_state。")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
logger.info("CORS中间件已添加，允许所有来源。")
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY.get_secret_value())

@app.middleware("http")
async def rewrite_spec_images_middleware(request: Request, call_next):
    """
    一个自定义中间件，用于拦截对规范图片文件夹的请求，
    并将其重写到专门的动态路由进行处理。
    """
    url_path = request.url.path
    if url_path.startswith("/static/images/"):
        image_name = url_path[len("/static/images/"):]
        request.scope['path'] = f"/spec_images/{image_name}"
        logger.debug(f"中间件重写路径: '{url_path}' -> '{request.scope['path']}'")
    
    response = await call_next(request)
    return response

# --- 静态文件服务 ---
app.mount("/static", StaticFiles(directory="frontend"), name="static")
# --- 测试挂载md文件图片路径 ---
# app.mount("/images", StaticFiles(directory="/media/zhouxiang/FC7C74827C743A0A/规程规范/电气/DLT-866-2015 电流互感器和电压互感器选择及计算规程_md/images"), name="images1")
# app.mount("/images", StaticFiles(directory="/media/zhouxiang/FC7C74827C743A0A/规程规范/电气/33-GB-50217-2018-电力工程电缆设计标准_md/33-GB-50217-2018-电力工程电缆设计标准_md/images"), name="images2")
# app.mount("/images", StaticFiles(directory="/media/zhouxiang/FC7C74827C743A0A/规程规范/二次/GB／T-14285—2023《继电保护和安全自动装置技术规程》_md/images"), name="images3")

# --- 设置路由 ---
app.include_router(route.router)
app.include_router(upload.router, prefix="/api", tags=["Upload"])
app.include_router(admin.router, prefix="/api", tags=["Admin"])
# 此处因为需修改前端，所以暂时没有'api'前缀，以后再修复
app.include_router(kkfileview.router, tags=["kkfileview"])
app.include_router(onlyoffice.router, tags=["onlyoffice"])

# --- 挂载 MCP 应用 ---
app.mount(MCP_MOUNT_PATH, project_mcp_app)
logger.info(f"MCP 服务器 '{project_mcp.name}' 已挂载到 FastAPI 应用的 '{MCP_MOUNT_PATH}' 路径。")

# --- Uvicorn 日志配置 ---
LOGGING_CONFIG = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {
        "default": {"()": "uvicorn.logging.DefaultFormatter", "fmt": "%(levelprefix)s %(message)s", "use_colors": None},
        "access": {"()": "uvicorn.logging.AccessFormatter", "fmt": '%(asctime)s - %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s', "datefmt": "%Y-%m-%d %H:%M:%S"},
    },
    "handlers": {
        "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"},
    },
    "loggers": {
        "uvicorn.error": {"level": "INFO", "handlers": ["default"], "propagate": False},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        "starlette.websockets": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "websockets": {"handlers": ["default"], "level": "INFO", "propagate": False},
    },
}

if __name__ == "__main__":
    # 忽略警告
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT, log_config=LOGGING_CONFIG)
