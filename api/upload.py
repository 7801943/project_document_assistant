import uuid # 新增导入
import asyncio
from typing import Optional, Union, cast, List
from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     status, Response, Query) # 新增 Query 导入
from fastapi import UploadFile
from fastapi.responses import JSONResponse
from loguru import logger
from pathlib import Path

from config import settings
from core import app_state
from core.auth import get_current_verified_user
from core.data_model import ProjectUploadForm, SpecUploadForm, DocType # 新增DocType导入

router = APIRouter()


# --- 新的、分离的上传端点 ---

@router.api_route("/upload-standards", methods=["GET", "POST"], summary="获取表单结构或上传规程文件", response_model=None)
async def handle_standards_upload(
    request: Request,
    user: str = Depends(get_current_verified_user),
    category: Optional[str] = Form(None),
    spec_name: Optional[str] = Form(None),
    overwrite: Optional[bool] = Form(None),
    files: Optional[List[UploadFile]] = File(None)
):
    """
    统一处理规程上传的GET和POST请求。
    - GET: 返回表单的JSON Schema。
    - POST: 处理文件上传。
    """
    if request.method == "GET":
        schema = SpecUploadForm.model_json_schema()
        categories = settings.SPEC_DIRS
        if categories and 'category' in schema.get('properties', {}):
            schema['properties']['category']['enum'] = categories
            if categories:
                schema['properties']['category']['default'] = categories[0]
        return JSONResponse(schema)

    # --- POST请求逻辑 ---
    if not all([category, spec_name, files]):
        raise HTTPException(status_code=400, detail="POST请求必须包含 'category', 'spec_name', 和 'files'。")

    # 类型断言，因为我们知道在POST中它们不是None
    category = cast(str, category)
    spec_name = cast(str, spec_name)
    overwrite = cast(bool, overwrite if overwrite is not None else False)
    files = cast(List[UploadFile], files)

    if not app_state.spec_file_service:
        raise HTTPException(status_code=503, detail="规程文件服务未就绪。")

    target_dir = f"{category}/{spec_name}"
    logger.info(f"用户 '{user}' 开始上传规程到 '{target_dir}' (覆盖: {overwrite})。")

    try:
        if await app_state.spec_file_service.directory_exists_async(target_dir) and not overwrite:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"规程目录 '{target_dir}' 已存在。"
            )

        await app_state.spec_file_service.save_uploaded_directory_async(files, target_dir)

        logger.info(f"成功上传 {len(files)} 个文件到规程目录 '{target_dir}'。")
        return JSONResponse({
            "message": "规程上传成功。",
            "directory": target_dir,
            "file_count": len(files)
        })
    except FileExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"无效的路径: {e}")
    except Exception as e:
        logger.error(f"上传规程到 '{target_dir}' 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")


@router.api_route("/upload-project", methods=["GET", "POST"], summary="获取表单、检查项目存在性或上传项目文件")
async def handle_project_upload(
    request: Request,
    user: str = Depends(get_current_verified_user),
    # GET请求的参数
    year_query: Optional[str] = Query(None, alias="year_query"),
    project_name_query: Optional[str] = Query(None, alias="project_name_query"),
    # POST请求的参数
    year: Optional[str] = Form(None),
    project_name: Optional[str] = Form(None),
    overwrite: bool = Form(False),
    files: Optional[List[UploadFile]] = File(None)
):
    """
    统一处理项目上传的GET和POST请求。
    - GET (无参数): 返回表单的JSON Schema。
    - GET (有参数): 检查项目是否存在。
    - POST: 处理文件上传。
    """
    if not app_state.project_file_service:
        raise HTTPException(status_code=503, detail="项目文件服务未就绪。")

    # --- GET 请求逻辑 ---
    if request.method == "GET":
        # 如果提供了查询参数，则检查项目是否存在
        if year_query and project_name_query:
            base_dir = f"{year_query}/{project_name_query}"
            if await app_state.project_file_service.directory_exists_async(base_dir):
                # 目录存在，返回特定消息，使用 409 Conflict 状态码表示资源冲突
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={"status": "exists", "message": f"项目目录 '{base_dir}' 已存在，如需覆盖请勾选复选框。"}
                )
            # 目录不存在，返回成功消息，表示可以创建
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "not_exists", "message": "项目目录不存在，可以创建。"}
            )
        # 否则，返回表单结构
        else:
            return JSONResponse(ProjectUploadForm.model_json_schema())

    # --- POST 请求逻辑 ---
    if not all([year, project_name, files]):
        raise HTTPException(status_code=400, detail="POST请求必须包含 'year', 'project_name', 和 'files'。")
    
    # 类型断言
    year = cast(str, year)
    project_name = cast(str, project_name)
    files = cast(List[UploadFile], files)

    base_dir = f"{year}/{project_name}"
    for_review_dir = f"{base_dir}/送审"
    final_dir = f"{base_dir}/收口"
    records_dir = f"{base_dir}/过程文件"

    logger.info(f"用户 '{user}' 开始上传项目 '{project_name}' 到年份 '{year}' (覆盖: {overwrite})。")

    try:
        # 检查根项目目录是否存在
        if await app_state.project_file_service.directory_exists_async(base_dir) and not overwrite:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"项目 '{base_dir}' 已存在。请确认是否覆盖。"
            )

        # 1. 保存上传的文件到“送审”目录
        await app_state.project_file_service.save_uploaded_directory_async(files, for_review_dir)
        logger.info(f"成功上传 {len(files)} 个文件到 '{for_review_dir}'。")

        # 2. 创建“收口”和“过程文件”目录的占位文件
        await app_state.project_file_service.create_placeholder_file_async(final_dir)
        await app_state.project_file_service.create_placeholder_file_async(records_dir)
        logger.info(f"已成功创建辅助目录的占位文件: '{final_dir}' 和 '{records_dir}'。")

        return JSONResponse({
            "message": "项目上传成功。",
            "directory": base_dir,
            "file_count": len(files)
        })
    except FileExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"无效的路径: {e}")
    except Exception as e:
        logger.error(f"上传项目文件到 '{base_dir}' 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")


@router.api_route("/upload-files", methods=["GET", "POST"], summary="上传文件到指定目录")
async def handle_files_upload(
    request: Request,
    user: str = Depends(get_current_verified_user),
    # GET 请求参数
    relative_path_query: Optional[str] = Query(None, alias="relative_path"), # 使用 Query 来获取 GET 参数
    # POST 请求参数
    relative_path_form: Optional[str] = Form(None, alias="relative_path"), # 使用 Form 来获取 POST 参数
    overwrite: bool = Form(False),
    files: Optional[List[UploadFile]] = File(None)
):
    '''
    处理文件上传
    - GET: 检查指定 relative_path 目录是否存在。
    - POST: 上传文件到指定 relative_path 目录。
    '''
    if not app_state.project_file_service:
        raise HTTPException(status_code=503, detail="项目文件服务未就绪。")

    # --- GET 请求逻辑 ---
    if request.method == "GET":
        if not relative_path_query:
            raise HTTPException(status_code=400, detail="GET 请求必须包含 'relative_path' 查询参数。")
        
        if await app_state.project_file_service.directory_exists_async(relative_path_query):
            return Response(status_code=status.HTTP_200_OK)
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    # --- POST 请求逻辑 ---
    if request.method == "POST":
        # 内部导入以避免循环引用
        from api.route import ProjectSearchRequest, search_project_files

        if not relative_path_form or not files:
            raise HTTPException(status_code=400, detail="POST 请求必须包含 'relative_path' 和 'files'。")
        
        # 类型断言
        relative_path = cast(str, relative_path_form)
        files = cast(List[UploadFile], files)

        # 验证路径深度
        path_parts = relative_path.split('/')
        if len(path_parts) < 3:
            raise HTTPException(
                status_code=400,
                detail="路径无效：上传目录必须至少在项目文件夹下两层（例如，年/项目名/子目录）。"
            )

        logger.info(f"用户 '{user}' 开始上传文件到 '{relative_path}' (覆盖: {overwrite})。")

        try:
            # 检查目标目录是否存在 (注意：这里的逻辑可能需要调整，因为前端已经检查过了)
            # 如果 overwrite 为 false，但目录存在，这里会报错。这通常是期望的行为。
            if await app_state.project_file_service.directory_exists_async(relative_path) and not overwrite:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"目标目录 '{relative_path}' 已存在。请确认是否覆盖。"
                )
            
            # 保存上传的文件到指定目录
            await app_state.project_file_service.save_uploaded_directory_async(files, relative_path)
            logger.info(f"成功上传 {len(files)} 个文件到 '{relative_path}'。")

            # --- 自动刷新工作目录 ---
            
            try:
                year = path_parts[0]
                project_name = path_parts[1]
                search_request = ProjectSearchRequest(project_name=project_name, project_year=year)
                logger.info(f"文件上传成功，触发工作目录刷新，项目: {year}/{project_name}")


                # await search_project_files(request_body=search_request, user=user)
                async def update_task():
                    # 延迟刷新，要大于2×document_service的debounce更新时间
                    await asyncio.sleep(5)
                    await search_project_files(request_body=search_request, user=user)

                asyncio.create_task(update_task())
            except Exception as e:
                # 如果刷新失败，不应中断整个上传流程，只记录错误
                logger.error(f"上传后刷新工作目录失败: {e}", exc_info=True)


            return JSONResponse({
                "message": "文件上传成功。",
                "directory": relative_path,
                "file_count": len(files)
            })
        except FileExistsError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"无效的路径: {e}")
        except Exception as e:
            logger.error(f"上传文件到 '{relative_path}' 失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
