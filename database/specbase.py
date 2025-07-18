import os
import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from loguru import logger

from utils.utils import calculate_md5 # 导入共享的MD5计算函数


def query_specs_by_category(db_path: str, category: str) -> Dict[str, str]:
    """
    根据专业类别从数据库查询规程规范文件。线程安全。

    Args:
        db_path (str): 数据库文件路径。
        category (str): 专业类别。

    Returns:
        Dict[str, str]: 一个字典，键是规程名称，值是相对路径。
    """
    logger.debug(f"正在从数据库 '{db_path}' 查询类别为 '{category}' 的规程规范...")
    specs = {}
    try:
        # 每个函数调用创建独立的连接，以保证线程安全
        # 使用 file: URI 和 ?mode=ro 以只读模式打开连接，增加安全性
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name, relative_path FROM spec_files WHERE category = ?", (category,))
            for row in cursor.fetchall():
                if row[0] and row[1]:
                    specs[row[0]] = row[1]
        logger.info(f"成功为类别 '{category}' 查询到 {len(specs)} 条规程记录。")
    except sqlite3.OperationalError as e:
        # 如果是只读模式相关的错误 (例如，数据库文件在共享驱动器上，权限问题)，尝试以默认模式打开
        if "attempt to write a readonly database" in str(e) or "readonly database" in str(e):
            logger.warning(f"以只读模式打开数据库失败，尝试以默认模式重新连接: {e}")
            try:
                conn = sqlite3.connect(db_path)
                with conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name, relative_path FROM spec_files WHERE category = ?", (category,))
                    for row in cursor.fetchall():
                        if row[0] and row[1]:
                            specs[row[0]] = row[1]
                logger.info(f"在默认模式下，成功为类别 '{category}' 查询到 {len(specs)} 条规程记录。")
            except sqlite3.Error as e_rw:
                logger.error(f"以读写模式连接数据库 '{db_path}' 并查询时也发生错误: {e_rw}")
        else:
            logger.error(f"查询数据库 '{db_path}' 时发生操作错误: {e}")
    except sqlite3.Error as e:
        logger.error(f"查询数据库 '{db_path}' 时发生错误: {e}")
    return specs


def _extract_spec_path_metadata(relative_file_path_str: str) -> Dict[str, Optional[str]]:
    """
    从文件的相对路径中提取分类和规程名称。
    路径结构假定为: <分类>/<规程名称>/...
    典型示例：
    <SPEC_ROOT_DIR>/二次/GB／T-14285—2023《继电保护和安全自动装置技术规程》_md/GB／T-14285—2023《继电保护和安全自动装置技术规程》_md.md
    """
    parts = Path(relative_file_path_str).parts
    category = parts[0] if len(parts) > 1 else None
    name = parts[1] if len(parts) > 2 else None
    return {"category": category, "name": name}

class SpecBase:
    """
    一个用于扫描和索引规程规范文件的类。

    该类会扫描指定的根目录下由 `spec_dirs` 定义的专业文件夹，
    查找所有符合特定命名规则（例如，以“规范.md”结尾）的文件，
    并将它们的元数据存储到一个 SQLite 数据库中。
    它不执行实时监控，而是提供一个可以被定时任务调用的扫描方法。
    """
    def __init__(self, root_dir: str, db_path: str, spec_dirs: List[str], allowed_file_types: List[str]):
        """
        初始化规程规范扫描器。

        Args:
            root_dir (str): 要扫描的根目录的绝对路径。
            db_path (str): SQLite 数据库文件的路径。
            spec_dirs (List[str]): 需要扫描的专业目录列表。
            allowed_file_types (List[str]): 允许的文件扩展名列表。
        """
        self.root_dir = Path(root_dir)
        self.db_path = db_path
        self.spec_dirs = spec_dirs
        self.allowed_file_types = [ft.lower() for ft in allowed_file_types]
        self.executor = ThreadPoolExecutor()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.lock = asyncio.Lock()
        self._init_db()

    def _init_db(self):
        """
        初始化数据库，创建 `spec_files` 表。
        """
        logger.info(f"正在初始化规程规范数据库: {self.db_path}")
        try:
            with self.conn:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS spec_files (
                        relative_path TEXT PRIMARY KEY,
                        name TEXT,
                        category TEXT,
                        file_type TEXT,
                        size INTEGER,
                        modified_time REAL,
                        md5_hash TEXT,
                        last_scanned REAL
                    )
                """)
            logger.info(f"数据库 {self.db_path} 初始化完成，已确保 spec_files 表存在。")
        except sqlite3.Error as e:
            logger.error(f"初始化规程规范数据库 {self.db_path} 失败: {e}")
            raise

    def _get_file_info(self, abs_path: Path) -> Optional[Dict[str, Any]]:
        """
        获取指定文件的元数据信息，以适配 `spec_files` 表结构。
        """
        try:
            if not abs_path.is_file():
                return None

            stat = abs_path.stat()
            relative_path_str = str(abs_path.relative_to(self.root_dir))

            # 提取文件类型
            file_type = abs_path.suffix.lstrip('.').lower()

            md5 = calculate_md5(abs_path)
            if md5 is None:
                logger.warning(f"无法计算文件 {abs_path} 的MD5，跳过此文件。")
                return None

            metadata = _extract_spec_path_metadata(relative_path_str)

            return {
                'relative_path': relative_path_str,
                'name': metadata.get('name'),
                'category': metadata.get('category'),
                'file_type': file_type,
                'size': stat.st_size,
                'modified_time': stat.st_mtime,
                'md5_hash': md5,
                'last_scanned': time.time()
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
        """
        loop = asyncio.get_running_loop()
        file_info = await loop.run_in_executor(self.executor, self._get_file_info, abs_path)

        if not file_info:
            return

        async with self.lock:
            try:
                with self.conn:
                    self.conn.execute("""
                        INSERT OR REPLACE INTO spec_files (
                            relative_path, name, category, file_type, size, modified_time, md5_hash, last_scanned
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, tuple(file_info.values()))
            except sqlite3.Error as e:
                logger.error(f"[规程数据库写入失败] {file_info['relative_path']} -> {e}")

    async def scan_specs(self):
        """
        对指定的专业目录进行一次全量扫描。
        """
        start_time = time.time()
        logger.info("开始执行规程规范全量扫描...")

        files_to_scan = []
        for spec_dir_name in self.spec_dirs:
            spec_dir_path = self.root_dir / spec_dir_name
            if not spec_dir_path.is_dir():
                logger.warning(f"专业目录不存在，跳过: {spec_dir_path}")
                continue

            # 递归查找所有文件
            for file_path in spec_dir_path.rglob('*'):
                if file_path.is_file():
                    # 检查文件类型是否在允许的列表中
                    file_ext = file_path.suffix.lstrip('.').lower()
                    if file_ext in self.allowed_file_types:
                        files_to_scan.append(file_path)

        total_files = len(files_to_scan)
        logger.info(f"在 {len(self.spec_dirs)} 个专业目录中找到 {total_files} 个符合条件的文件需要处理。")

        with tqdm(total=total_files, desc="规程规范扫描进度", unit="file") as pbar:
            for file_path in files_to_scan:
                pbar.set_postfix_str(f"正在处理: {file_path.name}")
                await self._insert_or_update(file_path)
                pbar.update(1)

        duration = time.time() - start_time
        logger.info(f"规程规范全量扫描完成，总耗时: {duration:.2f} 秒。")

    async def add_spec_directory(self, spec_path: Path):
        """
        扫描并索引指定目录下的所有文件。
        """
        logger.info(f"开始按需索引目录: {spec_path}")
        if not spec_path.is_dir():
            logger.warning(f"请求索引的路径不是一个目录，已跳过: {spec_path}")
            return

        # 使用 rglob('*') 递归查找所有文件
        files_to_index = [p for p in spec_path.rglob('*') if p.is_file()]
        total_files = len(files_to_index)

        if total_files == 0:
            logger.info(f"目录 {spec_path} 为空，无需索引。")
            return
        logger.debug(f"开始按需索引目录: {spec_path}, {total_files} 个文件。")

        tasks = [self._insert_or_update(file_path) for file_path in files_to_index]
        await asyncio.gather(*tasks)

        logger.info(f"目录 {spec_path} 的按需索引完成。")

    async def remove_spec_directory(self, category: str, name: str):
        """
        根据专业和规程名，从数据库中删除所有相关文件记录。
        """
        # 规范化路径，确保使用 posix 风格的斜杠
        dir_prefix = f"{category}/{name}"
        logger.info(f"准备从数据库中删除规程目录: {dir_prefix}")

        async with self.lock:
            try:
                with self.conn:
                    cursor = self.conn.execute(
                        "DELETE FROM spec_files WHERE relative_path LIKE ?",
                        (f"{dir_prefix}%",)
                    )
                    deleted_count = cursor.rowcount
                    logger.info(f"成功从数据库中删除 {deleted_count} 条与 '{dir_prefix}' 相关的记录。")
            except sqlite3.Error as e:
                logger.error(f"从数据库删除目录 '{dir_prefix}' 失败: {e}")


    async def start(self):
        """
        启动扫描器服务，执行一次初始扫描。
        """
        await self.scan_specs()

    async def shutdown(self):
        """
        安全地关闭扫描器服务。
        """
        logger.info("正在关闭规程规范扫描器...")
        self.conn.close()
        self.executor.shutdown(wait=True)
        logger.info("规程规范扫描器已关闭。")
