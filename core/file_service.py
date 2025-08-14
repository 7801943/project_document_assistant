# core/file_service.py

from anyio import to_thread, open_file
import shutil
import tempfile
import zipfile
import tarfile
from pathlib import Path
from typing import List, IO, Dict, Any, AsyncGenerator
from datetime import datetime

from fastapi import UploadFile
from fastapi.responses import FileResponse
from loguru import logger

class FileService:
    """
    一个封装了高级文件系统操作的统一服务。
    它确保所有操作都在一个安全的根目录下进行，并提供健壮的、异步的、高级的API。
    设计原则：
    1. 安全性：所有路径都经过校验，防止路径遍历攻击。
    2. 原子性：写操作默认采用“临时文件 -> 移动”模式，保证数据一致性。
    3. 异步化：所有阻塞的磁盘I/O操作都通过anyio在工作线程中执行，避免阻塞主事件循环。
    4. 高内聚：将文件管理相关的逻辑（存储、备份、监控等）集中于此。
    """

    def __init__(self, root_dir: str | Path):
        """
        使用一个根目录初始化文件服务。
        :param root_dir: 所有文件操作将被限制在此目录内。
        """
        self.root_dir = Path(root_dir).resolve()
        # 临时目录位于项目根目录（假定此文件在 core/ 目录下）
        self.temp_dir = Path(__file__).parent.parent / 'tempfile'
        try:
            self.root_dir.mkdir(parents=True, exist_ok=True)
            self.temp_dir.mkdir(exist_ok=True)
            logger.info(f"FileService 初始化完成。根目录: '{self.root_dir}', 临时目录: '{self.temp_dir}'")
        except (IOError, OSError) as e:
            logger.critical(f"FileService 初始化失败：无法创建或访问目录。错误: {e}")
            raise

    def _get_safe_path(self, relative_path: str | Path) -> Path:
        """
        将相对路径解析为根目录下的安全绝对路径，并防止路径遍历攻击。
        """
        # 确保 relative_path 是相对的
        if Path(relative_path).is_absolute():
            raise ValueError(f"不接受绝对路径，但收到了: '{relative_path}'")

        # 解析路径
        safe_path = (self.root_dir / relative_path).resolve()

        # 验证解析后的路径是否仍在 root_dir 内
        if self.root_dir not in safe_path.parents and safe_path != self.root_dir:
            raise ValueError(f"检测到路径遍历尝试: '{relative_path}' 解析到了安全区域之外。")

        return safe_path

    # --- 核心文件操作 ---

    async def save_uploaded_file_async(self, file: UploadFile, relative_path: str) -> Path:
        """
        异步、安全地保存一个上传的文件。
        """
        safe_path = self._get_safe_path(relative_path)
        await to_thread.run_sync(lambda: safe_path.parent.mkdir(parents=True, exist_ok=True))

        # 使用 anyio.to_thread.run_sync 将同步的I/O操作移到工作线程
        await to_thread.run_sync(self._save_stream_to_temp_and_move, file.file, safe_path)
        logger.debug(f"已成功保存上传的文件到: {safe_path}")
        return safe_path

    async def save_content_async(self, content: bytes, relative_path: str) -> Path:
        """
        异步、安全地将二进制内容写入文件。
        """
        safe_path = self._get_safe_path(relative_path)
        await to_thread.run_sync(lambda: safe_path.parent.mkdir(parents=True, exist_ok=True))

        await to_thread.run_sync(self._save_bytes_to_temp_and_move, content, safe_path)
        logger.debug(f"已成功将内容写入文件: {safe_path}")
        return safe_path

    def _save_stream_to_temp_and_move(self, source_stream: IO, final_path: Path):
        """[同步] 将流写入临时文件，然后原子性地移动它。"""
        with tempfile.NamedTemporaryFile(dir=self.temp_dir, delete=False) as tmp:
            shutil.copyfileobj(source_stream, tmp)
            temp_path = tmp.name
        shutil.move(temp_path, final_path)

    def _save_bytes_to_temp_and_move(self, content: bytes, final_path: Path):
        """[同步] 将字节内容写入临时文件，然后原子性地移动它。"""
        with tempfile.NamedTemporaryFile(dir=self.temp_dir, delete=False) as tmp:
            tmp.write(content)
            temp_path = tmp.name
        shutil.move(temp_path, final_path)

    async def get_file_response_async(self, relative_path: str) -> FileResponse:
        """
        异步获取一个用于下载的 FastAPI FileResponse 对象。
        """
        safe_path = self._get_safe_path(relative_path)
        if not await to_thread.run_sync(safe_path.is_file):
            raise FileNotFoundError(f"文件未找到: {relative_path}")
        return FileResponse(path=safe_path, filename=safe_path.name)

    async def read_file_stream_async(self, relative_path: str) -> AsyncGenerator[bytes, None]:
        """
        [修正] 异步读取文件，并以二进制流的形式返回。
        适合 FastAPI 的 StreamingResponse。
        """
        safe_path = self._get_safe_path(relative_path)
        if not await to_thread.run_sync(safe_path.is_file):
            raise FileNotFoundError(f"文件未找到: {relative_path}")

        async def file_iterator(path: Path, chunk_size: int = 8192) -> AsyncGenerator[bytes, None]:
            """一个异步生成器，用于块式读取文件。"""
            async with await open_file(path, "rb") as f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return file_iterator(safe_path)

    def read_file_bytes_sync(self, relative_path: str) -> bytes:
        """
        [新增] 同步读取并返回文件的所有二进制内容。
        """
        safe_path = self._get_safe_path(relative_path)
        if not safe_path.is_file():
            raise FileNotFoundError(f"文件未找到: {relative_path}")
        return safe_path.read_bytes()

    async def remove_directory_async(self, relative_path: str | Path):
        """异步、安全地递归删除目录。"""
        safe_path = self._get_safe_path(relative_path)
        if await to_thread.run_sync(safe_path.is_dir):
            await to_thread.run_sync(
                lambda : shutil.rmtree(safe_path, ignore_errors=True)
            )
            logger.info(f"已成功删除目录: {safe_path}")

    async def create_directory_async(self, relative_path: str | Path):
        """异步、安全地创建一个空目录（如果不存在）。"""
        safe_path = self._get_safe_path(relative_path)
        if not await to_thread.run_sync(safe_path.exists):
            await to_thread.run_sync(lambda: safe_path.mkdir(parents=True, exist_ok=True))
            logger.info(f"已成功创建目录: {safe_path}")

    async def create_placeholder_file_async(self, relative_dir_path: str | Path, filename: str = "placeholder.txt"):
        """异步、安全地在指定目录中创建一个空的占位文件。"""
        dir_path = self._get_safe_path(relative_dir_path)
        # 确保目录存在
        if not await to_thread.run_sync(dir_path.is_dir):
            await to_thread.run_sync(lambda: dir_path.mkdir(parents=True, exist_ok=True))
        
        placeholder_path = dir_path / filename
        if not await to_thread.run_sync(placeholder_path.exists):
            await to_thread.run_sync(lambda: placeholder_path.touch())
            logger.info(f"已成功创建占位文件: {placeholder_path}")

    # --- 路径查询 ---

    async def file_exists_async(self, relative_path: str | Path) -> bool:
        """
        [新增] 异步、安全地检查文件是否存在。
        """
        try:
            safe_path = self._get_safe_path(relative_path)
            return await to_thread.run_sync(safe_path.is_file)
        except ValueError:
            # 如果路径无效（例如，路径遍历），则视为不存在
            return False

    async def directory_exists_async(self, relative_path: str | Path) -> bool:
        """
        [新增] 异步、安全地检查目录是否存在。
        """
        try:
            safe_path = self._get_safe_path(relative_path)
            return await to_thread.run_sync(safe_path.is_dir)
        except ValueError:
            # 如果路径无效，则视为不存在
            return False

    # --- 新增功能 ---

    async def save_uploaded_directory_async(self, files: List[UploadFile], relative_dir: str) -> Path:
        """
        [修正] 异步、安全地将多个上传的文件保存到指定目录，并保留其相对路径结构。
        """
        base_dest_dir = self._get_safe_path(relative_dir)
        # 确保基础目标目录存在
        await to_thread.run_sync(lambda: base_dest_dir.mkdir(parents=True, exist_ok=True))

        saved_files: List[Path] = []
        try:
            for file in files:
                if not file.filename:
                    continue

                # file.filename 包含前端提供的相对路径，例如 "MyProject/subfolder/file.txt"
                # 我们直接使用这个相对路径来构建最终的目标路径
                # Path() 会自动处理不同操作系统的路径分隔符
                file_relative_path = Path(relative_dir) / file.filename

                # 调用现有的文件保存方法，它内部有安全检查
                saved_path = await self.save_uploaded_file_async(file, str(file_relative_path))
                saved_files.append(saved_path)

            logger.info(f"已成功将 {len(saved_files)} 个文件保存到目录: {base_dest_dir}")
            return base_dest_dir
        except Exception as e:
            logger.error(f"保存文件到目录 '{relative_dir}' 时出错: {e}")
            # 如果出错，尝试回滚（删除已保存的文件）
            for path in saved_files:
                # unlink 可能会失败，但我们还是要继续尝试删除其他文件
                await to_thread.run_sync(lambda: path.unlink(missing_ok=True))
            raise

    async def get_disk_usage_async(self) -> Dict[str, Any]:
        """
        [新增] 异步获取根目录所在磁盘的用量信息。
        """
        usage = await to_thread.run_sync(shutil.disk_usage, self.root_dir)
        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "total_gb": f"{usage.total / (1024**3):.2f} GB",
            "used_gb": f"{usage.used / (1024**3):.2f} GB",
            "free_gb": f"{usage.free / (1024**3):.2f} GB",
        }

    async def decompress_archive_async(self, relative_archive_path: str, overwrite: bool = False) -> Path:
        """
        [新增] 异步解压缩文件 (.zip, .tar, .tar.gz) 到同名目录。
        """
        safe_archive_path = self._get_safe_path(relative_archive_path)

        # 创建目标解压目录，以压缩文件名（不含扩展名）命名
        dest_dir = safe_archive_path.with_suffix('')

        if dest_dir.exists() and not overwrite:
            raise FileExistsError(f"目标目录 '{dest_dir.name}' 已存在。请使用 overwrite=True 进行覆盖。")

        if dest_dir.exists() and overwrite:
            await self.remove_directory_async(dest_dir.relative_to(self.root_dir))

        await to_thread.run_sync(lambda: dest_dir.mkdir(parents=True, exist_ok=True))

        await to_thread.run_sync(self._decompress_sync, safe_archive_path, dest_dir)
        logger.info(f"已成功将 '{safe_archive_path.name}' 解压到 '{dest_dir.name}'")
        return dest_dir

    def _decompress_sync(self, archive_path: Path, dest_dir: Path):
        """[同步] 解压缩逻辑的实现。"""
        suffix = archive_path.suffix.lower()
        if suffix == '.zip':
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(dest_dir)
        elif suffix in ['.gz', '.tar']:
            # tarfile 可以自动处理 .gz
            with tarfile.open(archive_path, 'r:*') as tar_ref:
                tar_ref.extractall(dest_dir)
        else:
            # 对于 .rar 等其他格式，需要外部依赖，此处仅作提示
            raise NotImplementedError(f"不支持的压缩格式: '{suffix}'。仅支持 .zip, .tar, .tar.gz。")

    async def backup_directory_async(self, relative_path_to_backup: str, backup_destination_dir: str | Path) -> Path:
        """
        [新增] 异步备份指定目录，生成一个带时间戳的 .zip 压缩文件。
        :param relative_path_to_backup: 在服务根目录下需要备份的相对路径。
        :param backup_destination_dir: 存放备份文件的目标目录（绝对路径或相对工作区的路径）。
        :return: 创建的备份文件的路径。
        """
        source_dir = self._get_safe_path(relative_path_to_backup)

        # 确保备份目标目录存在
        backup_dest = Path(backup_destination_dir).resolve()
        await to_thread.run_sync(lambda: backup_dest.mkdir(parents=True, exist_ok=True))

        # 生成带时间戳的文件名
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_filename = f"backup-{source_dir.name}-{timestamp}"

        # shutil.make_archive 会自动添加 .zip 后缀
        archive_path_without_suffix = backup_dest / backup_filename

        # 在工作线程中执行压缩
        final_archive_path_str = await to_thread.run_sync(
            lambda: shutil.make_archive(
                base_name=str(archive_path_without_suffix),
                format='zip',
                root_dir=source_dir
                )
        )

        final_archive_path = Path(final_archive_path_str)
        logger.info(f"已成功将目录 '{source_dir.name}' 备份到 '{final_archive_path}'")
        return final_archive_path
