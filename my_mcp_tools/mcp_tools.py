# my_mcp_tools/mcp_tools.py

import json
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any, Union
from pydantic import BaseModel
import os
import sqlite3
import difflib
import uuid
import requests
import numpy as np
import openai
from sklearn.metrics.pairwise import cosine_similarity
from fastmcp import FastMCP
# from fastmcp import Context # Context 未在工具函数签名中使用

from loguru import logger # Loguru 已在主程序配置，此处直接使用
from core.data_model import DocType
from database.specbase import query_specs_by_category # 导入新的数据库查询函数
from utils import file_parser # 假设 file_parser.py 可被正确导入
from utils.utils import get_host_ipv6_addr# 导入自定义工具函数
from config import settings # 导入配置

# --- 标准化返回模型 ---
class ToolBaseResponse(BaseModel):
    """所有工具返回值的基类"""
    content: Any
    token: Optional[str] = None
    hint: str

class ReadFileResponse(ToolBaseResponse):
    """read_project_file 工具的返回值模型"""
    file_path: Optional[str] = None
    download_url: Optional[str] = None

class SpecFile(BaseModel):
    """规程规范文件的数据模型"""
    path: str
    similarity: Optional[float] = None

class OpenSpecFilesResponse(ReadFileResponse):
    """open_specification_files 工具的返回值模型"""
    files: Optional[List[SpecFile]] = None
    similarity: Optional[float] = None # 单文件读取时也返回相似度

class DiffFileResponse(BaseModel):
    """所有工具返回值的基类"""
    content: Any
    token1: Optional[str] = None
    token2: Optional[str] = None
    file_path1: Optional[str] = None
    download_url1: Optional[str] = None
    file_path2: Optional[str] = None
    download_url2: Optional[str] = None
    hint: str

# --- FastMCP 服务器实例化 ---
# MCP_MOUNT_PATH 在主应用中定义和使用，此处不需要
project_mcp = FastMCP(
    name="项目文件检索服务器",
    instructions="一个通过 Streamable HTTP 访问的MCP服务器，提供工具来列出项目和项目文件，供LLM客户端查询和选择。"
)
logger.info(f"FastMCP 服务器 '{project_mcp.name}' 已在 my_mcp_tools/mcp_tools.py 中实例化。")

def _get_file_content(relative_file_path_str: str, delimiter: str, type:str = "projects") -> str:
    root = Path()
    if type == "projects":
        root = settings.PROJECTS_ROOT_DIR
    elif type == "specification":
        root = settings.SPEC_ROOT_DIR
    else:
        msg = f"错误：类型参数:{type} 无效。"
        logger.error(msg)
        return msg
    # 这个函数依赖 settings.PROJECTS_ROOT_DIR 和 file_parser
    abs_file_path = root / relative_file_path_str
    logger.debug(f"MCP Tool: 尝试解析文件: {abs_file_path} (相对路径: {relative_file_path_str})，分隔符: '{delimiter}'")
    content = file_parser.parse_file(str(abs_file_path), delimiter)
    if content is None or content.startswith("错误:"):
        logger.error(f"MCP Tool: 解析文件 {abs_file_path} (相对路径: {relative_file_path_str}) 失败或返回错误: {content}")
        return content if isinstance(content, str) else f"错误: 解析文件 {abs_file_path.name} 失败。"
    logger.debug(f"MCP Tool: 解析完成，文件: {abs_file_path.name}, 内容长度: {len(content)}")
    return content

def _get_spec_file_content(relative_file_path_str: str) -> str:
    """
    读取单个规程规范文件的内容。
    """
    abs_file_path = settings.SPEC_ROOT_DIR / relative_file_path_str
    logger.debug(f"MCP Tool: 尝试读取规程文件: {abs_file_path}")

    try:
        content = abs_file_path.read_text(encoding='utf-8')
        logger.debug(f"MCP Tool: 读取完成，文件: {abs_file_path.name}, 内容长度: {len(content)}")
        return content
    except FileNotFoundError:
        logger.error(f"MCP Tool: 规程文件未找到: {abs_file_path}")
        return f"错误: 文件未找到 {relative_file_path_str}"
    except Exception as e:
        logger.error(f"MCP Tool: 读取规程文件 {abs_file_path} 失败: {e}")
        return f"错误: 读取文件 {relative_file_path_str} 时发生错误。"

def _connect_db(db_path: Path) -> sqlite3.Connection:
    # 这个函数不依赖外部自定义模块，除了 sqlite3 和 Path
    try:
        conn = sqlite3.connect(db_path)
        return conn
    except sqlite3.Error as e:
        logger.error(f"MCP Tool: 连接数据库 {db_path} 失败: {e}")
        raise

def _get_available_project_names_nested(
        current_cursor: sqlite3.Cursor,
        current_year: Optional[str],
        current_status_condition: str = f"(status = '{settings.DEFAULT_STATUS}' OR status = '收口')") -> List[str]:
    '''
    获取所有项目
    '''
    sql_query_parts_helper = [current_status_condition]
    sql_params_helper = []
    if current_year:
        sql_query_parts_helper.append("year = ?")
        sql_params_helper.append(current_year)
    effective_where_clause_helper = " AND ".join(sql_query_parts_helper)
    query_helper = f"SELECT DISTINCT project_name FROM indexed_files WHERE {effective_where_clause_helper} ORDER BY project_name"
    current_cursor.execute(query_helper, sql_params_helper)
    return [str(row[0]) for row in current_cursor.fetchall() if row[0] is not None]

def _get_embeddings(texts: List[str]) -> Optional[np.ndarray]:
    """获取文本向量"""
    if not settings.EMBEDDING_AVAILABLE:
        logger.warning("嵌入模型服务不可用，无法获取向量。")
        return None
    try:
        logger.debug(f"正在调用嵌入模型: base_url='{str(settings.EMBEDDING_API_URL)}', model='{settings.EMBEDDING_MODEL_NAME}'")
        client = openai.OpenAI(
            api_key=settings.EMBEDDING_APIKEY.get_secret_value(),
            base_url=str(settings.EMBEDDING_API_URL)
        )
        response = client.embeddings.create(model=settings.EMBEDDING_MODEL_NAME, input=texts)
        return np.array([item.embedding for item in response.data])
    except Exception as e:
        logger.error(f"调用嵌入模型失败: {e}", exc_info=True)
        return None

def _find_similar_items_with_scores(query_text: str, candidate_items: List[str], top_k: int) -> List[Tuple[str, float]]:
    """通用的相似度查找函数，返回项目和分数"""
    if not candidate_items:
        return []

    all_texts = candidate_items + [query_text]
    embeddings = _get_embeddings(all_texts)

    if embeddings is None:
        logger.error("获取向量失败，无法进行相似度计算。")
        return []

    candidate_embeddings = embeddings[:-1]
    query_embedding = embeddings[-1:].reshape(1, -1)

    similarities = cosine_similarity(query_embedding, candidate_embeddings)[0]
    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = [(candidate_items[i], float(similarities[i])) for i in top_indices]
    logger.debug(f"向量检索 Top-{top_k} 结果: {results}")
    return results

# path_or_dir   token, filepath 格式
async def _update_session_manager(user_name: str, files_path: List[Tuple[str,Path]], dir: str = "", doc_type: DocType = DocType.PROJECT):
    '''
    type dir或file,调用线程管理器更新状态， type=file 更新文件， tpye=dir 更新目录
    '''
    from core import app_state

    if app_state.session_manager:
        if dir == "":
            # 场景：只打开单个或少量文件，而不是整个目录
            for token, file_path in files_path:
                await app_state.session_manager.update_opened_file(user_name, token, file_path, True, doc_type)
        else:
            # 场景：打开整个工作目录，覆盖更新
            # 为目录本身生成一个token
            dir_token = uuid.uuid4().hex
            # 转换 files_path 的格式以匹配新的 update_opened_dir 签名
            # 新签名需要 List[Tuple[str, str]]，格式为 (file_path_str, file_token)
            # files_path 的格式是 List[Tuple[str, Path]]，格式为 (file_token, file_path_obj)
            files_with_token_for_new_func = [(str(path_obj), token) for token, path_obj in files_path]

            await app_state.session_manager.update_opened_dir(
                user=user_name,
                dir_path=dir,
                dir_token=dir_token,
                files_with_token=files_with_token_for_new_func
            )
    else:
        logger.error("Session manager is not initialized.")



@project_mcp.tool()
def query_specification_knowledge_base(user_query:str, knowledge_base_name: str, top_k:int = settings.DIFY_KNOWLEDGEBASE_RETRIEVAL_TOP_K) -> str:
    """
    规程规范知识库检索工具，使用向量检索规程规范
    参数：
        user_query:  用户需要查询的内容
        name: 知识库名称，（电气、二次、 通信、 线路 四个必选之一）
    返回：
        一个标准ToolBaseResponse模型的JSON字符串，包含检索结果。
    """
    logger.debug(f"工具调用: query_specification_knowledge_base, 知识库: {knowledge_base_name}, 用户查询: '{user_query[:50]}...', top_k: {top_k}")

    tool_response: ToolBaseResponse

    header = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.DIFY_KNOWLEDGEBASE_APIKEY.get_secret_value()}"
    }

    try:
        # 1. 获取知识库ID
        url_get_id = f"{settings.DIFY_KNOWLEDGEBASE_URL}/datasets"
        param_get_id = {"keyword": knowledge_base_name, "page": 1, "limit": 10}
        logger.debug(f"正在从 {url_get_id} 获取知识库ID，参数: {param_get_id}")
        response_get_id = requests.get(url_get_id, headers=header, params=param_get_id, timeout=10)
        response_get_id.raise_for_status()
        data_get_id = response_get_id.json()

        if not (data_get_id and data_get_id.get('data')):
            logger.warning(f"未找到名为 '{knowledge_base_name}' 的知识库。响应: {data_get_id}")
            tool_response = ToolBaseResponse(
                content=f"错误: 未找到名为 '{knowledge_base_name}' 的知识库。",
                hint="请检查知识库名称是否正确，可选值为：电气、二次、通信、线路。"
            )
            return tool_response.model_dump_json()

        knowledge_base_id = data_get_id['data'][0].get('id')
        logger.info(f"成功获取到知识库 '{knowledge_base_name}' 的ID: {knowledge_base_id}")

        # 2. 检索知识库
        url_retrieve = f"{settings.DIFY_KNOWLEDGEBASE_URL}/datasets/{knowledge_base_id}/retrieve"
        payload = {
            "query": user_query,
            "retrieval_model": {
                "search_method": "semantic_search",
                "reranking_enable": settings.DIFY_ENABLE_RERANK,
                "reranking_model": { "reranking_model_name": settings.DIFY_RERANK_MODEL },
                "top_k": top_k,
                "score_threshold_enabled": False
            }
        }
        logger.debug(f"正在向 {url_retrieve} 发起检索请求。Payload (部分): query='{user_query[:50]}...', top_k={top_k}")
        response_retrieve = requests.post(url_retrieve, headers=header, json=payload, timeout=20)
        response_retrieve.raise_for_status()
        data_retrieve = response_retrieve.json()
        retrieval_content = data_retrieve.get('records', [])

        if retrieval_content:
            logger.info(f"从知识库 '{knowledge_base_name}' 检索到 {len(retrieval_content)} 条结果。")
            result_text = ""
            for index, item in enumerate(retrieval_content):
                doc_name = item.get('segment', {}).get('document', {}).get('name', '未知文档')
                score = item.get('score', 'N/A')
                content = item.get('segment', {}).get('content', '无内容')
                result_text += f"\n检索结果 {index + 1}\n来自源文档：{doc_name}\n相似度分数：{score}\n内容如下：\n{content}\n\n"

            tool_response = ToolBaseResponse(
                content=result_text,
                token=uuid.uuid4().hex,
                hint=f"已成功从知识库 '{knowledge_base_name}' 检索到 {len(retrieval_content)} 条内容。"
            )
        else:
            logger.info(f"知识库 '{knowledge_base_name}' 对于查询 '{user_query[:50]}...' 未返回任何结果。")
            tool_response = ToolBaseResponse(
                content=f"知识库 '{knowledge_base_name}' 未检索到与 '{user_query}' 相关的内容。",
                hint="可以尝试更换查询关键词或检查知识库内容。"
            )

    except requests.exceptions.RequestException as e:
        logger.error(f"请求Dify API时发生网络错误: {e}", exc_info=True)
        tool_response = ToolBaseResponse(
            content=f"错误: 访问知识库服务时发生网络错误: {e}",
            hint="请检查网络连接或稍后再试。"
        )
    except Exception as e:
        logger.error(f"查询知识库时发生未知错误: {e}", exc_info=True)
        tool_response = ToolBaseResponse(
            content=f"错误: 查询知识库时发生未知错误: {e}",
            hint="请联系管理员检查服务器日志。"
        )

    return tool_response.model_dump_json()

@project_mcp.tool()
async def diff_project_file(user:str, relative_file1_path: str,relative_file2_path: str, document_type: str, sheet_name: Optional[str] = None, all_sheet: bool = False) -> str:
    """
    使用diff函数比较两个项目文件的差异，返回差异结果。
    参数：
        user: 发起调用的用户名
        relative_file1_path: 需要比较的文件1（比如XXX送审版）的相对路径。
        relative_file2_path: 需要比较的文件2（比如XXX收口版）的相对路径。
        document_type:  文档类型，可选值为 "报告（说明书）", "材料清册", "概算表"。
        sheet_name: 表名（仅当file_type为"概算表"且文件为xlsx时需要，其他情况可忽略）。如果 all_sheet 为 True，此参数将被忽略。
        all_sheet:  布尔值，默认为 False。如果为 True，则对"概算表"类型的文档，比较其所有同名sheet，并在输出中包含各表名描述。
    返回：
        返回文件差异结果字符串，包含两个文件的文件名作为标题。如果文件不存在、类型不支持或比较出错，则返回错误内容和下一步操作建议。
    """
    #from core import app_state
    tool_response: DiffFileResponse

    # xlsx 有部分需要转化为绝对路径，以后再fix
    file_path1 = settings.PROJECTS_ROOT_DIR / relative_file1_path
    file_path2 = settings.PROJECTS_ROOT_DIR / relative_file2_path

    logger.info(f"工具调用: compare_project_file. 用户:{user}, 文件1: '{relative_file1_path}', 文件2: '{relative_file2_path}', 文件类型: '{document_type}', Sheet名: '{sheet_name}', All Sheets: {all_sheet}")
    if not document_type in ["报告（说明书）", "材料清册", "概算表"]:
        msg = f"错误: 不支持的文件类型 '{document_type}'。支持的类型有 '报告（说明书）', '材料清册', '概算表'。"
        logger.error(msg)
        tool_response = DiffFileResponse(
            content="N/A",
            hint=msg
        )
        return tool_response.model_dump_json()
    elif not os.path.exists(file_path1) or not os.path.exists(file_path2):
        msg = f"错误: 文件未找到: {relative_file1_path}:{os.path.exists(file_path1)}, {relative_file2_path}:{os.path.exists(file_path2)}"
        logger.error(msg)
        tool_response = DiffFileResponse(
            content="N/A",
            hint=msg
        )
        return tool_response.model_dump_json()
    elif not file_path1.suffix.lower() in {'.xlsx', '.pdf', '.docx'} or not file_path2.suffix.lower() in {'.xlsx', '.pdf', '.docx'}:
        msg = f"错误: {relative_file1_path}与{relative_file2_path} 文件无效，有效的文件名为:'.xlsx', '.pdf', '.docx'"
        logger.error(msg)
        tool_response = DiffFileResponse(
            content="N/A",
            hint= msg
        )
        return tool_response.model_dump_json()
    # 两个文件扩展名不一致不考虑比较。
    elif not Path(file_path1).suffix.lower() == Path(file_path2).suffix.lower():
        msg = f"错误: {relative_file1_path}与{relative_file2_path} 扩展名不一致，需使用扩展名一致的文件进行比较。"
        logger.error(msg)
        tool_response = DiffFileResponse(
            content="N/A",
            hint= msg
        )
        return tool_response.model_dump_json()
    else:
        # 文件检查完成
        pass

    result_header = f"比较文件:\n  1. {relative_file1_path}\n  2. {relative_file2_path}\n"
    success = False
    result = ""
    success_hint = ""
    try:
        if document_type == "概算表":
            if not file_path1.suffix == '.xlsx' or not file_path2.suffix == '.xlsx':
                msg = "错误: 文件概算表比较仅支持xlsx格式，请检查文件格式"
                logger.error(msg)
                tool_response = DiffFileResponse(
                    content="N/A",
                    hint=msg
                )
                return tool_response.model_dump_json()
            # 比较所有sheet
            # success = True
            if all_sheet:
                logger.info(f"对文件 '{relative_file1_path}' 和 '{relative_file2_path}' (类型: {document_type}) 进行所有Sheet的比较。")
                sheet_names1_list = file_parser.get_xlsx_sheet_names(str(file_path1))
                if not sheet_names1_list and os.path.exists(file_path1):
                    logger.warning(f"无法从文件1 '{os.path.basename(file_path1)}' 读取工作表列表，或文件不包含工作表。")
                sheet_names2_list = file_parser.get_xlsx_sheet_names(str(file_path2))
                if not sheet_names2_list and os.path.exists(file_path2):
                    logger.warning(f"无法从文件2 '{os.path.basename(file_path2)}' 读取工作表列表，或文件不包含工作表。")
                sheet_names1 = set(sheet_names1_list if sheet_names1_list else []) # 防御None
                sheet_names2 = set(sheet_names2_list if sheet_names2_list else []) # 防御None
                common_sheets = sorted(list(sheet_names1.intersection(sheet_names2)))
                sheets_only_in_file1 = sorted(list(sheet_names1 - sheet_names2))
                sheets_only_in_file2 = sorted(list(sheet_names2 - sheet_names1))
                comparison_results = [result_header]
                # 文件内容错误，不包含任何sheet页
                if not common_sheets and not sheets_only_in_file1 and not sheets_only_in_file2:
                     comparison_results.append("两个Excel文件均不包含任何sheet页，或无法读取sheet列表。\n")
                     tool_response = DiffFileResponse(content=f"N/A",hint="".join(comparison_results))
                     return tool_response.model_dump_json()
                     #return "".join(comparison_results)
                # 存在同名的sheets
                if common_sheets:
                    comparison_results.append("--- 共同存在的Sheet比较结果 ---\n")
                    for s_name in common_sheets:
                        current_sheet_header = f"Sheet名称: {s_name}\n" + "-" * 30 + "\n"
                        col_conf = settings.SHEET_COLUMN_CONFIG.get(s_name) if document_type == "概算表" else None
                        try:
                            content1_lines = file_parser.parse_xlsx_sheet_content(file_path1, s_name, col_conf)
                            content2_lines = file_parser.parse_xlsx_sheet_content(file_path2, s_name, col_conf)
                            if not content1_lines and not content2_lines:
                                comparison_results.append(f"{current_sheet_header}Sheet '{s_name}': 无法解析文件1和文件2的此sheet内容，或内容均为空。\n\n"); continue
                            elif not content1_lines:
                                comparison_results.append(f"{current_sheet_header}Sheet '{s_name}': 无法解析文件1的此sheet内容，或内容为空。\n\n")
                                continue
                            elif not content2_lines:
                                comparison_results.append(f"{current_sheet_header}Sheet '{s_name}': 无法解析文件2的此sheet内容，或内容为空。\n\n")
                                continue
                            diff = difflib.unified_diff(content1_lines, content2_lines, fromfile=f"{os.path.basename(file_path1)} ({s_name})", tofile=f"{os.path.basename(file_path2)} ({s_name})", lineterm='')
                            diff_output = list(diff)
                            if not diff_output:
                                comparison_results.append(f"{current_sheet_header}Sheet '{s_name}': 内容一致。\n\n")
                            else:
                                filtered_diff = [line for line in diff_output if not (line.startswith("--- ") or line.startswith("+++ "))]
                                comparison_results.append(f"{current_sheet_header}Sheet '{s_name}': 差异内容如下:\n" + "\n".join(filtered_diff) + "\n\n")
                        except ValueError as ve:
                            comparison_results.append(f"{current_sheet_header}Sheet '{s_name}': 比较错误 - {ve}\n\n")
                        except Exception as e_comp:
                            comparison_results.append(f"{current_sheet_header}Sheet '{s_name}': 比较时发生未知错误 - {e_comp}\n\n")
                if sheets_only_in_file1:
                    comparison_results.append(f"--- 仅存在于文件 '{os.path.basename(file_path1)}' 的Sheet ---\n")
                    for s_name in sheets_only_in_file1:
                        comparison_results.append(f"- {s_name}\n")
                    comparison_results.append("\n")
                if sheets_only_in_file2:
                    comparison_results.append(f"--- 仅存在于文件 '{os.path.basename(file_path2)}' 的Sheet ---\n")
                    for s_name in sheets_only_in_file2:
                        comparison_results.append(f"- {s_name}\n")
                    comparison_results.append("\n")
                # 成功，构建返回内容
                result = "".join(comparison_results)
                success = True
                success_hint = "请整理差异内容后输出，不要遗漏"
            else:
                # 指定表名
                if not sheet_name:
                    msg = f"错误: 文件类型 '{document_type}' (Excel) 且 all_sheet=False 时，需要提供 sheet_name 进行比较，请检查调用参数。"
                    logger.warning(msg)
                    tool_response = DiffFileResponse(
                        content="N/A",
                        hint=msg
                    )
                    return tool_response.model_dump_json()

                logger.info(f"对文件 '{relative_file1_path}' 和 '{relative_file2_path}' (类型: {document_type}) 比较{sheet_name}。")
                current_sheet_header = result_header + f"Sheet名称: {sheet_name}\n" + "-" * 30 + "\n"
                col_conf = settings.SHEET_COLUMN_CONFIG.get(sheet_name) if document_type == "概算表" else None
                content1_lines = file_parser.parse_xlsx_sheet_content(file_path1, sheet_name, col_conf)
                content2_lines = file_parser.parse_xlsx_sheet_content(file_path2, sheet_name, col_conf)

                # 任意一个文件读取结果为空
                if not content1_lines or not content2_lines:
                    msg = f"{current_sheet_header}错误: 无法解析{relative_file1_path if not content1_lines else relative_file2_path}的Sheet '{sheet_name}' 内容，或内容均为空。"
                    tool_response = DiffFileResponse(
                        content="N/A",
                        hint=msg
                    )
                    return tool_response.model_dump_json()
                    # returnmsg
                logger.debug(f"已从Excel文件 '{relative_file1_path}' 和 '{relative_file2_path}' 的Sheet '{sheet_name}' (列配置: {col_conf}) 读取内容进行比较。")
                diff = difflib.unified_diff(content1_lines, content2_lines, fromfile=os.path.basename(file_path1), tofile=os.path.basename(file_path2), lineterm='')
                diff_output = list(diff)
                if not diff_output:
                    # 成功，提示无差异
                    success = True
                    msg = f"'{relative_file1_path}' 和 '{relative_file2_path}' 的Sheet '{sheet_name}'完全一致。"
                    logger.info(msg)
                    result = current_sheet_header + "无差异"
                    success_hint = msg

                else:
                    # 成功，返回差异内容
                    msg = f"'{relative_file1_path}' 和 '{relative_file2_path}' 的Sheet '{sheet_name}' 存在差异。"
                    logger.info(msg)
                    filtered_diff = [line for line in diff_output if not (line.startswith("--- ") or line.startswith("+++ "))]
                    filtered_diff.insert(0, current_sheet_header)
                    success = True
                    result = "\n".join(filtered_diff)
                    success_hint = "请整理差异内容后输出，不要遗漏"
        # 比较报告和清册
        elif document_type == "报告（说明书）" or document_type == "材料清册":
            current_file_header = result_header + "-" * 30 + "\n"

            raw_content1 = _get_file_content(relative_file1_path, delimiter="")
            content1_lines = raw_content1.splitlines()
            raw_content2 = _get_file_content(relative_file2_path, delimiter="")
            content2_lines = raw_content2.splitlines()

            if raw_content1.startswith("错误:") or raw_content2.startswith("错误:"):
                # logger.error(f"解析文件1 ({relative_file1_path}) 失败: {raw_content1}")
                # 截取前50个字符，防止日志输出过多
                msg = f"错误:解析文件1 ({relative_file1_path}) 内容: {raw_content1[:50]}--解析文件2 ({relative_file2_path}) 内容: {raw_content2[:50]}"
                logger.error(msg)
                tool_response = DiffFileResponse(
                    content="N/A",
                    hint= msg + "请检查日志"
                )
                return tool_response.model_dump_json()

            # logger.debug(f"已将文件 '{file_path1}' (相对: {relative_file1_path}) 和 '{file_path2}' (相对: {relative_file2_path}) 作为文本文件读取内容进行比较。")
            diff = difflib.unified_diff(content1_lines, content2_lines, fromfile=os.path.basename(file_path1), tofile=os.path.basename(file_path2), lineterm='')
            diff_output = list(diff)
            if not diff_output:
                msg = f"文本文件 '{relative_file1_path}' 和 '{relative_file2_path}' 内容一致。"
                logger.info(msg)
                success = True
                result = current_file_header + "无差异"
                success_hint = msg
            else:
                msg = (f"文本文件 '{relative_file1_path}' 和 '{relative_file2_path}' 存在差异。")
                filtered_diff = [line for line in diff_output if not (line.startswith("--- ") or line.startswith("+++ "))]
                filtered_diff.insert(0,result_header)
                logger.info(msg)
                success = True
                result = "\n".join(filtered_diff)
                success_hint = "请整理差异内容后输出，不要遗漏"
        else:
            msg = f"内部错误: 无法处理的文档类型 '{document_type}，检查调用参数。"
            logger.error(msg)
            tool_response = DiffFileResponse(
                content="N/A",
                hint= msg
            )
            return tool_response.model_dump_json()
        # 让成功的比较 在此处返回
        if success:
            if len(result) > settings.MODEL_CONTEXT_WINDOW:
                logger.warning(f"比较结果长度{len(result)}，超过可能的模型上下文窗口{settings.MODEL_CONTEXT_WINDOW}")
            token1 = uuid.uuid4().hex
            token2 = uuid.uuid4().hex
            tool_response = DiffFileResponse(
                content=result,
                token1 = token1,
                token2 = token2,
                file_path1 = relative_file1_path,
                file_path2 = relative_file2_path,
                download_url1 = f"http://[{get_host_ipv6_addr()}]:{settings.SERVER_PORT}/download/{token1}/{relative_file1_path}",
                download_url2 = f"http://[{get_host_ipv6_addr()}]:{settings.SERVER_PORT}/download/{token2}/{relative_file2_path}",
                hint = success_hint
            )
            # 通知会话管理器更新状态
            await _update_session_manager(user, [(token1,file_path1),(token2,file_path2)])
            return tool_response.model_dump_json()
        else:

            # 理论上不会到这里
            tool_response = DiffFileResponse(
                content="N/A",
                hint= "未知错误"
            )
            return tool_response.model_dump_json()

    except ValueError as ve:
        msg = f"异常：比较文件时发生值错误: {ve}，检查日志。"
        logger.error(msg)
        header_for_error = result_header
        if sheet_name and not all_sheet:
            header_for_error += f"Sheet名称: {sheet_name}\n"
        header_for_error += "-" * 30 + "\n"
        tool_response = DiffFileResponse(
            content="N/A",
            hint= header_for_error + msg
        )
        return tool_response.model_dump_json()
    except Exception as e:
        msg = f"异常：比较文件时发生未知错误: {e}，检查日志。"
        logger.error(msg)
        header_for_error = result_header
        if sheet_name and not all_sheet:
            header_for_error += f"Sheet名称: {sheet_name}\n"
        header_for_error += "-" * 30 + "\n"
        tool_response = DiffFileResponse(
            content="N/A",
            hint= header_for_error + msg
        )
        return tool_response.model_dump_json()

@project_mcp.tool()
async def read_project_file(user:str, relative_file_path: str, file_category: str, sheet_name: str = "") -> str:
    """
    读取项目文件的文件内容并返回。
    参数：
        user_name : 发起对话的用户名，必填
        relative_file_path: 需要解析的文件的相对路径，通常由 query_project_file_path函数返回，必填
        file_category: "普通文档" "图纸图形文档" "概算书文档" 必填之一
        sheet_name: 如果为file_category="概算书文档" ，必填。
    返回：
        一个标准ReadFileResponse模型的JSON字符串。
    """
    # from core import app_state
    logger.info(f"工具调用 (LLM): read_project_file. User: '{user}', Path: '{relative_file_path}', Category: '{file_category}'")

    abs_file_path = settings.PROJECTS_ROOT_DIR / relative_file_path
    if not abs_file_path.exists():
        logger.error(f"请求的文件路径不存在: {abs_file_path}")
        return ReadFileResponse(content=f"错误: 文件路径 {relative_file_path} 不存在。", hint="请检查文件路径是否正确。").model_dump_json()

    response = ReadFileResponse(content="", hint="") # Default response
    success = False

    # --- 概算书文档 (Excel) ---
    if file_category == "概算书文档":
        sheet_names_list = file_parser.get_xlsx_sheet_names(str(abs_file_path))
        if not sheet_name:
            if not sheet_names_list:
                logger.warning(f"文件 '{relative_file_path}' 不包含任何工作表，或无法读取。")
                response.content = f"读取文件 {relative_file_path} 失败: 文件不包含任何工作表，或无法读取。"
                response.hint = "请检查文件是否为有效的Excel文件。"
            else:
                all_sheet_names = "\n".join(sheet_names_list)
                logger.info(f"未指定表名， {relative_file_path} 中的Sheet名称: {sheet_names_list}，等待重试")
                response.content = f"未指定表名，文件 {relative_file_path} 的sheets如下：\n{all_sheet_names}"
                response.hint = "请指定表名重试。"
        else:
            if not sheet_names_list or sheet_name not in sheet_names_list:
                available_sheets_str = "\n".join(sheet_names_list) if sheet_names_list else "无可用Sheet"
                logger.warning(f"Sheet '{sheet_name}' 在文件 {relative_file_path} 中未找到。可用Sheets: {available_sheets_str}")
                response.content = f"文件 {relative_file_path} 的sheet'{sheet_name}'未找到， 可用Sheets: {available_sheets_str}。"
                response.hint = "请检查sheet_name或从可用列表中选择一个重试。"
            else:
                content_lines = file_parser.parse_xlsx_sheet_content(str(abs_file_path), sheet_name, column_config=None)
                if not content_lines:
                    logger.warning(f"无法从文件 '{relative_file_path}' 的 Sheet '{sheet_name}' 解析内容，或该Sheet为空。")
                    response.content = f"无法从文件 '{relative_file_path}' 的 Sheet '{sheet_name}' 解析内容，或该Sheet为空。"
                    response.hint = "请检查文件内容和格式。"
                else:
                    sheet_content = "\n".join(content_lines)
                    preview_len = min(100, len(sheet_content))
                    logger.info(f"从文件 {relative_file_path}成功读取 sheet:{sheet_name}（预览100字）:{sheet_content[0:preview_len]}")
                    response.content = sheet_content
                    response.hint = "已成功读取Sheet内容。内容较多，无需罗列。"
                    success = True

    # --- 图纸图形文档 ---
    elif file_category == "图纸图形文档":
        logger.debug(f"{relative_file_path} 为llm暂不支持的文件类型。")
        response.content = "本文件为图纸图形文档，暂不支持你读取。"
        response.hint = "已为该文件生成下载链接，请提示用户下载查看。"
        success = True

    # --- 普通文档 ---
    else:
        file_content_data = _get_file_content(relative_file_path, delimiter="\t")
        if file_content_data.startswith("错误:"):
            logger.error(f"读取文件 {relative_file_path} 失败: {file_content_data}")
            response.content = f"读取文件 {relative_file_path} 失败: {file_content_data}"
            response.hint = "请检查文件是否存在或格式是否正确。"
        else:
            preview_len = min(100, (len(file_content_data)))
            logger.info(f"从文件 {relative_file_path}成功读取（预览100字）:{file_content_data[0:preview_len]}")
            response.content = file_content_data
            response.hint = "已成功读取文件内容。内容较多，无需罗列。"
            success = True

    if success:
        response.token = uuid.uuid4().hex
        response.download_url = f"http://[{get_host_ipv6_addr()}]:{settings.SERVER_PORT}/download/{response.token}/{abs_file_path.name}"
        response.file_path = relative_file_path
        logger.debug(f"为文件:{relative_file_path} 生成LLM工具下载token {response.token}")
        # 通知线程管理器更新
        await _update_session_manager(user,[(response.token,Path(relative_file_path))])
    return response.model_dump_json()

# 纯数据库查询模式
# @project_mcp.tool()
# def query_project_files(year: Optional[str] = None, project_name: Optional[str] = None) -> str:
#     """
#     根据project_name查询、检索数据库，获得唯一的项目名称(项目目录)及所属项目文件路径。
#     参数:
#         year: 项目的四位数字年份 (可选, 如'2024', 精确匹配)。如果未提供，则不按年份筛选。
#         project_name: 项目名称的关键字 (可选, 如'abc输变电工程'中的'abc'，模糊匹配)。如果未提供，将返回所有可选项目名称列表以供选择。
#                       如果提供，将尝试模糊匹配。
#     返回:
#         操作结果，token，项目文件路径，项目名称，提示
#     """
#     logger.info(f"工具调用: query_project_files, 年份='{year}', 项目关键字='{project_name}'")
#     response_data = {'result': "", 'token': "", 'project_file_or_name_list': "", 'project_name': "", "hint": ""}
#     try:
#         # connect_db 和 settings.DATABASE_PATH 已在本文件/模块中可用
#         with _connect_db(settings.DATABASE_PATH) as conn:
#             cursor = conn.cursor()
#             status_condition = f"(status = '{settings.DEFAULT_STATUS}' OR status = '收口')"
#             def _get_available_project_names_nested(current_cursor: sqlite3.Cursor, current_year: Optional[str], current_status_condition: str) -> List[str]:
#                 sql_query_parts_helper = [current_status_condition]
#                 sql_params_helper = []
#                 if current_year:
#                     sql_query_parts_helper.append("year = ?")
#                     sql_params_helper.append(current_year)
#                 effective_where_clause_helper = " AND ".join(sql_query_parts_helper)
#                 query_helper = f"SELECT DISTINCT project_name FROM indexed_files WHERE {effective_where_clause_helper} ORDER BY project_name"
#                 current_cursor.execute(query_helper, sql_params_helper)
#                 return [str(row[0]) for row in current_cursor.fetchall() if row[0] is not None]

#             if not project_name:
#                 response_data['result'] = "重试"
#                 all_project_names = _get_available_project_names_nested(cursor, year, status_condition)
#                 if all_project_names:
#                     response_data['project_name'] = "\n".join(all_project_names)
#                     response_data['hint'] = f"未提供项目名称。以上是 {year + '年' if year else ''} 可用的项目名称列表，请从中选择一个或提供项目名称关键字重试。"
#                 else:
#                     response_data['hint'] = f"{year + '年' if year else ''} 未找到任何项目。请检查年份或尝试不指定年份。"
#                     response_data['result'] = "失败"
#                 logger.debug(f"未提供 project_name。年份: '{year}'。获取所有可选项目共{len(all_project_names if all_project_names else [])}个。")
#                 return json.dumps(response_data, ensure_ascii=False) # 防御None

#             sql_query_parts_match_name = [status_condition]
#             sql_params_match_name = []
#             if year:
#                 sql_query_parts_match_name.append("year = ?")
#                 sql_params_match_name.append(year)
#             sql_query_parts_match_name.append("project_name LIKE ?")
#             sql_params_match_name.append(f"%{project_name}%")

#             effective_where_clause_match_name = " AND ".join(sql_query_parts_match_name)
#             query_match_name = f"SELECT DISTINCT project_name FROM indexed_files WHERE {effective_where_clause_match_name} ORDER BY project_name"
#             logger.debug(f"执行SQL (查匹配项目名): {query_match_name} 参数: {sql_params_match_name}")
#             cursor.execute(query_match_name, sql_params_match_name)
#             matched_project_names = [str(row[0]) for row in cursor.fetchall() if row[0] is not None]
#             logger.info(f"根据关键字 '{project_name}' {('和年份 ' + year) if year else ''} 查询到 {len(matched_project_names)} 个匹配的项目名称。")

#             if len(matched_project_names) == 0:
#                 # 未匹配到任何项目
#                 response_data['result'] = "重试"; initial_hint = f"未找到与关键字 '{project_name}' {('和年份 ' + year) if year else ''} 匹配的项目。" # 移除“返回年份所有项目”的承诺
#                 all_fallback_project_names = _get_available_project_names_nested(cursor, year, status_condition)
#                 if all_fallback_project_names:
#                     response_data['project_file_list'] = "\n".join(all_fallback_project_names)
#                     response_data['hint'] = f"{initial_hint} 以下是 {year + '年' if year else ''} 可用的项目名称列表 ({len(all_fallback_project_names)}个)，请从中选择一个或提供不同的项目名称关键字重试。"
#                 else:
#                     response_data['hint'] = f"{initial_hint} 此外，在 {year + '年' if year else '所有年份'} 中也未找到任何可选项目。请检查年份或项目库。"
#                 logger.debug(f"未找到与关键字 '{project_name}' {('和年份 ' + year) if year else ''} 匹配的项目。返回{year if year else ''}所有项目{len(all_fallback_project_names if all_fallback_project_names else [])}个。") # 防御None
#             elif len(matched_project_names) == 1:
#                 # 匹配到1个项目
#                 unique_project_name = matched_project_names[0]
#                 response_data['result'] = "完成"; response_data['project_name'] = unique_project_name
#                 sql_query_files_parts = [status_condition, "project_name = ?"]
#                 sql_params_files = [unique_project_name] # 修正变量名
#                 if year:
#                     sql_query_files_parts.append("year = ?")
#                     sql_params_files.append(year) # 修正变量名
#                 effective_where_clause_files = " AND ".join(sql_query_files_parts) # 修正变量名
#                 query_files = f"SELECT file_path FROM indexed_files WHERE {effective_where_clause_files} ORDER BY file_path"
#                 logger.debug(f"执行SQL (查项目文件): {query_files} 参数: {sql_params_files}")
#                 cursor.execute(query_files, sql_params_files)
#                 file_paths = [str(row[0]) for row in cursor.fetchall() if row[0] is not None]
#                 if file_paths:
#                     response_data['project_file_list'] = "\n".join(file_paths)
#                     response_data['hint'] = f"已成功查询到项目 '{unique_project_name}' 的文件列表 ({len(file_paths)}个文件)。"
#                     response_data['token'] = uuid.uuid4().hex
#                 else:
#                     # 项目存在，但文件为空
#                     response_data['hint'] = f"项目 '{unique_project_name}' 存在，但未找到其下的任何文件记录 (状态为'{settings.DEFAULT_STATUS}'或'收口')。" # 使用 settings.DEFAULT_STATUS
#                 logger.info(f"已成功查询到匹配的项目 '{unique_project_name}' ,文件列表 ({len(file_paths)}个文件)。")
#             else: # len(matched_project_names) > 1
#                 response_data['result'] = "重试"
#                 response_data['hint'] = f"找到多个与关键字 '{project_name}' {('和年份 ' + year) if year else ''} 匹配的项目名称 ({len(matched_project_names)}个): {', '.join(matched_project_names[:5])}{'...' if len(matched_project_names) > 5 else ''}。 请提供更精确的项目名称以获得唯一结果。"
#                 # logger.info(f"已成功查询到匹配的项目 '{unique_project_name}')。") # unique_project_name 在此分支未定义，移除此日志或修正
#                 logger.info(f"找到多个匹配项目: {len(matched_project_names)} 个。")


#     except sqlite3.Error as e: logger.error(f"查询索引数据库时发生错误: {e}"); response_data['result'] = "失败"; response_data['project_file_list'] = ""; response_data['hint'] = "数据库查询时发生错误，请检查服务器日志。"
#     except Exception as e: logger.error(f"查询索引文件时发生未知错误: {e}", exc_info=True); response_data['result'] = "失败"; response_data['project_file_list'] = ""; response_data['hint'] = "查询过程中发生未知错误，请检查服务器日志。"
#     r = [f"《{key}:{value}》" for key, value in response_data.items()]
#     return "".join(r)


@project_mcp.tool()
async def query_project_files(user:str, project_name: str, year: Optional[str] = None) -> str:
    """
    根据项目名称关键字查询项目文件列表。
    参数:
        user: 发起调用的用户名
        project_name: 项目名称的关键字，采用模糊匹配加向量相似度方式检索，如果为"/ALL",返回所有项目。
        year: 项目的四位数字年份 (默认为'2024')。如果为None，则检索所有年份。
    返回:
        一个JSON字符串，当仅有一个项目匹配时，返回项目文件列表，否则返回多个候选项目名称
    """
    logger.info(f"工具调用: query_project_files, 项目关键字='{project_name}', 年份='{year}'")
    response_data = {}

    def get_project_files(cursor: sqlite3.Cursor, proj_name: str, yr: Optional[str]) -> List[str]:
        """获取指定项目的所有文件路径"""
        query_parts = ["project_name = ?"]
        params = [proj_name]
        if yr:
            query_parts.append("year = ?")
            params.append(yr)
        query = f"SELECT file_path FROM indexed_files WHERE {' AND '.join(query_parts)} ORDER BY file_path"
        cursor.execute(query, params)
        return [row[0] for row in cursor.fetchall()]

    try:
        with _connect_db(settings.DATABASE_PATH) as conn:
            cursor = conn.cursor()

            if project_name == "/ALL":
                logger.info("收到全部项目查询请求")
                all_projects ="\n".join(_get_available_project_names_nested(cursor, year))
                response_data = {"hint":f"数据库中{year or ''}年份的所有项目如下:","project_name":all_projects}
                return json.dumps(response_data, ensure_ascii=False)

            logger.debug(f"第一步: 开始模糊匹配，关键字: '{project_name}', 年份: {year}")
            sql_query_parts = ["project_name LIKE ?"]
            sql_params = [f"%{project_name}%"]
            if year:
                sql_query_parts.append("year = ?")
                sql_params.append(year)

            query = f"SELECT DISTINCT project_name FROM indexed_files WHERE {' AND '.join(sql_query_parts)}"
            cursor.execute(query, sql_params)
            matched_projects = [row[0] for row in cursor.fetchall()]
            logger.debug(f"模糊匹配查询到 {len(matched_projects)} 个项目: {matched_projects}")

            if len(matched_projects) == 1:
                the_project_name = matched_projects[0]
                logger.info(f"模糊匹配到唯一项目: '{the_project_name}'")
                project_files = get_project_files(cursor, the_project_name, year)
                response_data = {"project_name": the_project_name, "project_files": project_files,"hint":"文件较多，若用户无要求，无需罗列"}
                # 更新会话管理器
                await _update_session_manager(user,[(uuid.uuid4().hex, Path(item)) for item in project_files],the_project_name)

            elif len(matched_projects) > 1 and settings.EMBEDDING_AVAILABLE:
                logger.debug(f"模糊匹配到多个项目，使用向量检索辅助判断。")
                top_project_with_score = _find_similar_items_with_scores(project_name, matched_projects, 1)
                if top_project_with_score:
                    the_project_name = top_project_with_score[0][0]
                    logger.info(f"向量检索匹配top1项目: '{the_project_name}'")
                    project_files = get_project_files(cursor, the_project_name, year)
                    response_data = {"project_name": the_project_name, "project_files": project_files, "hint":"文件较多，若用户无要求，无需罗列"}
                    # 更新会话管理器
                    await _update_session_manager(user,[(uuid.uuid4().hex, Path(item)) for item in project_files],the_project_name)
                else:
                    response_data = {"hint": "向量检索辅助判断失败。", "project_name": matched_projects}

            elif len(matched_projects) == 0 and settings.EMBEDDING_AVAILABLE:
                logger.debug("模糊匹配未找到，使用全局向量检索。")
                all_projects = _get_available_project_names_nested(cursor, year)
                if not all_projects:
                    response_data = {"hint": f"数据库中{'在' + year + '年份' if year else ''}未找到任何项目。", "project_name": "None"}
                else:
                    similar_projects = _find_similar_items_with_scores(project_name, all_projects, 3)
                    if similar_projects and similar_projects[0][1] > 0.8:
                        the_project_name = similar_projects[0][0]
                        logger.info(f"向量检索找到高分匹配项 (分数 > 0.8): '{the_project_name}'")
                        project_files = get_project_files(cursor, the_project_name, year)
                        response_data = {"project_name": the_project_name, "project_files": project_files, "hint":"文件较多，若用户无要求，无需罗列"}
                        # 更新会话管理器
                        await _update_session_manager(user,[(uuid.uuid4().hex, Path(item)) for item in project_files],the_project_name)
                    else:
                        top_3_names = [p[0] for p in similar_projects]
                        response_data = {"hint": "未找到精确匹配的项目，是否是以下几个项目？请以数字方式列表展示给用户并重试。", "project_name": top_3_names}
            else: # Not found and embedding is not available
                 response_data = {"hint": "未找到匹配项目，且向量检索功能不可用。", "project_name": "None"}

    except sqlite3.Error as e:
        logger.error(f"数据库操作失败: {e}", exc_info=True)
        response_data = {"error": f"数据库错误: {e}"}
    except Exception as e:
        logger.error(f"处理 query_project_files 时发生未知错误: {e}", exc_info=True)
        response_data = {"error": f"未知错误: {e}"}

    return json.dumps(response_data, ensure_ascii=False)


@project_mcp.tool()
async def open_specification_files(user: str, query_spec_filename: str, category: str, read_file: bool = False, top_n: int = 3) -> str:
    """
    根据关键字向量检索规程规范文件，并可选择性地读取文件内容。

    参数:
        user: 发起调用的用户名
        query_spec_filename：要查询的规范文件全名，若无匹配，将返回最相似的文件。若为"/ALL" 返回专业下的所有规范
        category (str): 规程规范的专业类别，必须是预设类别之一。["电气","二次"，"通信"，"土建"，"线路"，"技经"，"公用"]
        read_file (bool): 是否读取最匹配文件的内容。默认为 False。
        top_n (int): 返回最相似文件路径的数量。默认为 3。

    返回:
        一个标准OpenSpecFilesResponse模型的JSON字符串。
    """
    logger.info(f"工具调用: open_specification_files, 查询: '{query_spec_filename}', 类别: '{category}', 读取文件: {read_file}, Top N: {top_n}")

    if category not in settings.SPEC_DIRS:
        msg = f"错误: 无效的专业类别 '{category}'。有效类别为: {', '.join(settings.SPEC_DIRS)}"
        logger.warning(msg)
        return OpenSpecFilesResponse(content=msg, hint="请修正专业类别后重试。").model_dump_json()

    if not settings.EMBEDDING_AVAILABLE:
        msg = "错误: 向量检索功能当前不可用。"
        logger.warning(msg)
        return OpenSpecFilesResponse(content=msg, hint="请联系管理员检查嵌入模型配置。").model_dump_json()

    try:
        all_specs_in_category = query_specs_by_category(str(settings.SPEC_DATABASE_PATH), category)

        if not all_specs_in_category:
            msg = f"在专业类别 '{category}' 下未找到任何规程规范文件。"
            logger.warning(msg)
            return OpenSpecFilesResponse(content="", hint=msg, files=[]).model_dump_json()

        if query_spec_filename == "/ALL":
            logger.info(f"用户请求 '{category}' 类别下的所有规范。")
            spec_files = [SpecFile(path=path) for path in all_specs_in_category.values()]
            return OpenSpecFilesResponse(
                content=f"'{category}' 类别下的所有规范列表。",
                hint=f"已返回 {len(spec_files)} 个规范文件。",
                files=spec_files
            ).model_dump_json()

        spec_names = list(all_specs_in_category.keys())
        similar_specs = _find_similar_items_with_scores(query_spec_filename, spec_names, top_n)

        if not similar_specs:
            msg = f"在专业 '{category}' 中未找到与 '{query_spec_filename}' 相似的规程规范。"
            logger.info(msg)
            return OpenSpecFilesResponse(content="", hint=msg, files=[]).model_dump_json()

        matched_files = [
            SpecFile(path=all_specs_in_category[name], similarity=score)
            for name, score in similar_specs
        ]

        if read_file:
            top_match = matched_files[0]
            # 检查相似度是否达到阈值
            if top_match.similarity and top_match.similarity > 0.7:
                logger.info(f"最匹配文件 '{top_match.path}' 相似度({top_match.similarity:.4f}) > 0.7，准备读取内容。")

                file_ext = Path(top_match.path).suffix.lower()
                content = ""
                # 根据文件类型选择不同的读取方式
                if file_ext == '.pdf':
                    # 对于PDF，使用通用的文件内容获取函数，它会调用pdfplumber
                    content = _get_file_content(top_match.path, delimiter="\n", type="specification")
                elif file_ext == '.md':
                    # 对于Markdown，继续使用专门的文本读取函数
                    content = _get_spec_file_content(top_match.path)
                else:
                    # 对于其他可能支持的类型（如.txt），也使用文本读取
                    content = _get_spec_file_content(top_match.path)

                if content.startswith("错误:"):
                    return OpenSpecFilesResponse(content=content, hint="读取文件时发生错误。").model_dump_json()

                if len(content) > settings.MODEL_CONTEXT_WINDOW:
                    logger.warning(f"文件 '{top_match.path}' 内容过长，可能超过模型上下文窗口。")
                    content = f"文件内容过长(超过{settings.MODEL_CONTEXT_WINDOW}字符)，已截断，请提示用户下载查看完整内容。"

                token = uuid.uuid4().hex
                await _update_session_manager(user, [(token, Path(top_match.path))], doc_type=DocType.STANDARD)

                return OpenSpecFilesResponse(
                    content=content,
                    hint="已成功读取最匹配的文件内容。",
                    token=token,
                    download_url=f"http://[{get_host_ipv6_addr()}]:{settings.SERVER_PORT}/download/{token}/{Path(top_match.path).name}",
                    file_path=top_match.path,
                    similarity=top_match.similarity
                ).model_dump_json()
            else:
                # 相似度不足，即使read_file=True也只返回列表
                logger.info(f"最匹配文件 '{top_match.path}' 相似度({top_match.similarity:.4f}) 不足 0.7，不读取文件内容，返回列表。")
                return OpenSpecFilesResponse(
                    content=f"找到了 {len(matched_files)} 个相关文件。",
                    hint="未获取到精确匹配的文件，请用户从以下文件中选择准确的规范名称后，重试",
                    files=matched_files
                ).model_dump_json()
        else:
            return OpenSpecFilesResponse(
                content=f"找到了 {len(matched_files)} 个与 '{query_spec_filename}' 相关的规程规范。",
                hint="请用户从以下文件中选择。",
                files=matched_files
            ).model_dump_json()

    except sqlite3.Error as e:
        logger.error(f"查询规程规范数据库时发生错误: {e}", exc_info=True)
        return OpenSpecFilesResponse(content=f"数据库操作失败: {e}", hint="请联系管理员检查数据库。").model_dump_json()
    except Exception as e:
        logger.error(f"查询规程规范文件时发生未知错误: {e}", exc_info=True)
        return OpenSpecFilesResponse(content=f"未知错误: {e}", hint="请联系管理员检查服务器日志。").model_dump_json()
