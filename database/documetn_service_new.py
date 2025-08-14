import os
import time
import json
import sqlite3
from typing import Optional, Tuple, Dict, Any, List, Callable, Type, Literal, Union, cast
from pydantic import RootModel
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent, FileMovedEvent, DirMovedEvent
from pydantic import BaseModel, TypeAdapter
from tqdm import tqdm
from loguru import logger

from utils.utils import calculate_md5
from config import settings

from enum import Enum
from core.data_model import DocType

# --- 文档和数据库表结构定义 ---

DocumentType = Literal["项目文件", "规范文件", "管理文件", "其他"]

# --- Pydantic 模型定义 ---

class BaseDocument(BaseModel):
    """所有文档模型的基类，包含通用字段"""
    relative_path: str
    file_name: str
    ext: str
    size: int
    modified_time: float
    md5_hash: str
    last_scanned: float
    # 通用可选字段
    actual_name: Optional[str] = None
    description: Optional[str] = None
    version_date: Optional[str] = None
    create_user: Optional[str] = None
    modify_log: Optional[str] = None
    raw_content: Optional[bytes] = None

    def to_db_tuple(self, fields: List[str]) -> tuple:
        """将模型实例转换为用于数据库插入的元组"""
        return tuple(getattr(self, field) for field in fields)

class ProjectDocument(BaseDocument):
    """项目文件模型"""
    project_year: Optional[str] = None
    project_name: Optional[str] = None
    project_status: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None

class SpecDocument(BaseDocument):
    """规范文件模型"""
    category: Optional[str] = None
    doc_name: Optional[str] = None

class ManagementDocument(BaseDocument):
    """管理文件模型"""
    category: Optional[str] = None
    sub_category: Optional[str] = None

class OtherDocument(BaseDocument):
    """其他文件模型"""
    pass

# --- 辅助类型和枚举 ---

class ProjectFolderType(str, Enum):
    FINAL = "收口"
    FOR_REVIEW = "送审"
    RECORDS = "过程记录"

AnyDocument = Union[ProjectDocument, SpecDocument, ManagementDocument, OtherDocument]

class DocumentQueryService:
    _instance = None
    
    # 表名常量
    TABLE_PROJECTS = "docs_project"
    TABLE_SPECS = "docs_spec"
    TABLE_MANAGEMENT = "docs_management"
    TABLE_OTHERS = "docs_other"

    TABLE_MAP = {
        "项目文件": TABLE_PROJECTS,
        "规范文件": TABLE_SPECS,
        "管理文件": TABLE_MANAGEMENT,
        "其他": TABLE_OTHERS,
    }

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self.db_path = settings.DOCUMENT_DB_PATH.replace(".db", "_new.db") # 使用新的数据库文件
        self.root_dir: Dict[DocumentType, Path] = {
            "项目文件": settings.PROJECTS_ROOT_DIR,
            "规范文件": settings.SPEC_ROOT_DIR,
            "管理文件": settings.MANAGEMENT_ROOT_DIR,
        }
        self.root_dir = {k: v for k, v in self.root_dir.items() if v and os.path.isdir(v)}
        logger.info(f"有效文档根目录 (新版服务): {self.root_dir}")

        self.loop = asyncio.get_running_loop()
        self.executor = ThreadPoolExecutor()
        self.lock = asyncio.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row # 允许按列名访问

        self._init_db()

        self.observer = None
        self.pending_updates: Dict[str, float] = {}
        self.cooldown = 2.0
        self._initialized = True
        logger.info("新版 DocumentQueryService 初始化完成")

    def _init_db(self):
        logger.debug("初始化新版数据库...")
        base_columns = """
            relative_path TEXT PRIMARY KEY,
            file_name TEXT,
            ext TEXT,
            size INTEGER,
            modified_time REAL,
            md5_hash TEXT,
            last_scanned REAL,
            actual_name TEXT,
            description TEXT,
            version_date TEXT,
            create_user TEXT,
            modify_log TEXT,
            raw_content BLOB
        """
        with self.conn:
            # 项目文件表
            self.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE_PROJECTS} (
                    {base_columns},
                    project_year TEXT,
                    project_name TEXT,
                    project_status TEXT,
                    category TEXT,
                    sub_category TEXT
                )
            """)
            # 规范文件表
            self.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE_SPECS} (
                    {base_columns},
                    category TEXT,
                    doc_name TEXT
                )
            """)
            # 管理文件表
            self.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE_MANAGEMENT} (
                    {base_columns},
                    category TEXT,
                    sub_category TEXT
                )
            """)
            # 其他文件表
            self.conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE_OTHERS} (
                    {base_columns}
                )
            """)
        logger.debug("新版数据库表结构初始化完成。")

    def _get_table_and_model(self, doc_type: DocumentType) -> Tuple[str, Type[BaseDocument]]:
        """根据文档类型返回表名和对应的Pydantic模型"""
        return {
            "项目文件": (self.TABLE_PROJECTS, ProjectDocument),
            "规范文件": (self.TABLE_SPECS, SpecDocument),
            "管理文件": (self.TABLE_MANAGEMENT, ManagementDocument),
            "其他": (self.TABLE_OTHERS, OtherDocument),
        }[doc_type]

    def _extract_file_metadata(self, relative_path: Path, doc_type: DocumentType) -> Dict[str, Any]:
        """根据文件类型和相对路径提取元数据字典"""
        parts = relative_path.parts
        metadata = {}
        if not parts:
            return metadata

        if doc_type == "项目文件":
            metadata['project_year'] = parts[0] if len(parts) > 0 else None
            metadata['project_name'] = parts[1] if len(parts) > 1 else None
            status_str = parts[2] if len(parts) > 2 else None
            try:
                metadata['project_status'] = ProjectFolderType(status_str).value
            except ValueError:
                metadata['project_status'] = None
            
            if metadata.get('project_status') == ProjectFolderType.RECORDS.value:
                metadata['category'] = parts[3] if len(parts) > 3 else None
                metadata['sub_category'] = parts[4] if len(parts) > 4 else None

        elif doc_type == "规范文件":
            metadata['category'] = parts[0] if len(parts) > 0 else None
            metadata['doc_name'] = parts[1] if len(parts) > 1 else None

        elif doc_type == "管理文件":
            metadata['category'] = parts[0] if len(parts) > 0 else None
            metadata['sub_category'] = parts[1] if len(parts) > 1 else None

        return metadata

    def _get_file_info(self, abs_path: Path, doc_type: DocumentType) -> Optional[AnyDocument]:
        if not abs_path.is_file():
            return None

        if doc_type == "规范文件":
            spec_extensions = {".pdf", ".ofd", ".txt", ".ceb", ".md", ".docx"}
            if abs_path.suffix.lower() not in spec_extensions:
                return None

        try:
            base_path = self.root_dir[doc_type]
            relative_path = abs_path.relative_to(base_path)
            stat = abs_path.stat()
            md5 = calculate_md5(abs_path)
            if not md5: return None

            _, model_class = self._get_table_and_model(doc_type)
            
            base_info = {
                "relative_path": str(relative_path),
                "file_name": abs_path.name,
                "ext": abs_path.suffix.lstrip('.').lower(),
                "size": stat.st_size,
                "modified_time": stat.st_mtime,
                "md5_hash": md5,
                "last_scanned": time.time(),
            }
            
            metadata = self._extract_file_metadata(relative_path, doc_type)
            
            # 使用 cast 来帮助类型检查器
            instance = cast(AnyDocument, model_class(**base_info, **metadata))
            return instance

        except Exception as e:
            logger.error(f"为文件 '{abs_path}' 获取信息失败: {e}", exc_info=True)
        return None

    async def upsert_document(self, abs_path: Path, doc_type: DocumentType):
        file_model = await self.loop.run_in_executor(self.executor, self._get_file_info, abs_path, doc_type)
        if not file_model:
            return

        table_name, model_class = self._get_table_and_model(doc_type)
        fields = list(model_class.model_fields.keys())
        placeholders = ", ".join(["?"] * len(fields))
        
        sql = f"INSERT OR REPLACE INTO {table_name} ({', '.join(fields)}) VALUES ({placeholders})"
        db_tuple = file_model.to_db_tuple(fields)

        async with self.lock:
            try:
                with self.conn:
                    self.conn.execute(sql, db_tuple)
            except Exception as e:
                logger.error(f"写入数据库失败: {abs_path} -> {e}")

    async def _delete_document(self, relative_path: str):
        async with self.lock:
            try:
                with self.conn:
                    for table in self.TABLE_MAP.values():
                        self.conn.execute(f"DELETE FROM {table} WHERE relative_path = ?", (relative_path,))
                logger.info(f"文档已从所有表中删除: {relative_path}")
            except Exception as e:
                logger.error(f"从数据库删除失败: {relative_path} -> {e}")

    async def _delete_directory_contents(self, relative_path: str):
        path_prefix = f"{relative_path}/"
        async with self.lock:
            try:
                with self.conn:
                    for table in self.TABLE_MAP.values():
                        self.conn.execute(f"DELETE FROM {table} WHERE relative_path = ? OR relative_path LIKE ?", (relative_path, f"{path_prefix}%"))
                logger.info(f"目录内容已从所有表中删除: {relative_path}")
            except Exception as e:
                logger.error(f"从数据库删除目录内容失败: {relative_path} -> {e}")

    async def find_documents(self, **kwargs) -> List[Dict[str, Any]]:
        """
        通用的文档查询方法，根据提供的键值对参数查询数据库。
        如果提供了 document_type，则查询特定表；否则，查询所有表。
        """
        doc_type = kwargs.pop('document_type', None)

        def _db_query():
            if doc_type:
                # 查询单个表
                table_name = self.TABLE_MAP.get(doc_type)
                if not table_name:
                    return []
                
                query_parts = []
                params = []
                for key, value in kwargs.items():
                    if value is None: continue
                    operator = "LIKE" if isinstance(value, str) and '%' in value else "="
                    query_parts.append(f"{key} {operator} ?")
                    params.append(value)
                
                where_clause = " AND ".join(query_parts) if query_parts else "1=1"
                sql_query = f"SELECT * FROM {table_name} WHERE {where_clause}"
                
                logger.debug(f"执行单表查询: {sql_query} with params: {params}")
                cursor = self.conn.cursor()
                cursor.execute(sql_query, params)
                return [dict(row) for row in cursor.fetchall()]
            else:
                # 全局搜索，合并所有表的结果
                all_results = []
                for table_name in self.TABLE_MAP.values():
                    # 注意：这里的全局搜索逻辑可以根据需要变得更复杂
                    # 为简化起见，我们只支持基于通用字段的全局搜索
                    common_fields = [f.name for f in os.scandir('.') if f.is_file()] # 这是一个简化的例子
                    
                    query_parts = []
                    params = []
                    for key, value in kwargs.items():
                        # 检查字段是否存在于表中
                        cursor = self.conn.cursor()
                        cursor.execute(f"PRAGMA table_info({table_name})")
                        columns = [row['name'] for row in cursor.fetchall()]
                        if key in columns:
                            operator = "LIKE" if isinstance(value, str) and '%' in value else "="
                            query_parts.append(f"{key} {operator} ?")
                            params.append(value)

                    if not query_parts: continue # 如果没有匹配的字段，跳过此表

                    where_clause = " AND ".join(query_parts)
                    sql_query = f"SELECT * FROM {table_name} WHERE {where_clause}"
                    
                    logger.debug(f"执行全局查询 (部分): {sql_query} with params: {params}")
                    cursor = self.conn.cursor()
                    cursor.execute(sql_query, params)
                    all_results.extend([dict(row) for row in cursor.fetchall()])
                return all_results

        return await self.loop.run_in_executor(self.executor, _db_query)

    # --- Watchdog 和生命周期管理方法 (与旧版基本相同) ---
    # ... (此处省略 full_scan, _start_watchdog, _debounced_update_loop, start, shutdown 的代码，因为它们的核心逻辑不变)
    async def full_scan(self):
        logger.info("开始全量扫描...")
        total_files = 0
        for doc_type, root_path in self.root_dir.items():
            logger.info(f"正在扫描 '{doc_type}' 目录: {root_path}")
            files_in_dir = [f for f in root_path.rglob("*") if f.is_file()]
            total_files += len(files_in_dir)
            for f in tqdm(files_in_dir, desc=f"扫描 {doc_type}"):
                await self.upsert_document(f, doc_type)
        logger.info(f"全量扫描完成，共处理文件数: {total_files}")

    def _start_watchdog(self):
        logger.debug("启动文件监控 Watchdog")

        class Handler(FileSystemEventHandler):
            def __init__(self, outer_service: 'DocumentQueryService'):
                self.outer = outer_service

            def _to_str_path(self, path: Union[str, bytes, bytearray, memoryview]) -> str:
                if isinstance(path, str): return path
                if isinstance(path, memoryview): path = path.tobytes()
                if isinstance(path, (bytes, bytearray)): return path.decode("utf-8", errors="replace")
                return str(path)

            def _queue_update(self, event_type: str, path_str: str):
                if Path(path_str).name.startswith(('.', '~')) or path_str.endswith('.tmp'): return
                if event_type not in ["moved_dir", "deleted_dir"] and Path(path_str).is_dir(): return
                self.outer.pending_updates[path_str] = time.time()

            def _get_relative_path(self, abs_path_str: str) -> Optional[str]:
                p = Path(abs_path_str)
                for _, root_path in self.outer.root_dir.items():
                    try:
                        if p.is_relative_to(root_path): return str(p.relative_to(root_path))
                    except ValueError:
                        if abs_path_str.startswith(str(root_path)): return abs_path_str[len(str(root_path))+1:]
                return None

            def on_created(self, event: FileSystemEvent): self._queue_update("created", self._to_str_path(event.src_path))
            def on_modified(self, event: FileSystemEvent): self._queue_update("modified", self._to_str_path(event.src_path))
            
            def on_deleted(self, event: FileSystemEvent):
                rel_path = self._get_relative_path(self._to_str_path(event.src_path))
                if rel_path:
                    if event.is_directory: asyncio.run_coroutine_threadsafe(self.outer._delete_directory_contents(rel_path), self.outer.loop)
                    else: asyncio.run_coroutine_threadsafe(self.outer._delete_document(rel_path), self.outer.loop)

            def on_moved(self, event: Union[FileMovedEvent, DirMovedEvent]):
                src_path, dest_path = self._to_str_path(event.src_path), self._to_str_path(event.dest_path)
                rel_src_path = self._get_relative_path(src_path)
                if rel_src_path:
                    if event.is_directory: asyncio.run_coroutine_threadsafe(self.outer._delete_directory_contents(rel_src_path), self.outer.loop)
                    else: asyncio.run_coroutine_threadsafe(self.outer._delete_document(rel_src_path), self.outer.loop)
                if not event.is_directory: self._queue_update("moved", dest_path)

        event_handler = Handler(self)
        self.observer = Observer()
        for doc_type, path in self.root_dir.items():
            self.observer.schedule(event_handler, str(path), recursive=True)
            logger.info(f"正在监控 '{doc_type}' 目录: {path}")
        self.observer.start()
        logger.info("Watchdog 已启动")

    async def _debounced_update_loop(self):
        logger.debug("启动去抖更新循环")
        while True:
            await asyncio.sleep(self.cooldown)
            now = time.time()
            async with self.lock:
                paths_to_process = {p: t for p, t in self.pending_updates.items() if now - t >= self.cooldown}
                for p_str in paths_to_process: self.pending_updates.pop(p_str, None)
            if not paths_to_process: continue
            
            for p_str, _ in paths_to_process.items():
                p = Path(p_str)
                doc_type = None
                for dt, root_path in self.root_dir.items():
                    try:
                        if p.is_relative_to(root_path): doc_type = dt; break
                    except Exception:
                        if str(p).startswith(str(root_path)): doc_type = dt; break
                if doc_type: await self.upsert_document(p, doc_type)
                else: logger.warning(f"文件 '{p}' 不在任何已知的根目录下，跳过更新。")

    async def start(self):
        await self.full_scan()
        self._start_watchdog()
        self.update_task = asyncio.create_task(self._debounced_update_loop())
        logger.info("新版 DocumentQueryService 已启动")

    async def shutdown(self):
        logger.info("正在关闭新版 DocumentQueryService...")
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join()
        if hasattr(self, 'update_task') and not self.update_task.done():
            self.update_task.cancel()
            try: await self.update_task
            except asyncio.CancelledError: pass
        if self.conn: self.conn.close()
        logger.info("新版 DocumentQueryService 已成功关闭。")


async def query_specs_by_category(category: str) -> Dict[str, str]:
    """
    [Adapter Function] 使用新版服务按类别查询规范文件。
    """
    service = DocumentQueryService()
    results = await service.find_documents(document_type='规范文件', category=category)
    
    spec_map = {}
    for row in results:
        doc_name = row.get("doc_name")
        relative_path = row.get("relative_path")
        if doc_name and relative_path:
            spec_map[doc_name] = relative_path
            
    logger.info(f"查询到 '{category}' 分类的规范共 {len(spec_map)} 条 (新版)")
    return spec_map
