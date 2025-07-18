import os
import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

from loguru import logger
from utils.utils import calculate_md5 # 导入共享的MD5计算函数

def extract_path_metadata(relative_file_path_str: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    从文件的相对路径中提取元数据（年份、项目名称、状态）。
    这个函数逻辑来源于 server_v7.py，以确保兼容性。

    Args:
        relative_file_path_str (str): 文件的相对路径字符串。

    Returns:
        Tuple[Optional[str], Optional[str], Optional[str]]: 包含年份、项目名称和状态的元组。
    """
    parts = Path(relative_file_path_str).parts
    year, project_name, status = None, None, None
    if len(parts) > 1 and parts[0].isdigit() and len(parts[0]) == 4:
        year = parts[0]
    if len(parts) > 2:
        project_name = parts[1]
    if len(parts) > 3:
        status = parts[2]
    logger.trace(f"路径元数据提取: '{relative_file_path_str}' -> 年份={year}, 项目={project_name}, 状态={status}")
    return year, project_name, status

class AsyncFileDatabaseWatcher:
    """
    一个异步文件数据库监视器。

    该类使用 `watchdog` 实时监控指定目录的文件系统事件（创建、修改、删除），
    并将文件元数据同步到一个 SQLite 数据库中。它具有防抖功能，以避免
    因文件频繁修改而导致的性能问题。
    """
    def __init__(self, root_dir: str, db_path: str, cooldown_seconds: int = 2):
        """
        初始化文件监视器。

        Args:
            root_dir (str): 要监视的根目录的绝对路径。
            db_path (str): SQLite 数据库文件的路径。
            cooldown_seconds (int): 事件处理的冷却时间（防抖），单位为秒。
        """
        self.root_dir = Path(root_dir)
        self.db_path = db_path
        self.cooldown = cooldown_seconds
        self.executor = ThreadPoolExecutor()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.lock = asyncio.Lock()
        self.observer = None
        self.pending_updates = {}  # path -> last_modified_time
        self.recently_scanned_dirs: Dict[str, float] = {} # 用于防止重复扫描的缓存
        self.check_task = None
        self.loop = asyncio.get_running_loop() # 获取并保存事件循环
        self._init_db()

    def _init_db(self):
        """
        初始化数据库，创建与 server_v7.py 兼容的 `indexed_files` 表。
        """
        logger.info(f"正在初始化数据库: {self.db_path}")
        try:
            with self.conn:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS indexed_files (
                        file_path TEXT PRIMARY KEY,
                        relative_path TEXT NOT NULL,
                        size INTEGER,
                        modified_time REAL,
                        md5_hash TEXT,
                        year TEXT,
                        project_name TEXT,
                        status TEXT,
                        last_scanned REAL
                    )
                """)
            logger.info(f"数据库 {self.db_path} 初始化完成，已确保 indexed_files 表存在。")
        except sqlite3.Error as e:
            logger.error(f"初始化数据库 {self.db_path} 失败: {e}")
            raise

    def _get_file_info(self, abs_path: Path) -> Optional[Dict[str, Any]]:
        """
        获取指定文件的元数据信息，以适配 `indexed_files` 表结构。

        Args:
            abs_path (Path): 文件的绝对路径。

        Returns:
            Optional[Dict[str, Any]]: 包含文件信息的字典，如果文件不存在或读取失败则返回 None。
        """
        try:
            if not abs_path.is_file():
                return None

            stat = abs_path.stat()
            relative_path_str = str(abs_path.relative_to(self.root_dir))

            md5 = calculate_md5(abs_path)
            if md5 is None:
                logger.warning(f"无法计算文件 {abs_path} 的MD5，跳过此文件。")
                return None

            year, project_name, status = extract_path_metadata(relative_path_str)

            return {
                'file_path': relative_path_str,
                'relative_path': relative_path_str,
                'size': stat.st_size,
                'modified_time': stat.st_mtime,
                'md5_hash': md5,
                'year': year,
                'project_name': project_name,
                'status': status,
                'last_scanned': time.time() # 每次更新都记录扫描时间
            }
        except FileNotFoundError:
            logger.warning(f"尝试获取信息时文件未找到: {abs_path}")
            return None
        except Exception as e:
            logger.error(f"读取文件信息失败: {abs_path} -> {e}")
            return None

    async def _insert_or_update(self, abs_path: Path):
        """
        将单个文件的信息插入或更新到数据库中。
        这是一个异步方法，它在线程池中执行阻塞的IO操作。

        Args:
            abs_path (Path): 要处理的文件的绝对路径。
        """
        loop = asyncio.get_running_loop()
        file_info = await loop.run_in_executor(self.executor, self._get_file_info, abs_path)

        if not file_info:
            return

        async with self.lock:
            try:
                with self.conn:
                    self.conn.execute("""
                        INSERT OR REPLACE INTO indexed_files (
                            file_path, relative_path, size, modified_time, md5_hash,
                            year, project_name, status, last_scanned
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, tuple(file_info.values()))
                # 输出太多日志, 暂时关闭
                # logger.info(f"[数据库更新] {file_info['relative_path']}")
            except sqlite3.Error as e:
                logger.error(f"[数据库写入失败] {file_info['relative_path']} -> {e}")

    async def _remove_file(self, abs_path: Path):
        """
        从数据库中删除一个文件的记录。

        Args:
            abs_path (Path): 已被删除的文件的绝对路径。
        """
        relative_path_str = str(abs_path.relative_to(self.root_dir))
        async with self.lock:
            try:
                with self.conn:
                    self.conn.execute("DELETE FROM indexed_files WHERE file_path = ?", (relative_path_str,))
                logger.info(f"[数据库删除] {relative_path_str}")
            except sqlite3.Error as e:
                logger.error(f"[数据库删除失败] {relative_path_str} -> {e}")

    async def _schedule_update(self, abs_path: str):
        """
        将一个文件路径加入待更新队列（防抖处理）。

        Args:
            abs_path (str): 文件的绝对路径。
        """
        self.pending_updates[abs_path] = time.time()
        logger.trace(f"已调度更新: {abs_path}")

    async def _debounced_update_loop(self):
        """
        防抖循环，定期检查并处理待更新队列中的文件。
        """
        while True:
            try:
                now = time.time()
                to_process = [p for p, ts in self.pending_updates.items() if now - ts > self.cooldown]

                for p_str in to_process:
                    # 从待处理队列中移除
                    if p_str in self.pending_updates:
                        del self.pending_updates[p_str]
                    # 执行更新
                    await self._insert_or_update(Path(p_str))

                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"防抖更新循环中发生错误: {e}", exc_info=True)


    def _on_event(self, abs_path: str):
        """ watchdog 事件回调，用于文件创建和修改。"""
        asyncio.run_coroutine_threadsafe(self._schedule_update(abs_path), self.loop)

    def _on_remove(self, abs_path: str):
        """ watchdog 事件回调，用于文件删除。"""
        asyncio.run_coroutine_threadsafe(self._remove_file(Path(abs_path)), self.loop)

    async def _scan_new_directory(self, dir_path: str):
        """
        异步扫描一个新创建的目录，并将其下所有文件加入处理队列。
        此方法现在包含一个进度条和防重复扫描逻辑。
        """
        # 清理缓存中的旧条目
        now = time.time()
        self.recently_scanned_dirs = {
            p: ts for p, ts in self.recently_scanned_dirs.items() if now - ts < self.cooldown
        }

        logger.info(f"检测到新目录，开始扫描: {dir_path}")
        try:
            # 1. 预计算文件总数
            files_to_scan = [os.path.join(root, f) for root, _, files in os.walk(dir_path) for f in files]
            total_files = len(files_to_scan)
            logger.info(f"在新目录 {dir_path} 中找到 {total_files} 个文件需要处理。")

            # 2. 使用 tqdm 创建进度条
            with tqdm(total=total_files, desc="扫描新目录进度", unit="file") as pbar:
                for abs_path_str in files_to_scan:
                    # 格式化文件名以供显示
                    filename = os.path.basename(abs_path_str)
                    max_length = 45
                    if len(filename) <= max_length:
                        display_name = "   " + filename.ljust(max_length)
                    else:
                        display_name = "..." + filename[-max_length:]

                    # pbar.set_postfix_str(f"正在处理: {display_name}")
                    pbar.set_description_str(f"正在处理: {display_name}")

                    # 使用 _schedule_update 来利用现有的防抖机制
                    await self._schedule_update(abs_path_str)
                    pbar.update(1)

            # 扫描成功后，将此目录添加到缓存
            self.recently_scanned_dirs[dir_path] = time.time()
            logger.info(f"新目录扫描完成: {dir_path}")
        except Exception as e:
            logger.error(f"扫描新目录 {dir_path} 时出错: {e}")

    def start_watchdog(self):
        """
        配置并启动 watchdog 观察者线程。
        """
        class Handler(FileSystemEventHandler):
            def __init__(self, outer):
                self.outer = outer

            def on_created(self, event):
                if event.is_directory:
                    now = time.time()
                    src_path = event.src_path

                    # 检查是否有父目录在近期被扫描过
                    for path, scan_time in self.outer.recently_scanned_dirs.items():
                        if src_path.startswith(path) and now - scan_time < self.outer.cooldown:
                            logger.info(f"跳过对 '{src_path}' 的扫描，因为它已被父目录 '{path}' 的近期扫描所覆盖。")
                            return  # 跳过此事件

                    # 如果没有，则正常安排扫描
                    asyncio.run_coroutine_threadsafe(self.outer._scan_new_directory(src_path), self.outer.loop)
                else:
                    self.outer._on_event(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    self.outer._on_event(event.src_path)

            def on_moved(self, event):
                # 当一个目录被移动或重命名时，其下的文件路径也会改变
                # 我们需要移除旧记录并扫描新位置
                self.outer._on_remove(event.src_path)
                if event.is_directory:
                    asyncio.run_coroutine_threadsafe(self.outer._scan_new_directory(event.dest_path), self.outer.loop)
                else:
                    self.outer._on_event(event.dest_path)

            def on_deleted(self, event):
                if not event.is_directory:
                    self.outer._on_remove(event.src_path)

        self.observer = Observer()
        self.observer.schedule(Handler(self), str(self.root_dir), recursive=True)
        self.observer.start()
        logger.info(f"文件系统监视器已启动，正在监视: {self.root_dir}")

    async def scan_existing(self):
        """
        在启动时对整个目录进行一次全量扫描，以确保数据库与文件系统同步。
        此方法现在包含一个进度条，并会记录总耗时。
        """
        start_time = time.time()
        start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))
        logger.info(f"开始执行初始全量扫描... (开始时间: {start_time_str})")

        # 1. 预计算文件总数
        files_to_scan = [Path(root) / f for root, _, files in os.walk(self.root_dir) for f in files]
        total_files = len(files_to_scan)
        logger.info(f"处理 {total_files} 个文件...")

        # 2. 使用 tqdm 创建进度条，并将文件名分行记录
        with tqdm(total=total_files, desc="文件初始扫描进度", unit="file") as pbar:
            for file_path in files_to_scan:
                # 修正：获取文件名
                filename = file_path.name  # 添加：从file_path获取文件名

                # 修正：格式化文件名显示
                max_length = 45
                if len(filename) <= max_length:
                    # 如果文件名不超过45字符，右边用空格填充到45字符，左边加3个空格
                    display_name = "..." + filename.ljust(max_length, ".")  # 总长度48
                else:
                    # 从右往左截断45个字符，左边加3个点
                    display_name = "..." + filename[-max_length:]      # 总长度48

                # 修正：先更新进度条显示，再处理文件
                # pbar.set_postfix_str(f"正在处理: {display_name}")
                pbar.set_description_str(f"正在处理: {display_name}")
                await self._insert_or_update(file_path)
                pbar.update(1)

        end_time = time.time()
        end_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))
        duration = end_time - start_time
        logger.info(f"初始全量扫描完成。 (结束时间: {end_time_str}, 总耗时: {duration:.2f} 秒)")

    async def start(self):
        """
        启动监视器服务，包括初始扫描、启动 watchdog 和防抖循环。
        """
        await self.scan_existing()
        self.start_watchdog()
        self.check_task = asyncio.create_task(self._debounced_update_loop())

    async def shutdown(self):
        """
        安全地关闭监视器服务。
        """
        logger.info("正在关闭文件系统监视器...")
        if self.observer:
            self.observer.stop()
            self.observer.join()
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                logger.info("防抖更新任务已取消。")
        self.conn.close()
        self.executor.shutdown(wait=True)
