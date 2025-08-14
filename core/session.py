import os
import asyncio
from pathlib import Path
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union, Tuple
import uuid

from fastapi import WebSocket
from loguru import logger
from config import settings

from core.data_model import DocType
from utils.utils import calculate_md5

@dataclass
class FileEntry:
    """
    文件条目类，用于注册文件token

    Attributes:
        expire_at: 过期时间戳
        opened_by_llm: 是否被LLM打开
        opened_by_user: 是否被用户打开
        doc_type: 文档类型
        file_path: 文件路径
        token: 访问令牌
    """
    expire_at: float
    opened_by_llm: bool
    opened_by_user: bool
    doc_type: Optional[DocType] = DocType.PROJECT # 默认为项目目录
    file_path: str = ""
    token: str = ""


@dataclass
class DirEntry:
    '''
    注册目录token
    '''
    directory: str                          # 管理的目录
    expire_at: float                       # 整个会话的过期时
    directory_token: str= ""
    files: List[FileEntry] = field(default_factory=list)


@dataclass
class UserSessionData:
    """
    封装了单个用户所有会话相关的数据。
    """
    username: str
    session_id: str
    ip_address: str
    login_time: float
    login_time_str: str
    last_activity_time: float  # HTTP活动的最后时间
    last_activity_time_str: str
    # --- 统一 'key' 为 'file_key' ---
    editing_file: dict[str,str] = field(default_factory= lambda: {"user_id":uuid.uuid4().hex[0:8], "file_key":"","file_path":""})
    is_websocket_connected: bool = False
    websocket: Optional[WebSocket] = None  # 直接持有WebSocket对象
    working_directory: Optional[DirEntry] = None # 修复：DirEntry需要参数，不能用default_factory
    working_files: List[FileEntry] = field(default_factory=list)  # 存储通过LLM打开的文件信息

# --- 会话状态与连接管理器合并类 ---

class SessionStateManager:
    """
    一个统一的管理器，负责处理用户的会话状态、HTTP活动、WebSocket连接以及其他相关数据。
    此类合并了原有的 ConnectionManager 的功能。
    """
    def __init__(self, inactivity_timeout: int):
        # Dict[用户名，会话数据]
        self._user_sessions: Dict[str, UserSessionData] = {}
        self._lock = asyncio.Lock()
        self._inactivity_timeout = inactivity_timeout
        logger.info(f"SessionStateManager已初始化。整体HTTP不活动超时: {inactivity_timeout}秒")

    # --- WebSocket 连接管理 (原ConnectionManager功能) ---

    async def connect_websocket(self, websocket: WebSocket, username: str, session_id: str):
        """
        处理新的WebSocket连接请求，并将其与现有用户会话关联。
        """
        async with self._lock:
            user_data = self._user_sessions.get(username)
            if user_data and user_data.session_id == session_id:
                await websocket.accept()
                user_data.websocket = websocket
                user_data.is_websocket_connected = True
                logger.info(f"用户 '{username}' (会话: {session_id}) 的 WebSocket 已连接并关联。")
            else:
                logger.warning(f"WebSocket 连接尝试失败: 用户 '{username}' 的会话(ID: {session_id}) 无效或不匹配。")
                # 主动关闭这个未经授权的ws连接
                await websocket.close(code=1008, reason="Invalid session")

    async def disconnect_websocket(self, username: str):
        """
        处理WebSocket断开连接的逻辑。
        """
        async with self._lock:
            user_data = self._user_sessions.get(username)
            if user_data:
                user_data.websocket = None
                user_data.is_websocket_connected = False
                logger.info(f"用户 '{username}' 的 WebSocket 已断开。")

    # --- 用户登录与登出 ---

    async def attempt_login(self, username: str, ip_address: str, new_session_id: str) -> bool:
        """
        处理用户登录请求，包含排他性登录检查。
        """
        async with self._lock:
            current_time = time.time()
            current_time_str = time.ctime(current_time)

            existing_user_data = self._user_sessions.get(username)
            if existing_user_data:
                is_active_elsewhere = False
                # 检查旧会话是否仍在活动有效期内
                if (current_time - existing_user_data.last_activity_time) < self._inactivity_timeout:
                    is_active_elsewhere = True
                    logger.debug(f"用户 {username} 的旧会话 {existing_user_data.session_id} 最近有HTTP活动。")

                if is_active_elsewhere:
                    logger.warning(f"登录尝试失败: 用户 '{username}' 已在会话'{existing_user_data.session_id}' (IP: {existing_user_data.ip_address}) 活动。新尝试来自 IP: {ip_address}, 会话: {new_session_id}。")
                    return False

            # 登出旧会话（如果存在）并创建新会话
            self._user_sessions[username] = UserSessionData(
                username=username,
                session_id=new_session_id,
                ip_address=ip_address,
                login_time=current_time,
                login_time_str=current_time_str,
                last_activity_time=current_time,
                last_activity_time_str=current_time_str,
            )
            logger.info(f"用户 '{username}' (会话: {new_session_id}, IP: {ip_address}) 登录/状态更新成功。")
            return True

    async def logout_user(self, username: str):
        """
        处理用户登出，同时清理会话数据和关联的WebSocket连接。
        """
        async with self._lock:
            if username in self._user_sessions:
                user_data = self._user_sessions.pop(username)
                logger.debug(f"用户 '{username}' (会话: {user_data.session_id}) 已从状态管理器登出。")
                if user_data.websocket:
                    logger.debug(f"用户 '{username}' 登出时，其WebSocket连接处于活动状态，将尝试关闭。")
                    try:
                        await user_data.websocket.close(code=1000, reason="用户已登出")
                    except Exception as e:
                        logger.warning(f"登出时关闭用户 '{username}' 的WebSocket连接时发生错误: {e}")
            else:
                logger.warning(f"尝试登出不存在或已登出的用户: {username}")

    # --- 状态获取与更新 ---

    async def get_user_data(self, username: str) -> Optional[UserSessionData]:
        """安全地获取指定用户的数据。"""
        async with self._lock:
            return self._user_sessions.get(username)

    async def get_username_by_session_id(self, session_id_to_find: str) -> Optional[str]:
        """
        根据 session_id 查找并返回关联的用户名。
        """
        async with self._lock:
            for username, user_data in self._user_sessions.items():
                if user_data.session_id == session_id_to_find:
                    return username
            return None

    async def set_http_activity(self, username: str):
        """记录用户的HTTP活动，更新最后活动时间。"""
        async with self._lock:
            user_data = self._user_sessions.get(username)
            if user_data:
                current_time = time.time()
                user_data.last_activity_time = current_time
                user_data.last_activity_time_str = time.ctime(current_time)
                logger.trace(f"用户 '{username}' HTTP 活动已记录，last_activity_time 更新为: {user_data.last_activity_time_str}。")

    async def clear_working_directory(self, username: str):
        """清空指定用户的当前工作目录信息。"""
        async with self._lock:
            user_data = self._user_sessions.get(username)
            if user_data:
                if user_data.working_directory:
                    logger.info(f"正在为用户 '{username}' 清理工作目录 '{user_data.working_directory.directory}'。")
                    user_data.working_directory = None
                else:
                    logger.debug(f"用户 '{username}' 没有需要清理的工作目录。")
            else:
                logger.warning(f"尝试为一个不存在的用户 '{username}' 清理工作目录。")

    # --- 文件相关操作 ---
    async def set_edited_file(self, user:str, file_key: str, file_path:str) -> bool:
        async with self._lock:
            user_session_data = self._user_sessions.get(user)
            if not user_session_data:
                logger.error(f"错误：尝试为未知用户 '{user}' 执行文件编辑注册'。")
                return False
            user_session_data.editing_file['file_key'] = file_key
            user_session_data.editing_file['file_path'] = file_path
            logger.debug(f"成功为文件{file_path}进行编辑注册,file_key:{file_key}")
            return True

    async def get_editing_file(self, file_key:str) -> str:
        async with self._lock:
            for username, user_data in self._user_sessions.items():
                if user_data.editing_file.get('file_key') == file_key:
                    result = user_data.editing_file['file_path']
                    logger.debug(f"成功获取文件编辑file_key:{file_key},文件路径:{result}")
                    return result

            # 用户会话中未找到key
            logger.error(f"错误：未在任何用户会话数据中找到file_key:{file_key}，无法获取文件路径")
            return ""

    async def register_editing_file(self, user:str, file_path:str, doc_type:DocType) -> Tuple[Optional[str], Optional[str]]:
        """
        为协同编辑注册文件。
        如果文件已在编辑中，则复用现有的file_key；否则创建新key。
        为每个加入的用户返回一个唯一的user_id和共享的file_key。
        """
        async with self._lock:
            file_key = ""
            # 1. 查找是否已有其他用户在编辑此文件
            for username, user_data in self._user_sessions.items():
                if user_data.editing_file.get('file_path') == file_path:
                    file_key = user_data.editing_file.get('file_key', "")
                    if file_key:
                        logger.debug(f"文件 '{file_path}' 已在编辑中，复用 file_key: {file_key}")
                        break

            # 2. 为当前用户注册编辑信息
            current_user_data = self._user_sessions.get(user)
            if not current_user_data:
                logger.warning(f"错误：注册新的编辑文件失败，用户名 '{user}' 无效")
                return None, None

            # 3. 分配 user_id 和 file_key
            user_id = uuid.uuid4().hex[0:8] # 为每个协作者生成唯一的ID
            if not file_key:
                file_key = uuid.uuid4().hex[0:12] # 如果是第一个编辑者，创建新的key
                logger.debug(f"文件 '{file_path}' 的新编辑会话开始，生成新 file_key: {file_key}")

            current_user_data.editing_file['file_path'] = file_path
            current_user_data.editing_file['user_id'] = user_id
            current_user_data.editing_file['file_key'] = file_key

            logger.debug(f"注册编辑文件成功, user:'{user}', user_id:'{user_id}', file_path:'{file_path}', file_key:'{file_key}'")
            return user_id, file_key

    async def remove_edited_file(self, file_key:str):
        async with self._lock:
            for username, user_data in self._user_sessions.items():
                if user_data.editing_file.get('file_key') == file_key:
                    logger.info(f"为用户 '{username}' 移除文件编辑状态 (file_key: {file_key}, path: {user_data.editing_file.get('file_path')})")
                    # 清除该用户的编辑状态。在多人场景下，其他用户的状态不受影响。
                    # 注意：这依赖于OnlyOffice回调机制。当文档最终保存时，可能需要一个更集中的方式来清理所有相关的编辑状态。
                    user_data.editing_file['file_key'] = ""
                    user_data.editing_file['file_path'] = ""
                    # 这里只清除了一个用户的状态，如果需要可以返回，但目前不需要
            # logger.warning(f"未在任何用户会话数据中找到file_key:{file_key}，无法移除。这在多人关闭时是正常现象。")

    async def update_opened_file(self, user: str, relative_file_path: Union[Path, str], llm_opened: bool, document_type: DocType) -> Optional[FileEntry]:
        """
        [重构后] 更新用户打开的单个文件，在内部生成token，并返回创建的FileEntry。
        调用者仅需提供文件路径等基本信息。

        Args:
            user (str): 用户名。
            relative_file_path (Union[Path, str]): 文件的相对路径。
            llm_opened (bool): 文件是否由LLM打开。
            document_type (DocType): 文档类型。

        Returns:
            Optional[FileEntry]: 成功时返回创建的文件条目对象，失败则返回None。
        """
        async with self._lock:
            user_data = self._user_sessions.get(user)
            if not user_data:
                logger.warning(f"尝试为未知用户 '{user}' 注册已打开文件 '{relative_file_path}'。")
                return None

            # 内部生成token
            token = uuid.uuid4().hex
            current_time = time.time()
            expires_time = current_time + settings.DOWNLOAD_LINK_VALIDITY_SECONDS

            # 创建文件条目
            file_entry = FileEntry(
                file_path=str(relative_file_path),
                token=token,
                opened_by_llm=llm_opened,
                opened_by_user=True,  # 假设立即被用户打开
                expire_at=expires_time,
                doc_type=document_type
            )
            
            # 将文件条目添加到用户的会话数据中
            user_data.working_files.append(file_entry)
            logger.info(f"用户 '{user}' 已打开文件 '{relative_file_path}' (token: {token}), 有效期至: {time.ctime(expires_time)}")

            # 通过WebSocket通知前端
            if user_data.is_websocket_connected and user_data.websocket:
                file_basename = os.path.basename(relative_file_path)
                file_format = file_basename.split('.')[-1].lower() if '.' in file_basename else 'txt'
                try:
                    await user_data.websocket.send_json({
                        "type": "file_open_request",
                        "payload": {
                            "filename": str(relative_file_path),
                            "download_token": token,
                            "format": file_format
                        }
                    })
                except Exception as e:
                    logger.error(f"向用户 '{user}' 发送WebSocket文件打开消息时出错: {e}", exc_info=True)
            else:
                logger.warning(f"用户 '{user}' 的WebSocket未连接，无法发送文件打开通知。")
            
            # 返回创建的文件条目，包含新生成的token
            return file_entry

    async def update_opened_dir(self, user: str, dir_path: str, files: List[str]) -> Optional[DirEntry]:
        """
        [重构后] 新增或完全覆盖用户的整个工作目录视图。
        此方法在内部生成目录和文件的token，并返回创建的DirEntry。

        Args:
            user (str): 用户名。
            dir_path (str): 目录的路径。
            files (List[str]): 目录下的文件相对路径列表。

        Returns:
            Optional[DirEntry]: 成功时返回创建的目录条目对象，失败则返回None。
        """
        async with self._lock:
            user_data = self._user_sessions.get(user)
            if not user_data:
                logger.warning(f"尝试为未知用户 '{user}' 更新工作目录 '{dir_path}'。")
                return None

            current_time = time.time()
            expires_time = current_time + settings.DOWNLOAD_LINK_VALIDITY_SECONDS
            
            # 内部生成目录token
            dir_token = uuid.uuid4().hex

            # 为每个文件创建FileEntry并生成token
            file_entries = []
            for file_path in files:
                entry = FileEntry(
                    file_path=str(file_path),
                    token=uuid.uuid4().hex, # 内部生成文件token
                    opened_by_llm=False,
                    opened_by_user=False,
                    expire_at=expires_time,
                    doc_type=DocType.PROJECT
                )
                file_entries.append(entry)

            # 创建新的目录条目
            new_dir_entry = DirEntry(
                directory=dir_path,
                directory_token=dir_token,
                expire_at=expires_time,
                files=file_entries
            )
            user_data.working_directory = new_dir_entry
            logger.info(f"用户 '{user}' 的工作目录已更新为 '{dir_path}'，包含 {len(file_entries)} 个文件。")

            # 通过WebSocket通知前端更新整个目录视图
            if user_data.is_websocket_connected and user_data.websocket:
                files_payload = [{
                    "filename": os.path.basename(entry.file_path),
                    "file_path": entry.file_path,
                    "download_token": entry.token,
                    "format": os.path.basename(entry.file_path).split('.')[-1].lower() if '.' in os.path.basename(entry.file_path) else 'txt'
                } for entry in file_entries]

                try:
                    await user_data.websocket.send_json({
                        "type": "directory_update",
                        "payload": {
                            "directory": dir_path,
                            "directory_token": dir_token,
                            "files": files_payload
                        }
                    })
                    logger.info(f"已向用户 '{user}' 发送工作目录更新通知。")
                except Exception as e:
                    logger.error(f"向用户 '{user}' 发送WebSocket目录更新消息时出错: {e}", exc_info=True)
            else:
                logger.warning(f"用户 '{user}' 的WebSocket未连接，无法发送目录更新通知。")
            
            # 返回创建的目录条目，包含所有新生成的token
            return new_dir_entry

    async def get_downloadable_file_info(self, token_to_find: str) -> Optional[Dict[str, Any]]:
        """根据token查找有效的、未过期的可下载文件信息。"""
        async with self._lock:
            for username, user_data in self._user_sessions.items():
                # 首先在 working_files 中查找
                if user_data.working_files:
                    found_entry = next((entry for entry in user_data.working_files if entry.token == token_to_find), None)
                    if found_entry:
                        filename = os.path.basename(found_entry.file_path)
                        # logger.info(f"下载token '{token_to_find}' (文件: {filename}, 用户: {username}) 在 working_files 中验证成功。")
                        if found_entry.doc_type == DocType.PROJECT:
                            absolute_path = os.path.join(settings.PROJECTS_ROOT_DIR, found_entry.file_path)
                        elif found_entry.doc_type == DocType.STANDARD:
                            absolute_path = os.path.join(settings.SPEC_ROOT_DIR, found_entry.file_path)
                        else:
                            # 其他路径
                            logger.error(f"文档类型 '{found_entry.doc_type}，类型{type(found_entry.doc_type)}' 无效。")
                            return None
                        return {
                            'token': found_entry.token,
                            'path': found_entry.file_path,
                            'filename': filename,
                            'absolute_path': absolute_path,
                            'expires_at': found_entry.expire_at
                        }

                # 如果在 working_files 中未找到，则在 working_directory.files 中查找
                if user_data.working_directory and user_data.working_directory.files:
                    found_entry = next((entry for entry in user_data.working_directory.files if entry.token == token_to_find), None)
                    if found_entry:
                        filename = os.path.basename(found_entry.file_path)
                        logger.info(f"下载token '{token_to_find}' (文件: {filename}, 用户: {username}) 在 working_directory 中验证成功。")
                        absolute_path = os.path.join(settings.PROJECTS_ROOT_DIR, found_entry.file_path)
                        return {
                            'token': found_entry.token,
                            'path': found_entry.file_path,
                            'filename': filename,
                            'absolute_path': absolute_path,
                            'expires_at': found_entry.expire_at
                        }

            logger.warning(f"下载token '{token_to_find}' 在任何活动用户会话中均未找到。")
            return None

    # --- 定时清理任务 ---

    async def cleanup_expired_opened_files(self):
        """定时任务：清理所有用户会话中已过期的文件和目录条目。"""
        async with self._lock:
            current_time = time.time()
            for username, user_data in self._user_sessions.items():
                # 1. 清理 working_files 中过期的 FileEntry
                initial_files_count = len(user_data.working_files)
                user_data.working_files = [
                    entry for entry in user_data.working_files
                    if current_time < entry.expire_at
                ]
                cleaned_files_count = initial_files_count - len(user_data.working_files)
                if cleaned_files_count > 0:
                    logger.info(f"为用户 '{username}' 清理了 {cleaned_files_count} 个过期的单独文件条目。")

                # 2. 检查并清理过期的 working_directory
                if user_data.working_directory and current_time >= user_data.working_directory.expire_at:
                    expired_dir_path = user_data.working_directory.directory
                    user_data.working_directory = None
                    logger.info(f"用户 '{username}' 的工作目录 '{expired_dir_path}' 因过期已被清理。")

    async def process_inactive_sessions(self):
        """定时任务：处理并登出所有因长时间不活动而超时的用户会话。"""
        async with self._lock:
            current_time = time.time()
            users_to_logout = [
                username for username, data in self._user_sessions.items()
                if (current_time - data.last_activity_time) >= self._inactivity_timeout
            ]

            if not users_to_logout:
                return

            logger.info(f"检测到 {len(users_to_logout)} 个不活动会话，将执行登出: {', '.join(users_to_logout)}")
            for username in users_to_logout:
                # logout_user 方法已经是异步且线程安全的，但这里我们在一个循环中调用它
                # 并且我们已经持有锁，所以直接调用内部逻辑以避免死锁
                if username in self._user_sessions:
                    user_data = self._user_sessions.pop(username)
                    logger.info(f"APScheduler: 用户 '{username}' (会话 {user_data.session_id}) 因不活动被自动登出。")
                    if user_data.websocket:
                        logger.info(f"APScheduler: 尝试关闭用户 '{username}' 的不活动WebSocket连接。")
                        try:
                            # 需要在一个新的任务中运行，以避免阻塞事件循环
                            asyncio.create_task(user_data.websocket.close(code=1001, reason="会话因不活动而超时"))
                        except Exception as e:
                            logger.warning(f"APScheduler: 关闭用户 '{username}' 的WebSocket时发生错误: {e}")

    # --- 调试接口 ---

    async def get_all_user_data_for_debug(self) -> Dict[str, Dict[str, Any]]:
        """获取所有当前用户会话的调试信息。"""
        async with self._lock:
            debug_data = {}
            for uname, udata in self._user_sessions.items():
                working_dir_info = None
                if udata.working_directory:
                    working_dir_info = {
                        "path": udata.working_directory.directory,
                        "files_count": len(udata.working_directory.files),
                        "expires_at": time.ctime(udata.working_directory.expire_at)
                    }

                debug_data[uname] = {
                    "username": udata.username,
                    "session_id": udata.session_id,
                    "ip_address": udata.ip_address,
                    "login_time_str": udata.login_time_str,
                    "last_activity_time_str": udata.last_activity_time_str,
                    "is_websocket_connected": udata.is_websocket_connected,
                    "has_websocket_object": udata.websocket is not None,
                    "opened_files_count": len(udata.working_files),
                    "working_directory": working_dir_info,
                    "editing_file": udata.editing_file
                }
            return debug_data
