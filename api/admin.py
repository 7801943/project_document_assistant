# api/admin.py
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from loguru import logger
from dotenv import set_key, get_key
from fastapi import Request

from core.auth import get_current_user
from config import settings, Settings
from core import app_state

router = APIRouter()

# --- 安全性依赖 ---
async def admin_access(request: Request, user: str = Depends(get_current_user)):
    """
    依赖项：检查当前用户是否为 'admin'，并且请求IP是否与登录IP一致。
    如果不满足任一条件，则引发 404 错误，以隐藏此端点的存在。
    """
    if not app_state.session_manager:
        logger.error("SessionManager 未初始化，无法执行 admin 访问检查。")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="服务暂时不可用")

    user_data = await app_state.session_manager.get_user_data(user)
    client_ip = request.client.host if request.client else "unknown"

    if user != "admin" or not user_data or user_data.ip_address != client_ip:
        logger.warning(
            f"Admin access denied for user '{user}' from IP '{client_ip}'. "
            f"Reason: user is not admin or IP mismatch (expected {user_data.ip_address if user_data else 'N/A'})."
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )

    logger.debug(f"Admin access granted for user '{user}' from IP '{client_ip}'.")
    return user

# --- 可配置项定义 ---
# 定义哪些字段可以在运行时被修改，并进行分类
EDITABLE_CONFIGS = {
    "通用设置": [
        "SYSTEM_PROMPT",
        "DEFAULT_YEAR",
        "DEFAULT_STATUS",
    ],
    "文件处理与扫描": [
        "FILE_SCAN_CRON_HOUR",
        "FILE_SCAN_CRON_MINUTE",
        "FILE_WATCHER_COOLDOWN_SECONDS",
        "DOWNLOAD_LINK_VALIDITY_SECONDS",
        "ALLOWED_FILE_TYPES_JSON",
    ],
    "模型与服务": [
        "DIFY_HTTP_TIMEOUT",
        "DIFY_KNOWLEDGEBASE_RETRIEVAL_TOP_K",
        "DIFY_ENABLE_RERANK",
        "MODEL_CONTEXT_WINDOW",
    ],
    "OpenAI 接口": [
        "OPENAI_API_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL_NAME",
    ],
    "本地向量模型": [
        "EMBEDDING_API_URL",
        "EMBEDDING_APIKEY",
        "EMBEDDING_MODEL_NAME",
    ],
    "会话管理": [
        "SESSION_CLEANUP_INTERVAL_SECONDS",
        "SESSION_OVERALL_INACTIVITY_TIMEOUT_SECONDS",
    ],
    "第三方服务": [
        "KKFILEVIEW_HTTP_TIMEOUT",
    ]
}

# 用于更新配置的模型
class ConfigUpdateRequest(BaseModel):
    configs: Dict[str, Any] = Field(..., description="包含要更新的配置项的字典")
    selected_provider: Optional[str] = Field(None, description="用户在前端选择的服务商预设名称")


@router.get("/admin_config", dependencies=[Depends(admin_access)], response_model=Dict[str, Any])
async def get_admin_config():
    """
    获取所有可在线编辑的配置项。
    为了安全，API Key 等敏感信息将返回脱敏后的值。
    同时，提供模型列表供前端选择。
    """
    config_data = {}
    sensitive_keys = ["API_KEY", "APIKEY", "SECRET"]  # 敏感字段的部分关键字

    for category, keys in EDITABLE_CONFIGS.items():
        category_data = {}
        for key in keys:
            value = getattr(settings, key, None)
            # 对敏感信息进行处理
            if any(sub in key.upper() for sub in sensitive_keys):
                # Pydantic SecretStr 的处理
                if hasattr(value, 'get_secret_value') and value is not None:
                    secret_value = value.get_secret_value()
                    if secret_value and len(secret_value) > 4:
                        category_data[key] = f"******{secret_value[-4:]}"
                    elif secret_value:
                        category_data[key] = "******"
                    else:
                        category_data[key] = "" # 如果密钥本身就是空的，返回空字符串
                else:
                    category_data[key] = "" # 如果没有值，也返回空字符串
            else:
                category_data[key] = value
        config_data[category] = category_data

    # --- 添加模型列表 ---
    model_options = []
    if settings.MODELS_DB:
        for provider, details in settings.MODELS_DB.items():
            if "models" in details and isinstance(details["models"], list):
                model_options.extend(details["models"])

    # --- 添加服务商预设 ---
    provider_presets = {}
    if settings.MODELS_DB:
        for provider, details in settings.MODELS_DB.items():
            # 只提取包含 url 或 apikey 的预设
            if "url" in details or "apikey" in details:
                provider_presets[provider] = {
                    "url": details.get("url", ""),
                    "models": details.get("models", []),
                    "has_apikey": bool(details.get("apikey"))
                }

    # 将模型列表和服务商预设添加到返回数据的顶层
    return {
        "configs": config_data,
        "model_options": sorted(list(set(model_options))),
        "provider_presets": provider_presets
    }


@router.post("/admin_config", dependencies=[Depends(admin_access)])
async def update_admin_config(update_request: ConfigUpdateRequest):
    """
    更新一个或多个配置项。
    - 如果用户输入了新的 API Key，则使用新值。
    - 如果用户未输入 API Key 但选择了服务商预设，则从 models_info.json 中获取预设的 Key。
    """
    updated_configs = update_request.configs
    provider = update_request.selected_provider
    all_editable_keys = [key for keys in EDITABLE_CONFIGS.values() for key in keys]

    try:
        # 检查是否需要从预设加载 API Key
        # 条件：用户没有在前端输入新的 OPENAI_API_KEY，但选择了服务商
        if 'OPENAI_API_KEY' not in updated_configs and provider and settings.MODELS_DB:
            provider_info = settings.MODELS_DB.get(provider)
            if provider_info and provider_info.get("apikey"):
                # 将预设的 apikey 添加到待更新列表
                updated_configs['OPENAI_API_KEY'] = provider_info["apikey"]
                logger.info(f"从服务商 '{provider}' 的预设中加载了 API Key。")

        for key, value in updated_configs.items():
            if key not in all_editable_keys:
                logger.warning(f"尝试更新一个不允许在线编辑的配置项: {key}")
                continue

            # 动态更新当前运行的 settings 对象
            if hasattr(settings, key):
                field = Settings.model_fields.get(key)
                # 对 Pydantic 的 SecretStr 特殊处理
                if field and 'SecretStr' in str(field.annotation):
                    from pydantic import SecretStr
                    setattr(settings, key, SecretStr(str(value)))
                    # 记录日志时隐藏真实值
                    log_value = f"******{str(value)[-4:]}" if len(str(value)) > 4 else "******"
                    logger.info(f"运行时配置 '{key}' 已更新为: {log_value}")
                else:
                    setattr(settings, key, value)
                    logger.info(f"运行时配置 '{key}' 已更新为: {value}")

        return {"status": "success", "message": "运行时配置已成功更新。"}

    except Exception as e:
        logger.error(f"更新配置时发生错误: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新配置时发生内部错误: {e}"
        )
