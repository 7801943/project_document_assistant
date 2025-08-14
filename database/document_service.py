import os
import time
import json
import sqlite3
from typing import Optional, Tuple, Dict, Any, List, Callable, Type, Literal, Union
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

# --- 文档类型定义 ---
DocumentType = Literal["项目文件", "规范文件", "管理文件", "其他"]

class ProjectFolderType(str, Enum):
    FINAL = "收口"
    FOR_REVIEW = "送审"
    RECORDS = "过程记录"

class ProjectMetadata(BaseModel):
    year: Optional[str] = None
    project_name: Optional[str] = None
    status: Optional[ProjectFolderType] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None

class ManagementMetadata(BaseModel):
    category: Optional[str] = None
    sub_category: Optional[str] = None

class SpecMetadata(BaseModel):
    category: Optional[str] = None
    doc_name: Optional[str] = None

class UnknownMetadata(RootModel[dict]):
    pass

MetadataType = Union[ProjectMetadata, ManagementMetadata, SpecMetadata, UnknownMetadata]

class IndexedFile(BaseModel):
    relative_path: str
    file_name: str
    ext: str
    size: int
    modified_time: float
    md5_hash: str
    last_scanned: float
    document_type: DocumentType

    metadata_raw: Optional[str] = None
    actual_name: Optional[str] = None
    description: Optional[str] = None
    version_date: Optional[str] = None
    create_user: Optional[str] = None
    modify_log: Optional[str] = None
    raw_content: Optional[bytes] = None

    project_name: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    project_year: Optional[str] = None
    project_status: Optional[str] = None
    doc_name: Optional[str] = None

    metadata: Optional[MetadataType] = None

    def to_db_tuple(self) -> tuple:
        return (
            self.relative_path,
            self.file_name,
            self.ext,
            self.size,
            self.modified_time,
            self.md5_hash,
            self.last_scanned,
            self.document_type,
            self.metadata_raw,
            self.actual_name,
            self.description,
            self.version_date,
            self.create_user,
            self.modify_log,
            self.raw_content
        )

    @classmethod
    def from_db_row(cls, row: dict) -> "IndexedFile":
        metadata_raw = row.get("metadata", "{}")
        doc_type = row.get("document_type", "未知")

        try:
            metadata_dict = json.loads(metadata_raw)
        except Exception:
            metadata_dict = {}

        if doc_type == "项目文件":
            metadata = ProjectMetadata(**metadata_dict)
        elif doc_type == "管理文件":
            metadata = ManagementMetadata(**metadata_dict)
        elif doc_type == "规范文件":
            metadata = SpecMetadata(**metadata_dict)
        else:
            metadata = UnknownMetadata(root=metadata_dict)

        init_args = row.copy()
        init_args['metadata'] = metadata
        init_args['document_type'] = doc_type

        return cls(**init_args)

    def absolute_path(self, root_dirs: Dict[DocumentType, Path]) -> Optional[Path]:
        """根据文档类型和根目录字典，计算绝对路径"""
        root = root_dirs.get(self.document_type)
        if root:
            return root / self.relative_path
        # 如果找不到特定类型的根目录，尝试从根路径的第一部分猜测
        try:
            first_part = Path(self.relative_path).parts[0]
            if first_part in root_dirs:
                 return root_dirs[first_part] / Path(*Path(self.relative_path).parts[1:])
        except IndexError:
            pass
        logger.warning(f"无法确定文件 '{self.relative_path}' 的根目录")
        return None


class DocumentQueryService:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self.db_path = settings.DOCUMENT_DB_PATH
        # yapf: disable
        self.root_dir: Dict[DocumentType, Path] = {
            "项目文件": settings.PROJECTS_ROOT_DIR,
            "规范文件": settings.SPEC_ROOT_DIR,
            "管理文件": settings.MANAGEMENT_ROOT_DIR,
        }
        # yapf: enable
        # 过滤掉值为 None 或不是目录的路径
        self.root_dir = {
            k: v for k, v in self.root_dir.items()
            if v is not None and os.path.isdir(v)
        }
        logger.info(f"有效文档根目录: {self.root_dir}")

        self.loop = asyncio.get_running_loop()
        self.executor = ThreadPoolExecutor()
        self.lock = asyncio.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)

        self._init_db()

        self.observer = None
        self.pending_updates: Dict[str, float] = {}
        self.cooldown = 2.0
        self._initialized = True
        logger.info("DocumentQueryService 初始化完成")

    def _init_db(self):
        logger.debug("初始化数据库")
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS indexed_files (
                    relative_path TEXT PRIMARY KEY,
                    file_name TEXT,
                    ext TEXT,
                    size INTEGER,
                    modified_time REAL,
                    md5_hash TEXT,
                    last_scanned REAL,
                    document_type TEXT,
                    metadata TEXT,
                    actual_name TEXT,
                    description TEXT,
                    version_date TEXT,
                    create_user TEXT,
                    modify_log TEXT,
                    raw_content TEXT
                )
            """)
        logger.debug("数据库初始化完成")

    def _extract_file_metadata(self, relative_path: Path, doc_type: DocumentType) -> MetadataType:
        """根据文件类型和相对路径提取元数据"""
        parts = relative_path.parts
        if not parts:
            return UnknownMetadata(root={})

        if doc_type == "项目文件":
            year = parts[0] if len(parts) > 0 else None
            project_name = parts[1] if len(parts) > 1 else None
            status_str = parts[2] if len(parts) > 2 else None
            try:
                status = ProjectFolderType(status_str)
            except ValueError:
                status = None

            category = sub_category = None
            if status == ProjectFolderType.RECORDS:
                category = parts[3] if len(parts) > 3 else None
                sub_category = parts[4] if len(parts) > 4 else None

            return ProjectMetadata(
                year=year, project_name=project_name, status=status,
                category=category, sub_category=sub_category
            )

        elif doc_type == "规范文件":
            category = parts[0] if len(parts) > 0 else None
            doc_name = None  # 默认 doc_name 为 None

            # 只有当文件是可检索的文档类型，才提取 doc_name
            searchable_extensions = {".pdf", ".md", ".docx", ".txt", ".ofd", ".ceb"}
            if relative_path.suffix.lower() in searchable_extensions:
                # 如果文件在第二层目录（例如 '电气/文档名/文件.md'）
                # 我们假定文档名就是它所在的目录名
                if len(parts) > 1:
                    doc_name = parts[1]

            return SpecMetadata(category=category, doc_name=doc_name)

        elif doc_type == "管理文件":
            category = parts[0] if len(parts) > 0 else None
            sub_category = parts[1] if len(parts) > 1 else None
            return ManagementMetadata(category=category, sub_category=sub_category)

        return UnknownMetadata(root={})

    def _get_file_info(self, abs_path: Path, doc_type: DocumentType) -> Optional[IndexedFile]:
        if not abs_path.is_file():
            return None

        # 当文档类型为“规范文件”时，只索引特定扩展名的文件
        if doc_type == "规范文件":
            spec_extensions = {".pdf", ".ofd", ".txt", ".ceb", ".md", ".docx",".jpeg",".jpg","png"}
            if abs_path.suffix.lower() not in spec_extensions:
                # logger.debug(f"跳过非规范文件类型，文件: {abs_path}")
                return None

        try:
            base_path = self.root_dir[doc_type]
            # 使用 doc_type 的根目录计算相对路径
            relative_path = abs_path.relative_to(base_path)

            metadata = self._extract_file_metadata(relative_path, doc_type)
            stat = abs_path.stat()
            md5 = calculate_md5(abs_path)
            if not md5:
                return None

            return IndexedFile(
                relative_path=str(relative_path),
                file_name=abs_path.name,
                ext=abs_path.suffix.lstrip('.').lower(),
                size=stat.st_size,
                modified_time=stat.st_mtime,
                md5_hash=md5,
                last_scanned=time.time(),
                document_type=doc_type,
                metadata_raw=metadata.model_dump_json(exclude_none=True),
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"为文件 '{abs_path}' 获取信息失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        return None

    async def upsert_document(self, abs_path: Path, doc_type: DocumentType):
        #logger.debug(f"准备更新文档: {abs_path} (类型: {doc_type})")
        file = await self.loop.run_in_executor(self.executor, self._get_file_info, abs_path, doc_type)
        if not file:
            logger.debug(f"跳过空文档或元信息失败: {abs_path}")
            return
        async with self.lock:
            try:
                with self.conn:
                    self.conn.execute("""
                        INSERT OR REPLACE INTO indexed_files (
                            relative_path, file_name, ext, size, modified_time, md5_hash, last_scanned,
                            document_type, metadata, actual_name, description, version_date,
                            create_user, modify_log, raw_content
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, file.to_db_tuple())
                # logger.info(f"文档已写入数据库: {file.relative_path}")
            except Exception as e:
                logger.error(f"写入数据库失败: {abs_path} -> {e}")

    async def _delete_document(self, relative_path: str):
        """从数据库中删除单个文档记录。"""
        async with self.lock:
            try:
                with self.conn:
                    self.conn.execute("DELETE FROM indexed_files WHERE relative_path = ?", (relative_path,))
                logger.info(f"文档已从数据库删除: {relative_path}")
            except Exception as e:
                logger.error(f"从数据库删除失败: {relative_path} -> {e}")

    async def _delete_directory_contents(self, relative_path: str):
        """从数据库中递归删除目录及其所有内容的记录。"""
        # 确保路径以斜杠结尾，以匹配目录下的所有文件
        path_prefix = f"{relative_path}/"
        async with self.lock:
            try:
                with self.conn:
                    # 删除目录本身以及目录下的所有文件
                    self.conn.execute("DELETE FROM indexed_files WHERE relative_path = ? OR relative_path LIKE ?", (relative_path, f"{path_prefix}%"))
                logger.info(f"目录内容已从数据库删除: {relative_path}")
            except Exception as e:
                logger.error(f"从数据库删除目录内容失败: {relative_path} -> {e}")

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
                """将各种可能的路径类型统一转换为字符串。"""
                if isinstance(path, str):
                    return path
                if isinstance(path, memoryview):
                    path = path.tobytes()
                if isinstance(path, (bytes, bytearray)):
                    return path.decode("utf-8", errors="replace")
                return str(path)

            def _queue_update(self, event_type: str, path_str: str):
                """检查并过滤事件，然后将有效路径加入待更新队列。"""
                # 忽略临时/隐藏文件
                if Path(path_str).name.startswith(('.', '~')) or path_str.endswith('.tmp'):
                    return
                
                # 对于非目录移动/删除事件，如果目标是目录，则忽略
                if event_type not in ["moved_dir", "deleted_dir"] and Path(path_str).is_dir():
                    return

                self.outer.pending_updates[path_str] = time.time()
                # logger.debug(f"文件变化捕获 ({event_type}): {path_str}")

            def _get_relative_path(self, abs_path_str: str) -> Optional[str]:
                """将绝对路径转换为相对于监控根目录的路径。"""
                p = Path(abs_path_str)
                for _, root_path in self.outer.root_dir.items():
                    try:
                        if p.is_relative_to(root_path):
                            return str(p.relative_to(root_path))
                    except ValueError: # python < 3.9
                        if abs_path_str.startswith(str(root_path)):
                            return abs_path_str[len(str(root_path))+1:]
                return None

            def on_created(self, event: FileSystemEvent):
                path_str = self._to_str_path(event.src_path)
                self._queue_update("created", path_str)

            def on_modified(self, event: FileSystemEvent):
                path_str = self._to_str_path(event.src_path)
                self._queue_update("modified", path_str)

            def on_deleted(self, event: FileSystemEvent):
                src_path_str = self._to_str_path(event.src_path)
                rel_path = self._get_relative_path(src_path_str)
                if rel_path:
                    if event.is_directory:
                        asyncio.run_coroutine_threadsafe(self.outer._delete_directory_contents(rel_path), self.outer.loop)
                    else:
                        asyncio.run_coroutine_threadsafe(self.outer._delete_document(rel_path), self.outer.loop)

            def on_moved(self, event: Union[FileMovedEvent, DirMovedEvent]):
                src_path_str = self._to_str_path(event.src_path)
                dest_path_str = self._to_str_path(event.dest_path)

                # 处理源路径（删除旧记录）
                rel_src_path = self._get_relative_path(src_path_str)
                if rel_src_path:
                    if event.is_directory:
                        asyncio.run_coroutine_threadsafe(self.outer._delete_directory_contents(rel_src_path), self.outer.loop)
                    else:
                        asyncio.run_coroutine_threadsafe(self.outer._delete_document(rel_src_path), self.outer.loop)
                
                # 处理目标路径（添加/更新新记录）
                if event.is_directory:
                    # 如果是目录移动，需要扫描整个新目录
                    # 注意：这里我们假设移动后目录下的文件会触发单独的事件，
                    # 或者依赖于下一次的全量扫描。为了简化，我们只记录目录移动事件。
                    # 一个更健壮的实现是遍历 dest_path_str 并为每个文件调用 _queue_update。
                    # 这里我们选择简单的方式，依赖于文件事件。
                    logger.info(f"目录已移动: 从 {src_path_str} 到 {dest_path_str}。数据库记录已删除，新位置将由文件事件更新。")
                else:
                    # 如果是文件移动，直接更新目标文件
                    self._queue_update("moved", dest_path_str)

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

            # 加锁以安全地复制和清空待办事项
            async with self.lock:
                paths_to_process = {p: t for p, t in self.pending_updates.items() if now - t >= self.cooldown}
                for p_str in paths_to_process:
                    self.pending_updates.pop(p_str, None)

            if not paths_to_process:
                continue

            logger.debug(f"开始批量更新 {len(paths_to_process)} 个文件...位于目录{paths_to_process}")
            for p_str, _ in paths_to_process.items():
                p = Path(p_str)
                # 确定文件属于哪个文档类型
                doc_type = None
                for dt, root_path in self.root_dir.items():
                    try:
                        if p.is_relative_to(root_path):
                            doc_type = dt
                            break
                    except Exception: # python < 3.9
                        if str(p).startswith(str(root_path)):
                            doc_type = dt
                            break

                if doc_type:
                    await self.upsert_document(p, doc_type)
                else:
                    logger.warning(f"文件 '{p}' 不在任何已知的根目录下，跳过更新。")
            logger.info(f"已更新至数据库")

    async def start(self):
        await self.full_scan()
        self._start_watchdog()
        self.update_task = asyncio.create_task(self._debounced_update_loop())
        logger.info("DocumentQueryService 已启动")

    async def shutdown(self):
        """优雅地关闭服务，释放资源。"""
        logger.info("正在关闭 DocumentQueryService...")
        # 1. 停止文件监控
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join() # 等待监控线程完全停止
            logger.info("Watchdog 监控已停止。")

        # 2. 取消后台更新任务
        if hasattr(self, 'update_task') and not self.update_task.done():
            self.update_task.cancel()
            try:
                await self.update_task
            except asyncio.CancelledError:
                logger.info("后台更新任务已取消。")

        # 3. 关闭数据库连接
        if self.conn:
            self.conn.close()
            logger.info("数据库连接已关闭。")

        logger.info("DocumentQueryService 已成功关闭。")

    async def find_documents(self, **kwargs) -> List[Dict[str, Any]]:
        """
        通用的文档查询方法，根据提供的键值对参数查询数据库。
        参数以键值对形式提供，例如:
        - document_type='项目文件'
        - project_name='xxx'
        - year='2024'
        """
        def _db_query():
            query_parts = []
            params = []

            # 动态构建WHERE子句
            for key, value in kwargs.items():
                if value is None:
                    continue

                is_like_query = isinstance(value, str) and '%' in value
                operator = "LIKE" if is_like_query else "="

                if key in ['project_name', 'year', 'project_status', 'category', 'sub_category', 'doc_name']:
                    query_parts.append(f"json_extract(metadata, '$.{key}') {operator} ?")
                    params.append(value)
                else:
                    query_parts.append(f"{key} {operator} ?")
                    params.append(value)

            if not query_parts:
                # 如果没有提供任何查询条件，可以返回空列表或所有文档
                # 为安全起见，返回空列表
                return []

            where_clause = " AND ".join(query_parts)
            sql_query = f"SELECT * FROM indexed_files WHERE {where_clause}"

            logger.debug(f"执行数据库查询: {sql_query} with params: {params}")

            cursor = self.conn.cursor()
            cursor.execute(sql_query, params)
            # 获取列名
            columns = [description[0] for description in cursor.description]
            # 将每一行元组转换为字典
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

        return await self.loop.run_in_executor(self.executor, _db_query)


async def query_specs_by_category(category: str) -> Dict[str, str]:
    """
    [Adapter Function] Queries spec files by category and returns a dictionary
    mapping document names to their relative paths, ensuring backward compatibility
    with mcp_tools.py.
    """
    service = DocumentQueryService()
    conn = service.conn
    loop = asyncio.get_running_loop()

    def _db_query() -> Dict[str, str]:
        """Synchronous database query logic."""
        logger.debug(f"查询规范文件分类 (适配器): {category}")
        cursor = conn.cursor()
        # 查询 metadata 和 relative_path
        cursor.execute(
            "SELECT metadata, relative_path FROM indexed_files WHERE document_type = '规范文件'"
        )
        rows = cursor.fetchall()

        results: Dict[str, str] = {}
        for metadata_json, relative_path in rows:
            try:
                metadata_dict = json.loads(metadata_json)
                # 检查 category 是否匹配，并且 doc_name 存在
                if isinstance(metadata_dict, dict) and metadata_dict.get("category") == category:
                    doc_name = metadata_dict.get("doc_name")
                    if doc_name:
                        results[doc_name] = relative_path
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"无法解析元数据: {metadata_json}")
                continue

        logger.info(f"查询到 '{category}' 分类的规范共 {len(results)} 条:{results}")
        return results

    # 在线程池中运行同步的数据库查询
    return await loop.run_in_executor(service.executor, _db_query)
