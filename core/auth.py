import time
from typing import Optional
from fastapi import Request, Depends, HTTPException
from loguru import logger

from config import settings
from core import app_state

# --- 认证依赖 (从 main.py 移动过来) ---
async def get_current_user(request: Request) -> Optional[str]:
    '''
    获取用户名，会话id
    '''
    user = request.session.get("user")
    session_id = request.session.get("session_id")
    if not user or not session_id:
        return None
    return user

async def verify_active_session(request: Request) -> str:
    username = request.session.get("user")
    http_session_id = request.session.get("session_id")
    if not username or not http_session_id:
        logger.debug("verify_active_session: HTTP session中缺少用户名或session_id。")
        raise HTTPException(status_code=401, detail="用户未登录或会话标识无效")
    if not app_state.session_manager:
        logger.error("verify_active_session: SessionManager 未初始化。")
        raise HTTPException(status_code=500, detail="服务器内部错误 (SessionManager missing)")
    user_data = await app_state.session_manager.get_user_data(username)
    if not user_data:
        logger.warning(f"verify_active_session: 用户 '{username}'"
                       "在SessionStateManager中未找到。可能是会话已过期被清理。")
        request.session.clear()
        raise HTTPException(status_code=401, detail="会话已过期或无效，请重新登录 (user not in SM)")
    if user_data.session_id != http_session_id:
        logger.warning(f"verify_active_session: 用户 '{username}' 的HTTP session_id ({http_session_id})"
                       "与SessionStateManager中的 ({user_data.session_id}) 不匹配。")
        request.session.clear()
        raise HTTPException(status_code=401, detail="会话冲突或已失效，请重新登录 (session_id mismatch)")
    current_time = time.time()
    if (current_time - user_data.last_activity_time) >= settings.SESSION_OVERALL_INACTIVITY_TIMEOUT_SECONDS:
        logger.info(f"verify_active_session: 用户 '{username}' (会话 {user_data.session_id})"
                    "因HTTP不活动超时 (最后活动: {user_data.last_activity_time_str})。")
        await app_state.session_manager.logout_user(username)
        request.session.clear()
        raise HTTPException(status_code=401, detail="会话因长时间不活动已过期，请重新登录")
    await app_state.session_manager.set_http_activity(username)
    logger.trace(f"verify_active_session: 用户 '{username}' 会话有效，活动时间已更新。")
    return username

async def get_current_verified_user(user: str = Depends(verify_active_session)) -> str:
    return user