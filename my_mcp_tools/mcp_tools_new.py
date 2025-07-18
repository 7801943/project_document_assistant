# my_mcp_tools/mcp_tools.py

import json
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
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
from utils import file_parser # 假设 file_parser.py 可被正确导入
from utils.utils import get_host_ipv6_addr# 导入自定义工具函数
from config import settings # 导入配置

# --- FastMCP 服务器实例化 ---
# MCP_MOUNT_PATH 在主应用中定义和使用，此处不需要
project_mcp = FastMCP(
    name="项目文件检索服务器",
    instructions="一个通过 Streamable HTTP 访问的MCP服务器，提供工具来列出项目和项目文件，供LLM客户端查询和选择。"
)
logger.info(f"FastMCP 服务器 '{project_mcp.name}' 已在 my_mcp_tools/mcp_tools.py 中实例化。")

def _get_file_content(relative_file_path_str: str, delimiter: str) -> str:
    # 这个函数依赖 settings.PROJECTS_ROOT_DIR 和 file_parser
    abs_file_path = settings.PROJECTS_ROOT_DIR / relative_file_path_str
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

async def _update_session_manager(user_name: str, token: str, path_or_dir: str, is_llm_opend: bool, type: str):
    '''
    type dir或file,调用线程管理器更新状态
    '''
    from core import app_state
    if app_state.session_manager:
        if type == "file":
            await app_state.session_manager.update_opened_file(user_name, token, path_or_dir, llm_opened=is_llm_opend)
        elif type == "dir":
            await app_state.session_manager.update_opened_dir(user_name, token, path_or_dir, llm_opened=is_llm_opend)
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
        返回从知识库中检索的内容，多个文本块，约数千字
    """
    logger.debug(f"工具调用: query_specification_knowledge_base, 知识库: {knowledge_base_name}, 用户查询: '{user_query[:50]}...', top_k: {top_k}")
    result = ""
    header = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.DIFY_KNOWLEDGEBASE_APIKEY.get_secret_value()}"
    }
    knowledge_base_id = ""
    try:
        url_get_id = f"{settings.DIFY_KNOWLEDGEBASE_URL}/datasets"
        param_get_id = {"keyword": knowledge_base_name, "page": 1, "limit": 10}
        logger.debug(f"正在从 {url_get_id} 获取知识库ID，参数: {param_get_id}")
        response_get_id = requests.get(url_get_id, headers=header, params=param_get_id, timeout=10)
        response_get_id.raise_for_status()
        data_get_id = response_get_id.json()
        if data_get_id and data_get_id.get('data'):
            knowledge_base_id = data_get_id['data'][0].get('id')
            logger.info(f"成功获取到知识库 '{knowledge_base_name}' 的ID: {knowledge_base_id}")
        else:
            logger.warning(f"未找到名为 '{knowledge_base_name}' 的知识库。响应: {data_get_id}")
            return f"错误: 未找到名为 '{knowledge_base_name}' 的知识库。"

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
            for index, item in enumerate(retrieval_content):
                doc_name = item.get('segment', {}).get('document', {}).get('name', '未知文档')
                score = item.get('score', 'N/A')
                content = item.get('segment', {}).get('content', '无内容')
                result += f"\n检索结果 {index + 1}\n来自源文档：{doc_name}\n相似度分数：{score}\n内容如下：\n{content}\n\n"
        else:
            logger.info(f"知识库 '{knowledge_base_name}' 对于查询 '{user_query[:50]}...' 未返回任何结果。")
            result = f"知识库 '{knowledge_base_name}' 未检索到与 '{user_query}' 相关的内容。"
    except requests.exceptions.Timeout:
        logger.error(f"请求Dify API超时。知识库: {knowledge_base_name}, 查询: '{user_query[:50]}...'")
        result = "错误: 知识库请求超时，请稍后再试。"
    except requests.exceptions.ConnectionError:
        logger.error(f"连接Dify API失败。知识库: {knowledge_base_name}, 查询: '{user_query[:50]}...'")
        result = "错误: 无法连接到知识库服务。"
    except requests.exceptions.HTTPError as e:
        logger.error(f"Dify API请求失败: {e.response.status_code} - {e.response.text}. 知识库: {knowledge_base_name}, 查询: '{user_query[:50]}...'")
        result = f"错误: 知识库服务返回错误 {e.response.status_code}。"
    except json.JSONDecodeError as e:
        logger.error(f"解析Dify API响应JSON失败: {e}. 知识库: {knowledge_base_name}, 查询: '{user_query[:50]}...'")
        result = "错误: 解析知识库响应失败。"
    except KeyError as e:
        logger.error(f"处理Dify API响应时发生KeyError: {e}. 知识库: {knowledge_base_name}, 查询: '{user_query[:50]}...'")
        result = "错误: 处理知识库响应数据时出错。"
    except Exception as e:
        logger.error(f"查询知识库时发生未知错误: {e}. 知识库: {knowledge_base_name}, 查询: '{user_query[:50]}...'", exc_info=True)
        result = "错误: 查询知识库时发生未知错误。"
    return result

@project_mcp.tool()
def compare_project_file(file_path1: str, file_path2: str, file_type: str, sheet_name: Optional[str] = None, all_sheet: bool = False) -> str:
    """
    使用diff函数比较两个项目文件的差异，返回差异结果。
    参数：
        file_path1: 需要比较的文件1（比如XXX送审版）的绝对路径。
        file_path2: 需要比较的文件2（比如XXX收口版）的绝对路径。
        file_type:  文件类型，可选值为 "报告（说明书）", "材料清册", "概算表"。
        sheet_name: 表名（仅当file_type为"概算表"或"材料清册"且文件为XLSX时需要，其他情况可忽略）。如果 all_sheet 为 True，此参数将被忽略。
        all_sheet:  布尔值，默认为 False。如果为 True，则对“材料清册”和“概算表”类型的文件，比较其所有同名sheet，并在输出中包含各表名描述。
    返回：
        返回文件差异结果字符串，包含两个文件的文件名作为标题。如果文件不存在、类型不支持或比较出错，则返回错误信息。
    """
    logger.info(f"工具调用: compare_project_file. 文件1: '{file_path1}', 文件2: '{file_path2}', 文件类型: '{file_type}', Sheet名: '{sheet_name}', All Sheets: {all_sheet}")
    if not file_type in ["报告（说明书）", "材料清册", "概算表"]:
        msg = f"错误: 不支持的文件类型 '{file_type}'。支持的类型有 '报告（说明书）', '材料清册', '概算表'。"
        logger.warning(msg); return msg
    if not os.path.exists(file_path1):
        msg = f"错误: 文件1未找到: {file_path1}"
        logger.error(msg)
        return msg
    if not os.path.exists(file_path2):
        msg = f"错误: 文件2未找到: {file_path2}"
        logger.error(msg)
        return msg
    result_header = f"比较文件:\n  1. {os.path.basename(file_path1)}\n  2. {os.path.basename(file_path2)}\n"
    try:
        if file_type == "概算表" or (file_type == "材料清册" and (file_path1.endswith(('.xlsx', '.xls')) or file_path2.endswith(('.xlsx', '.xls')))):
            if all_sheet:
                logger.info(f"对文件 '{file_path1}' 和 '{file_path2}' (类型: {file_type}) 进行所有Sheet的比较。")
                sheet_names1_list = file_parser.get_xlsx_sheet_names(file_path1)
                if not sheet_names1_list and os.path.exists(file_path1):
                    logger.warning(f"无法从文件1 '{os.path.basename(file_path1)}' 读取工作表列表，或文件不包含工作表。")
                sheet_names2_list = file_parser.get_xlsx_sheet_names(file_path2)
                if not sheet_names2_list and os.path.exists(file_path2):
                    logger.warning(f"无法从文件2 '{os.path.basename(file_path2)}' 读取工作表列表，或文件不包含工作表。")
                sheet_names1 = set(sheet_names1_list if sheet_names1_list else []) # 防御None
                sheet_names2 = set(sheet_names2_list if sheet_names2_list else []) # 防御None
                common_sheets = sorted(list(sheet_names1.intersection(sheet_names2)))
                sheets_only_in_file1 = sorted(list(sheet_names1 - sheet_names2))
                sheets_only_in_file2 = sorted(list(sheet_names2 - sheet_names1))
                comparison_results = [result_header]
                if not common_sheets and not sheets_only_in_file1 and not sheets_only_in_file2:
                     comparison_results.append("两个Excel文件均不包含任何sheet页，或无法读取sheet列表。\n")
                     return "".join(comparison_results)
                if common_sheets:
                    comparison_results.append("--- 共同存在的Sheet比较结果 ---\n")
                    for s_name in common_sheets:
                        current_sheet_header = f"Sheet名称: {s_name}\n" + "-" * 30 + "\n"
                        col_conf = settings.SHEET_COLUMN_CONFIG.get(s_name) if file_type == "概算表" else None
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
                    [comparison_results.append(f"- {s_name}\n") for s_name in sheets_only_in_file1]
                    comparison_results.append("\n")
                if sheets_only_in_file2:
                    comparison_results.append(f"--- 仅存在于文件 '{os.path.basename(file_path2)}' 的Sheet ---\n")
                    [comparison_results.append(f"- {s_name}\n") for s_name in sheets_only_in_file2]
                    comparison_results.append("\n")
                return "".join(comparison_results)
            else:
                if not sheet_name:
                    msg = f"错误: 文件类型 '{file_type}' (Excel) 且 all_sheet=False 时，需要提供 sheet_name 进行比较。"
                    logger.warning(msg); return msg
                current_sheet_header = result_header + f"Sheet名称: {sheet_name}\n" + "-" * 30 + "\n"
                col_conf = settings.SHEET_COLUMN_CONFIG.get(sheet_name) if file_type == "概算表" else None
                content1_lines = file_parser.parse_xlsx_sheet_content(file_path1, sheet_name, col_conf)
                content2_lines = file_parser.parse_xlsx_sheet_content(file_path2, sheet_name, col_conf)
                if not content1_lines and not content2_lines:
                    return f"{current_sheet_header}错误: 无法解析文件1和文件2的Sheet '{sheet_name}' 内容，或内容均为空。"
                elif not content1_lines:
                    return f"{current_sheet_header}错误: 无法解析文件1的Sheet '{sheet_name}' 内容，或内容为空。"
                elif not content2_lines:
                    return f"{current_sheet_header}错误: 无法解析文件2的Sheet '{sheet_name}' 内容，或内容为空。"
                logger.debug(f"已从Excel文件 '{file_path1}' 和 '{file_path2}' 的Sheet '{sheet_name}' (列配置: {col_conf}) 读取内容进行比较。")
                diff = difflib.unified_diff(content1_lines, content2_lines, fromfile=os.path.basename(file_path1), tofile=os.path.basename(file_path2), lineterm='')
                diff_output = list(diff)
                if not diff_output: logger.info(f"Sheet '{sheet_name}' 内容一致。"); return f"{current_sheet_header}文件内容一致。"
                else: logger.info(f"Sheet '{sheet_name}' 存在差异。"); filtered_diff = [line for line in diff_output if not (line.startswith("--- ") or line.startswith("+++ "))]; return f"{current_sheet_header}差异内容如下:\n" + "\n".join(filtered_diff)
        elif file_type == "报告（说明书）" or file_type == "材料清册":
            if file_type == "材料清册" and (file_path1.endswith(('.xlsx', '.xls')) or file_path2.endswith(('.xlsx', '.xls'))):
                if not all_sheet and not sheet_name:
                    msg = f"错误: 材料清册 (Excel) 比较需要提供 sheet_name (当 all_sheet=False)。"
                    logger.warning(msg)
                    return msg
            current_file_header = result_header + "-" * 30 + "\n"
            try:
                # 这部分需要 PROJECTS_ROOT_DIR，它通过 settings 导入
                relative_file_path1_str = str(Path(file_path1).relative_to(settings.PROJECTS_ROOT_DIR))
                relative_file_path2_str = str(Path(file_path2).relative_to(settings.PROJECTS_ROOT_DIR))
            except ValueError as e:
                logger.error(f"无法将文件路径转换为相对路径 (相对于 {settings.PROJECTS_ROOT_DIR}): {e}")
                return f"{current_file_header}错误: 文件路径 {file_path1} 或 {file_path2} 不在预期的项目根目录下。"
            raw_content1 = _get_file_content(relative_file_path1_str, delimiter="")
            content1_lines = raw_content1.splitlines()
            if raw_content1.startswith("错误:"):
                logger.error(f"解析文件1 ({relative_file_path1_str}) 失败: {raw_content1}")
                return f"{current_file_header}错误: 解析文件 {os.path.basename(file_path1)} 失败: {raw_content1}"
            raw_content2 = _get_file_content(relative_file_path2_str, delimiter="")
            content2_lines = raw_content2.splitlines()
            if raw_content2.startswith("错误:"):
                logger.error(f"解析文件2 ({relative_file_path2_str}) 失败: {raw_content2}"); return f"{current_file_header}错误: 解析文件 {os.path.basename(file_path2)} 失败: {raw_content2}"
            logger.debug(f"已将文件 '{file_path1}' (相对: {relative_file_path1_str}) 和 '{file_path2}' (相对: {relative_file_path2_str}) 作为文本文件读取内容进行比较。")
            diff = difflib.unified_diff(content1_lines, content2_lines, fromfile=os.path.basename(file_path1), tofile=os.path.basename(file_path2), lineterm='')
            diff_output = list(diff)
            if not diff_output:
                logger.info(f"文本文件 '{file_path1}' 和 '{file_path2}' 内容一致。")
                return f"{current_file_header}文件内容一致。"
            else:
                logger.info(f"文本文件 '{file_path1}' 和 '{file_path2}' 存在差异。")
                filtered_diff = [line for line in diff_output if not (line.startswith("--- ") or line.startswith("+++ "))]
                return f"{current_file_header}差异内容如下:\n" + "\n".join(filtered_diff)
        else:
            msg = f"内部错误: 未处理的文件类型 '{file_type}'。"
            logger.error(msg); return msg
    except ValueError as ve:
        logger.error(f"比较文件时发生值错误: {ve}"); header_for_error = result_header
        if sheet_name and not all_sheet: header_for_error += f"Sheet名称: {sheet_name}\n"
        header_for_error += "-" * 30 + "\n"; return f"{header_for_error}错误: {ve}"
    except Exception as e:
        logger.error(f"比较文件时发生未知错误: {e}", exc_info=True); header_for_error = result_header
        if sheet_name and not all_sheet: header_for_error += f"Sheet名称: {sheet_name}\n"
        header_for_error += "-" * 30 + "\n"; return f"{header_for_error}错误: 比较文件时发生未知错误 - {e}"

@project_mcp.tool()
async def read_project_file(user_name:str, relative_file_path: str, file_category: str, sheet_name: str = "") -> str:
    """
    读取项目文件的文件内容并返回。
    参数：
        user_name : 发起对话的用户名，必填
        relative_file_path: 需要解析的文件的相对路径，通常由 query_project_file_path函数返回，必填
        file_category: "普通文档" "图纸图形文档" "概算书文档" 必填之一
        sheet_name: 如果为file_category="概算书文档" ，必填。
    返回：
        文件内容、下载链接 和下载token（下载token不要向用户展示）。操作结果("完成" "重试" "失败")
    """
    # 防止循环引用，在函数内部导入
    from core import app_state
    logger.info(f"工具调用 (LLM): read_project_file. Relative Path: {relative_file_path}, file_category:{file_category}")
    response_data ={
        'result':"",
        'token':"",
        'file_path':"",
        'download_url':"",
        "file_content":"",
        "hint":"文件内容较多，无需全部输出,向用户提供下载链接"
        }
    all_sheet_names = "" # 初始化
    abs_file_path = settings.PROJECTS_ROOT_DIR / relative_file_path
    if not abs_file_path.exists():
        logger.error(f"请求的文件路径{relative_file_path}不存在。)")
        response_data['file_content'] = f"<文件 {relative_file_path} 路径不存在。>"
        response_data["result"] = "失败"
    elif file_category == "概算书文档":
        sheet_names_list_local = file_parser.get_xlsx_sheet_names(str(abs_file_path)) # 确保传递 str
        if not sheet_name:
            if not sheet_names_list_local:
                logger.warning(f"文件 '{relative_file_path}' 不包含任何工作表，或无法读取工作表列表。")
                response_data['file_content'] = f"读取文件 {relative_file_path} 失败: 文件 '{relative_file_path}' 不包含任何工作表，或无法读取工作表列表。"
                response_data["result"] = "失败"
            else:
                all_sheet_names = "\n".join(sheet_names_list_local)
                logger.info(f"未指定表名， {relative_file_path} 中的Sheet名称: {sheet_names_list_local}，等待重试")
                response_data['file_content'] = f"<未指定表名，文件 {relative_file_path} 的sheets如下：{all_sheet_names}，请指定表名重试。>"; response_data["result"] = "重试" # 使用 all_sheet_names
        else:
            # all_sheet_names = file_parser.get_xlsx_sheet_names(abs_file_path) # 重复获取，上面已有 sheet_names_list_local
            if not sheet_names_list_local or sheet_name not in sheet_names_list_local: # 使用 sheet_names_list_local
                available_sheets_str = "\n".join(sheet_names_list_local) if sheet_names_list_local else "无可用Sheet"
                logger.warning(f"Sheet '{sheet_name}' 在文件 {relative_file_path} 中未找到。可用Sheets: {available_sheets_str}")
                response_data['file_content'] = f"文件 {relative_file_path} 的sheet'{sheet_name}'未找到， 可用Sheets: {available_sheets_str}。"
                response_data["result"] = "重试"
            else:
                content_lines = file_parser.parse_xlsx_sheet_content(str(abs_file_path), sheet_name, column_config=None) # 确保传递 str
                if not content_lines:
                    logger.warning(f"无法从文件 '{relative_file_path}' 的 Sheet '{sheet_name}' 解析内容，或该Sheet为空。")
                    response_data['file_content'] = f"无法从文件 '{relative_file_path}' 的 Sheet '{sheet_name}' 解析内容，或该Sheet为空。"
                    response_data["result"] = "失败"
                else:
                    sheet_content = "\n".join(content_lines)
                    preview_len = min(100,len(sheet_content))
                    logger.info(f"从文件 {relative_file_path}成功读取 sheet:{sheet_name}（预览100字）:{sheet_content[0:preview_len]}")
                    response_data['file_content'] = "\n".join(content_lines)
                    response_data["result"] = "完成"
    elif file_category == "图纸图形文档":
        response_data["file_content"] = "文件为你暂不支持的文件类型，向用户提供下载链接"
        response_data["result"] = "完成"
        logger.debug(f"{relative_file_path} 为llm暂不支持的文件类型。")
    else: # 普通文档
        file_content_data = _get_file_content(relative_file_path, delimiter="\t") # get_file_content 已在本文件定义
        if file_content_data.startswith("错误:"):
            logger.error(f"读取文件 {relative_file_path} 失败: {file_content_data}")
            response_data['file_content'] = f"读取文件 {relative_file_path} 失败: {file_content_data}"
            response_data["result"] = "失败"
        else:
            response_data["file_content"] = file_content_data
            response_data["result"] = "完成"
            preview_len = min(100, (len(file_content_data)))
            logger.info(f"从文件 {relative_file_path}成功读取（预览100字）:{file_content_data[0:preview_len]}")

    if response_data['result'] != "失败":
        token_str = uuid.uuid4().hex
        # get_host_ipv6_addr() 和 settings.SERVER_PORT 来自导入
        download_url = f"http://[{get_host_ipv6_addr()}]:{settings.SERVER_PORT}/download/{token_str}/{abs_file_path.name}"
        response_data['download_url'] = download_url; response_data['file_path'] = relative_file_path; response_data['token'] = token_str
        logger.debug(f"为文件:{relative_file_path} 生成LLM工具下载token {token_str}")
        # 注册下载路径
        if app_state.session_manager:
            await app_state.session_manager.update_opened_file(user_name, token_str, relative_file_path, llm_opened = True)
        else:
            logger.error("Session manager is not initialized, cannot register opened file.")
    r = [f"《{key}:{value}》" for key, value in response_data.items()]
    return "".join(r)

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

# --- 可复用的向量检索辅助函数 ---



# --- MCP 工具定义 ---

@project_mcp.tool()
async def query_project_files(project_name: str, year: Optional[str] = None) -> str:
    """
    根据项目名称关键字查询项目文件列表。
    参数:
        project_name: 项目名称的关键字，采用模糊匹配加向量相似度方式检索，如果为"/ALL",返回所有项目。
        year: 项目的四位数字年份 (可选, 如'2024')。如果为None，则检索所有年份。
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
                response_data = {"project_name": the_project_name, "project_files": project_files}

            elif len(matched_projects) > 1 and settings.EMBEDDING_AVAILABLE:
                logger.debug(f"模糊匹配到多个项目，使用向量检索辅助判断。")
                top_project_with_score = _find_similar_items_with_scores(project_name, matched_projects, 1)
                if top_project_with_score:
                    the_project_name = top_project_with_score[0][0]
                    logger.info(f"向量检索匹配top1项目: '{the_project_name}'")
                    project_files = get_project_files(cursor, the_project_name, year)
                    response_data = {"project_name": the_project_name, "project_files": project_files}
                else:
                    response_data = {"hint": "向量检索辅助判断失败。", "project_name": matched_projects}

            elif len(matched_projects) == 0 and settings.EMBEDDING_AVAILABLE:
                logger.debug("模糊匹配未找到，使用全局向量检索。")
                all_projects = _get_available_project_names_nested(cursor, year)
                if not all_projects:
                    response_data = {"hint": f"数据库中{'在' + year + '年份' if year else ''}未找到任何项目。", "project_name": "None"}
                else:
                    similar_projects = _find_similar_items_with_scores(project_name, all_projects, 5)
                    if similar_projects and similar_projects[0][1] > 0.8:
                        the_project_name = similar_projects[0][0]
                        logger.info(f"向量检索找到高分匹配项 (分数 > 0.8): '{the_project_name}'")
                        project_files = get_project_files(cursor, the_project_name, year)
                        response_data = {"project_name": the_project_name, "project_files": project_files}
                    else:
                        top_5_names = [p[0] for p in similar_projects]
                        response_data = {"hint": "未找到精确匹配的项目，以下是相似度最高的几个项目，请重试。", "project_name": top_5_names}
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
def query_specification_files(query: str, category: str, read_file: bool = False, top_n: int = 5) -> str:
    """
    根据关键字向量检索规程规范文件，并可选择性地读取文件内容。

    参数:
        query (str): 文件名称的关键字。
        category (str): 规程规范的专业类别，必须是预设类别之一。
        read_file (bool): 是否读取最匹配文件的内容。默认为 False。
        top_n (int): 返回最相似文件路径的数量。默认为 5。

    返回:
        - 如果 read_file 为 False，返回一个JSON字符串，包含匹配的文件路径列表。
        - 如果 read_file 为 True，返回一个JSON字符串，包含最匹配文件的路径和内容。
        - 如果出错或未找到，返回一个包含错误信息的JSON字符串。
    """
    logger.info(f"工具调用: query_specification_files, 查询: '{query}', 类别: '{category}', 读取文件: {read_file}, Top N: {top_n}")

    if category not in settings.SPEC_DIRS:
        error_msg = f"错误: 无效的专业类别 '{category}'。有效类别为: {', '.join(settings.SPEC_DIRS)}"
        logger.warning(error_msg)
        return json.dumps({"error": error_msg}, ensure_ascii=False)

    if not settings.EMBEDDING_AVAILABLE:
        return json.dumps({"error": "向量检索功能当前不可用。"}, ensure_ascii=False)

    response_data = {}
    try:
        with _connect_db(settings.SPEC_DATABASE_PATH) as conn:
            cursor = conn.cursor()

            # 1. 根据 category 从数据库获取所有相关文件
            cursor.execute("SELECT name, relative_path FROM spec_files WHERE category = ?", (category,))
            # 创建一个从规程名称到其相对路径的映射
            all_specs_in_category = {row[0]: row[1] for row in cursor.fetchall() if row[0]}

            if not all_specs_in_category:
                msg = f"在专业类别 '{category}' 下未找到任何规程规范文件。"
                logger.warning(msg)
                return json.dumps({"hint": msg, "files": []}, ensure_ascii=False)

            # 2. 使用向量检索进行相似度匹配
            spec_names = list(all_specs_in_category.keys())
            similar_specs = _find_similar_items_with_scores(query, spec_names, top_n)

            if not similar_specs:
                msg = f"在专业 '{category}' 中未找到与 '{query}' 相似的规程规范。"
                logger.info(msg)
                return json.dumps({"hint": msg, "files": []}, ensure_ascii=False)

            # 将匹配到的规程名称映射回完整的相对路径
            matched_paths = [all_specs_in_category[name] for name, score in similar_specs]

            if read_file:
                top_match_path = matched_paths[0]
                logger.info(f"找到最匹配的文件: '{top_match_path}' (相似度: {similar_specs[0][1]:.4f})，准备读取内容。")
                content = _get_spec_file_content(top_match_path)
                if content.startswith("错误:"):
                    response_data = {"error": content}
                else:
                    long_content_size = 64000
                    if len(content) > long_content_size:
                        logger.warning(f"规范：{top_match_path} 过大(超过{long_content_size})，可能超过模型上下文窗口。")
                    response_data = {
                        "hint": f"已成功读取最匹配的文件内容。",
                        "file_path": top_match_path,
                        "similarity": f"{similar_specs[0][1]:.4f}",
                        "content": content
                    }
            else:
                logger.info(f"找到 {len(matched_paths)} 个匹配的文件路径。")
                files_with_scores = [{"path": all_specs_in_category[name], "similarity": f"{score:.4f}"} for name, score in similar_specs]
                response_data = {
                    "hint": f"成功找到 {len(matched_paths)} 个与 '{query}' 相关的规程规范文件。",
                    "files": files_with_scores
                }

    except sqlite3.Error as e:
        logger.error(f"查询规程规范数据库时发生错误: {e}", exc_info=True)
        response_data = {"error": f"数据库操作失败: {e}"}
    except Exception as e:
        logger.error(f"查询规程规范文件时发生未知错误: {e}", exc_info=True)
        response_data = {"error": f"未知错误: {e}"}

    return json.dumps(response_data, ensure_ascii=False)
