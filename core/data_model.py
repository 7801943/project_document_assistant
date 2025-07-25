
from enum import Enum
from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Dict, Any
from fastapi import Form, File
from starlette.datastructures import UploadFile
from uuid import uuid4, UUID
from datetime import datetime

class DocType(Enum):
    """文档类型枚举"""
    STANDARD = "standard"      # 规范
    MANAGEMENT = "management"  # 管理
    PROJECT = "project"        # 项目

class UploadType(str, Enum):
    """上传类型枚举"""
    FILE = "file"
    DIRECTORY = "directory"

from pathlib import Path

class SpecUploadForm:
    """
    规程上传表单的数据模型.
    使用 Depends 来处理 multipart/form-data.
    """
    def __init__(
        self,
        category: str = Form(...),
        spec_name: str = Form(...),
        upload_type: UploadType = Form(...),
        overwrite: bool = Form(False),
        files: List[UploadFile] = File(...),
        file_paths: List[str] = Form(...)
    ):
        self.category = category
        self.spec_name = spec_name
        self.upload_type = upload_type
        self.overwrite = overwrite
        self.files = files
        self.file_paths = file_paths

class ProjectUploadForm:
    """
    项目文件上传表单的数据模型.
    """
    def __init__(
        self,
        year: str = Form(...),
        project_name: str = Form(...),
        project_type: str = Form(...), # '送审' or '收口'
        upload_type: UploadType = Form(...),
        overwrite: bool = Form(False),
        files: List[UploadFile] = File(...),
        file_paths: List[str] = Form(...)
    ):
        self.year = year
        self.project_name = project_name
        self.project_type = project_type
        self.upload_type = upload_type
        self.overwrite = overwrite
        self.files = files
        self.file_paths = file_paths


# openai completion 接口
class FunctionCall(BaseModel):
    name: str
    arguments: Dict[str, Any]


class ToolCall(BaseModel):
    id: str
    type: Literal["function"]
    function: FunctionCall


class ConversationMessage(BaseModel):
    # --- Conversation-level info ---
    conversation_id: UUID = Field(default_factory=uuid4)

    # --- Message content ---
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    # --- Metadata ---
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    token_count: Optional[int] = None
    annotations: Optional[Dict[str, Any]] = None
