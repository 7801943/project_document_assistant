import os
import asyncio
from pathlib import Path
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union, Tuple


from fastapi import WebSocket
from loguru import logger
from config import settings

from core.data_model import DocType


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
    editing_file: dict[str,str] = field(default_factory= lambda: {"key":"","file_path":""})
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
                logger.info(f"用户 '{username}' (会话: {user_data.session_id}) 已从状态管理器登出。")
                if user_data.websocket:
                    logger.info(f"用户 '{username}' 登出时，其WebSocket连接处于活动状态，将尝试关闭。")
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

    # --- 文件相关操作 ---
    async def set_edited_file(self, user:str, key: str, file_path:str) -> bool:
        async with self._lock:
            user_session_data = self._user_sessions.get(user)
            if not user_session_data:
                logger.error(f"错误：尝试为未知用户 '{user}' 执行文件编辑注册'。")
                return False
            user_session_data.editing_file['key'] = key
            user_session_data.editing_file['file_path'] = file_path
            logger.info(f"成功为文件{file_path}进行编辑注册,key:{key}")
            return True

        # --- 文件相关操作 ---
    async def get_edited_file(self, key:str) -> str:
        async with self._lock:
            for username, user_data in self._user_sessions.items():
                if user_data.editing_file['key'] == key:
                    result = user_data.editing_file['file_path']
                    logger.info(f"成功获取文件编辑key:{key},文件路径:{result}")
                    return result

            # 用户会话中未找到key
            logger.error(f"错误：未在任何用户会话数据中找到key:{key}，无法文件路径")
            return ""

    async def remove_edited_file(self, key:str):
        async with self._lock:
            for username, user_data in self._user_sessions.items():
                if user_data.editing_file['key'] == key:
                    logger.info(f"成功为用户{username}移除文件编辑key:{key},文件路径:{user_data.editing_file['file_path']}")
                    user_data.editing_file['key'] = ""
                    user_data.editing_file['file_path'] = ""
                    return
            logger.error(f"错误：未在任何用户会话数据中找到key:{key}，无法移除")

    # llm_opend 必须有默认参数，否则在声明时必须放到所有参数之前
    async def update_opened_file(self, user:str, token: str, relative_file_path: Union[Path,str], llm_opened: bool, document_type:DocType):
        '''
        # 更新用户打开的文件
        # 通过socket发送请求
        '''
        async with self._lock:
            user_data = self._user_sessions.get(user)
            if not user_data:
                logger.warning(f"尝试为未知用户 '{user}' 注册已打开文件 '{relative_file_path}'。")
                return

            current_time = time.time()
            expires_time = current_time + settings.DOWNLOAD_LINK_VALIDITY_SECONDS

            file_entry = FileEntry(
                file_path = str(relative_file_path),
                token = token,
                opened_by_llm = llm_opened,
                # 其实要依赖与前后端正确工作
                opened_by_user= True,
                expire_at = expires_time,
                doc_type = document_type
            )
            # 暂不考虑重复
            user_data.working_files.append(file_entry)
            logger.info(f"用户 '{user}' 已打开文件 '{relative_file_path}' (token: {token}), 有效期至: {time.ctime(expires_time)}")
            # 通过socket发送文件打开请求
            if user_data.is_websocket_connected and user_data.websocket:
                file_basename = os.path.basename(relative_file_path)
                file_format = file_basename.split('.')[-1].lower() if '.' in file_basename else 'txt'
                await user_data.websocket.send_json({
                    "type": "file_open_request",
                    "payload": {
                        "filename": str(relative_file_path),
                        "download_token": token,
                        "format": file_format
                    }
                })
            else:
                logger.warning(f"用户 '{user}' 的WebSocket未连接，无法发送文件打开通知。")

    async def update_opened_dir(self, user: str, dir_path: str, dir_token: str, files_with_token: List[Tuple[str, str]]):
        """
        新增或完全覆盖用户的整个工作目录视图。
        这个操作是破坏性的，会替换掉旧的目录和文件列表。
        """
        async with self._lock:
            user_data = self._user_sessions.get(user)
            if not user_data:
                logger.warning(f"尝试为未知用户 '{user}' 更新工作目录 '{dir_path}'。")
                return

            current_time = time.time()
            expires_time = current_time + settings.DOWNLOAD_LINK_VALIDITY_SECONDS

            # 1. 根据传入的文件列表，创建FileEntry对象
            file_entries = []
            for file_path, file_token in files_with_token:
                entry = FileEntry(
                    file_path=str(file_path),
                    token=file_token,
                    opened_by_llm=False,  # 假设由LLM打开
                    opened_by_user=False, # 等待前端确认
                    expire_at=expires_time,
                    doc_type= DocType.PROJECT # 目录打开暂时只考虑项目文件
                )
                file_entries.append(entry)

            # 2. 创建一个新的DirEntry对象，并用它覆盖旧的
            new_dir_entry = DirEntry(
                directory=dir_path,
                directory_token=dir_token,
                expire_at=expires_time, # 目录的token也设置过期时间
                files=file_entries
            )
            user_data.working_directory = new_dir_entry

            logger.info(f"用户 '{user}' 的工作目录已更新为 '{dir_path}'，包含 {len(file_entries)} 个文件。")

            # 通过WebSocket通知前端更新整个目录视图
            if user_data.is_websocket_connected and user_data.websocket:
                # 构造前端需要的文件列表
                files_payload = []
                for entry in file_entries:
                    file_basename = os.path.basename(entry.file_path)
                    file_format = file_basename.split('.')[-1].lower() if '.' in file_basename else 'txt'
                    files_payload.append({
                        "filename": file_basename,
                        "file_path": entry.file_path,
                        "download_token": entry.token,
                        "format": file_format
                    })

                await user_data.websocket.send_json({
                    "type": "directory_update", # 使用新的类型
                    "payload": {
                        "directory": dir_path,
                        "directory_token": dir_token,
                        "files": files_payload
                    }
                })
                logger.info(f"已向用户 '{user}' 发送工作目录更新通知。")
            else:
                logger.warning(f"用户 '{user}' 的WebSocket未连接，无法发送目录更新通知。")

    async def get_downloadable_file_info(self, token_to_find: str) -> Optional[Dict[str, Any]]:
        """根据token查找有效的、未过期的可下载文件信息。"""
        async with self._lock:
            for username, user_data in self._user_sessions.items():
                # 首先在 working_files 中查找
                if user_data.working_files:
                    found_entry = next((entry for entry in user_data.working_files if entry.token == token_to_find), None)
                    if found_entry:
                        filename = os.path.basename(found_entry.file_path)
                        logger.info(f"下载token '{token_to_find}' (文件: {filename}, 用户: {username}) 在 working_files 中验证成功。")
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
                    "working_directory": working_dir_info
                }
            return debug_data
