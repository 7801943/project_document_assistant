from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, HttpUrl, DirectoryPath # 导入 DirectoryPath 用于路径验证
from typing import Dict, Tuple, Optional, Any, List
from pathlib import Path
import secrets
import json
import logging # 用于记录解析错误

# 获取一个日志记录器实例，用于记录配置加载中可能出现的问题
config_logger = logging.getLogger(__name__)

class Settings(BaseSettings):

    SYSTEM_PROMPT: str
    # --- 通用项目设置 ---
    PROJECTS_ROOT_DIR: DirectoryPath = Path("/media/zhouxiang/FC7C74827C743A0A/Projects1") # 项目根目录
    DEFAULT_YEAR: str = "2024" # 默认年份
    DEFAULT_STATUS: str = "送审" # 默认状态

    # --- 数据库和扫描设置 ---
    DATABASE_NAME: str = "data/project_files.db" # 数据库文件名
    FILE_SCAN_CRON_HOUR: int = 23 # 文件扫描执行小时 (23点)
    FILE_SCAN_CRON_MINUTE: int = 0 # 文件扫描执行分钟
    FILE_WATCHER_COOLDOWN_SECONDS: int = 2 # 文件监视器事件处理延迟（防抖）

    # --- 服务器配置 ---
    SERVER_HOST: Optional[str] = None # ipv6 ipv4 双栈
    SERVER_PORT: int = 8888 # 服务器端口
    SERVER_INTERFACE: str = "wlp7s0" # 服务器监听的网络接口
    SESSION_SECRET_KEY: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_hex(32))) # Session 密钥，如果 .env 未提供则自动生成
    MCP_PATH: str = "/mcp/"

    # --- Dify Agent API 配置 ---
    DIFY_AGENT_APIKEY: SecretStr # Dify Agent API 密钥
    DIFY_AGENT_BASE_URL: str # Dify Agent 基础 URL, 例如 "/v1/chat-messages"
    UPSTREAM_CHAT_URL: HttpUrl # 上游聊天服务 URL, 例如 "http://127.0.0.1/v1/chat-messages"
    DIFY_HTTP_TIMEOUT: float = 30.0

    # --- OpenAI 兼容接口配置 ---
    OPENAI_API_BASE_URL: HttpUrl # OpenAI 兼容接口的基础 URL
    OPENAI_API_KEY: SecretStr # OpenAI 兼容接口的 API 密钥
    OPENAI_MODEL_NAME: str # OpenAI 兼容接口的 模型名称
    CONVERSATION_ROOT_PATH: str = "chat_history" # 会话历史记录的根目录


    # --- Dify 知识库配置 ---
    DIFY_KNOWLEDGEBASE_URL: HttpUrl # Dify 知识库 URL, 例如 "http://127.0.0.1/v1"
    DIFY_KNOWLEDGEBASE_APIKEY: SecretStr # Dify 知识库 API 密钥
    DIFY_RERANK_MODEL: str = "gte-rerank-v2" # Rerank 模型
    DIFY_RERANK_MODEL_PROVIDER: str = "Tongyi" # Rerank 模型提供商
    DIFY_KNOWLEDGEBASE_RETRIEVAL_TOP_K: int = 5 # 知识库检索 Top
    DIFY_ENABLE_RERANK: bool = True


    # --- 本地向量模型配置 ---
    EMBEDDING_API_URL: HttpUrl
    EMBEDDING_APIKEY: SecretStr
    EMBEDDING_MODEL_NAME: str = "bge-m3" # 默认为 'bge-m3'，但会被 .env 中的值覆盖
    EMBEDDING_AVAILABLE: bool = Field(default=False, init_var=False) # 设为 False，并且不由 __init__ 直接赋值

    # --- 文件比较配置 (从 .env 中的 JSON 字符串加载) ---
    SHEET_COLUMN_CONFIG_JSON: str = Field(default='{}') # 表格列读取配置的 JSON 字符串
    # --- 模型上下文窗口（用于比较文件和文件读取工具）
    MODEL_CONTEXT_WINDOW: int = 64000
    # --- 下载链接配置 ---
    DOWNLOAD_LINK_VALIDITY_SECONDS: int = 3600 # 下载链接有效期 (秒)

    # --- 会话管理配置 ---
    SESSION_CLEANUP_INTERVAL_SECONDS: int = 60 # 会话清理任务执行间隔 (秒)
    SESSION_OVERALL_INACTIVITY_TIMEOUT_SECONDS: int = 3600 # 整体会话不活动超时时间 (秒)

    # --- kkFileView 配置 ---
    KKFILEVIEW_BASE_URL: HttpUrl # kkFileView 服务地址, 例如 "http://127.0.0.1:8012/kkfileview"
    KKFILEVIEW_HTTP_TIMEOUT: float = 60.0

    # --- OnlyOffice 配置 ---
    ONLYOFFICE_JWT_SECRET: SecretStr = Field(default="your_secret_key_for_onlyoffice") # 请在 .env 文件中覆盖此项
    ONLYOFFICE_JWT_ENABLED: bool

    # --- 用户认证配置 (从 .env 中的 JSON 字符串加载) ---
    FAKE_USERS_DB_JSON: str = Field(default='{}') # 模拟用户数据库的 JSON 字符串

    # --- 规程规范扫描配置 ---
    SPEC_ROOT_DIR: DirectoryPath = Path("/media/zhouxiang/FC7C74827C743A0A/规程规范") # 项目根目录
    SPEC_DIRS_CAT: str = Field(default='[]')
    ALLOWED_FILE_TYPES_JSON: str = Field(default='[]', alias='ALLOWED_FILE_TYPES')
    SPEC_DATABASE_NAME: str = "data/spec_files.db"
    SPEC_SCAN_CRON_HOUR: int = 2
    SPEC_SCAN_CRON_MINUTE: int = 30

    # --- 计算属性 ---
    @property
    def ALLOWED_FILE_TYPES(self) -> List[str]:
        try:
            return json.loads(self.ALLOWED_FILE_TYPES_JSON)
        except json.JSONDecodeError:
            config_logger.warning(f"无法解析 ALLOWED_FILE_TYPES_JSON: '{self.ALLOWED_FILE_TYPES_JSON}'。返回空列表。")
            return []
    @property
    def SPEC_DATABASE_PATH(self) -> Path:
        return Path(__file__).parent / self.SPEC_DATABASE_NAME

    @property
    def SPEC_DIRS(self) -> List[str]:
        try:
            return json.loads(self.SPEC_DIRS_CAT)
        except json.JSONDecodeError:
            config_logger.warning(f"无法解析 SPEC_DIRS_CAT: '{self.SPEC_DIRS_CAT}'。返回空列表。")
            return []

    @property
    def DATABASE_PATH(self) -> Path:
        # config.py 与项目根目录同级, 因此 .parent 就是项目根目录
        return Path(__file__).parent / self.DATABASE_NAME

    @property
    def SHEET_COLUMN_CONFIG(self) -> Dict[str, Tuple[int, int]]:
        try:
            loaded_config = json.loads(self.SHEET_COLUMN_CONFIG_JSON)
            # 如果 JSON 中的值是列表，则确保将其转换为元组
            return {k: tuple(v) if isinstance(v, list) else v for k, v in loaded_config.items()}
        except json.JSONDecodeError:
            config_logger.warning(f"无法解析 SHEET_COLUMN_CONFIG_JSON: '{self.SHEET_COLUMN_CONFIG_JSON}'。返回空字典。")
            return {}

    @property
    def FAKE_USERS_DB(self) -> Dict[str, Dict[str, str]]:
        try:
            return json.loads(self.FAKE_USERS_DB_JSON)
        except json.JSONDecodeError:
            config_logger.warning(f"无法解析 FAKE_USERS_DB_JSON: '{self.FAKE_USERS_DB_JSON}'。返回空字典。")
            return {}

    async def async_init(self):
        """
        执行异步初始化任务，例如健康检查。
        """
        # 仅在尚未检查时执行
        # from utils.utils import check_embedding_service_health
        # from core.app_state import http_client
        # if http_client:
        #     self.EMBEDDING_AVAILABLE = await check_embedding_service_health(http_client)
        # else:
        #     # 如果 http_client 尚未初始化，则无法执行检查
        #     config_logger.warning("无法执行嵌入服务健康检查，因为共享的 http_client 尚未初始化。")
        #     self.EMBEDDING_AVAILABLE = False
        pass # 暂时移除这里的逻辑，移到 main.py 的 lifespan 中执行

    model_config = SettingsConfigDict(
        env_file='.env', # 指定 .env 文件名
        env_file_encoding='utf-8', # 指定 .env 文件编码
        extra='ignore' # 忽略 .env 文件中未在 Settings 类中定义的额外字段
    )

settings = Settings() # 创建 Settings 实例，Pydantic 会自动从 .env 文件加载配置


