# mcp_client_test.py
import asyncio
import json
import traceback
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession # Import ToolCallResult for type hinting if needed
from mcp.types import CallToolResult, ListToolsResult

# MCP服务器的URL
SERVER_URL = "http://localhost:8888/mcp/mcp" # <--- 更新端口号

def display_tool_result(call_result: CallToolResult):
    """Helper function to display the result of a tool call."""
    print("\n--- 服务器响应 ---")
    if not call_result.isError and call_result.content:
        server_response_text = ""
        content_to_parse = call_result.content

        # FastMCP often returns a list containing a single content item (e.g., TextContent)
        if isinstance(content_to_parse, list) and len(content_to_parse) > 0:
            content_item = content_to_parse[0]
        else:
            content_item = content_to_parse

        # 优先尝试从 content_item 中提取 text 属性
        if hasattr(content_item, 'text'):
            server_response_text = getattr(content_item, 'text', '')
        elif isinstance(content_item, str):
            # 尝试解析为JSON，以处理 open_specification_files 等工具的结构化返回
            try:
                parsed_json = json.loads(content_item)
                if isinstance(parsed_json, dict):
                    if "files" in parsed_json and (isinstance(parsed_json["files"], list) or isinstance(parsed_json["files"], str)):
                        server_response_text += parsed_json.get("hint", "找到以下文件:") + "\n"
                        if isinstance(parsed_json["files"], list):
                            for f_item in parsed_json["files"]:
                                if isinstance(f_item, dict) and "path" in f_item:
                                    server_response_text += f"- 文件: {f_item['path']}"
                                    if "similarity" in f_item:
                                        server_response_text += f" (相似度: {f_item['similarity']})"
                                    server_response_text += "\n"
                        else: # For /ALL case where files is a string
                            server_response_text += parsed_json["files"] + "\n"
                        if "project_name" in parsed_json: # For query_project_files /ALL case
                            server_response_text += f"项目名称: {parsed_json['project_name']}\n"
                    elif "content" in parsed_json:
                        server_response_text += parsed_json.get("hint", "文件内容:") + "\n"
                        server_response_text += f"文件路径: {parsed_json.get('file_path', 'N/A')}\n"
                        if "similarity" in parsed_json:
                            server_response_text += f"相似度: {parsed_json['similarity']}\n"
                        server_response_text += "内容:\n" + parsed_json["content"]
                    elif "project_name" in parsed_json and "project_files" in parsed_json:
                        server_response_text += parsed_json.get("hint", "项目文件列表:") + "\n"
                        server_response_text += f"项目名称: {parsed_json['project_name']}\n"
                        server_response_text += "文件列表:\n" + "\n".join(parsed_json["project_files"])
                    elif "error" in parsed_json:
                        server_response_text = f"错误: {parsed_json['error']}"
                    elif "hint" in parsed_json: # Generic hint
                        server_response_text = parsed_json["hint"]
                    else:
                        server_response_text = json.dumps(parsed_json, indent=2, ensure_ascii=False)
                else: # Not a dict, just dump it
                    server_response_text = json.dumps(parsed_json, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                # 如果不是有效的JSON，则直接使用原始字符串
                server_response_text = content_item
        elif isinstance(content_item, dict) or isinstance(content_item, list): # If result is JSON
            server_response_text = json.dumps(content_item, indent=2, ensure_ascii=False)
        else:
            error_msg = f"错误: 未能识别的响应内容类型: {type(content_item)}"
            print(error_msg + f" 原始值: {repr(content_item)}")
            server_response_text = error_msg + f"\n请检查服务器返回的原始结构: {repr(content_item)}"

        print(server_response_text)

    elif call_result.isError:
        error_content_str = repr(call_result.content)
        # 尝试从 content 中提取文本或 detail
        if isinstance(call_result.content, list) and len(call_result.content) > 0:
            first_content_item = call_result.content[0]
            if hasattr(first_content_item, 'text'):
                error_content_str = getattr(first_content_item, 'text', '')
            elif isinstance(first_content_item, dict) and "detail" in first_content_item:
                error_content_str = first_content_item.get("detail", "")
            elif isinstance(first_content_item, str):
                error_content_str = first_content_item
        elif hasattr(call_result.content, 'text'):
            error_content_str = getattr(call_result.content, 'text', '')
        elif isinstance(call_result.content, dict) and "detail" in call_result.content:
            error_content_str = call_result.content.get("detail", "")
        elif isinstance(call_result.content, str):
            error_content_str = call_result.content

        print(f"错误: 服务器工具调用返回错误: {error_content_str}")
    else:
        print("信息: 服务器未返回有效内容，或者响应为空。")

async def get_params_for_query_specification_knowledge_base():
    params = {}
    params["user_query"] = input("请输入用户查询内容: ").strip()
    params["knowledge_base_name"] = input("请输入知识库名称 (例如: 电气, 二次): ").strip()
    top_k_str = input("请输入top_k (可选, 整数, 回车使用默认值): ").strip()
    if top_k_str.isdigit():
        params["top_k"] = int(top_k_str)
    return params

async def get_params_for_query_specification_files():
    """为 open_specification_files 工具交互式地获取参数。"""
    params = {}
    print("\n--- 配置 'open_specification_files' 工具参数 ---")

    user = input("请输入用户名 (必需): ").strip()
    while not user:
        print("错误: 用户名不能为空。")
        user = input("请输入用户名 (必需): ").strip()
    params["user"] = user

    query = input("请输入规程规范的查询关键字 (必需, 输入 '/ALL' 返回所有规范): ").strip()
    while not query:
        print("错误: 查询关键字不能为空。")
        query = input("请输入规程规范的查询关键字 (必需, 输入 '/ALL' 返回所有规范): ").strip()
    params["query_spec_filename"] = query # 参数名从 'query' 改为 'query_spec_filename'

    category = input("请输入专业类别 (例如: 电气, 二次, 通信, 线路): ").strip()
    while not category:
        print("错误: 专业类别不能为空。")
        category = input("请输入专业类别 (例如: 电气, 二次, 通信, 线路): ").strip()
    params["category"] = category

    read_file_input = input("是否读取最匹配文件的内容? (y/n, 默认 n): ").strip().lower()
    params["read_file"] = read_file_input == 'y'

    top_n_str = input("请输入返回结果的数量 (可选, 整数, 回车使用默认值 3): ").strip() # 默认值改为3
    if top_n_str.isdigit():
        params["top_n"] = int(top_n_str)
    else:
        params["top_n"] = 3 # 默认值改为3

    return params

async def get_params_for_vector_query():
    params = {}
    query = input("请输入项目查询关键字 (必需): ").strip()
    while not query:
        print("错误: 项目查询关键字不能为空。")
        query = input("请输入项目查询关键字 (必需): ").strip()
    params["query"] = query

    top_n_str = input("请输入返回的最相似结果数量 (可选, 整数, 回车使用默认值5): ").strip()
    if top_n_str.isdigit():
        params["top_n"] = int(top_n_str)
    else:
        params["top_n"] = 5
        print("提示: 使用默认值 top_n=5")
    return params

async def get_params_two_stage_vector_query():
    params = {}
    user = input("请输入用户名 (必需): ").strip()
    while not user:
        print("错误: 用户名不能为空。")
        user = input("请输入用户名 (必需): ").strip()
    params["user"] = user
    #params['query_type'] = "project"
    query = input("请输入项目查询关键字 (必需): ").strip()
    while not query:
        print("错误: 项目查询关键字不能为空。")
        query = input("请输入项目查询关键字 (必需): ").strip()
    params["project_name"] = query
    year_keyword = input("请输入年份 (可选, 精确匹配, 回车跳过): ").strip()
    if year_keyword: params['year'] = year_keyword
    # top_n_str = input("请输入返回的最相似结果数量 (可选, 整数, 回车使用默认值5): ").strip()
    # if top_n_str.isdigit():
    #     params["top_n"] = int(top_n_str)
    # else:
    #     params["top_n"] = 5
    #     print("提示: 使用默认值 top_n=5")
    return params

async def get_params_for_write_review_doc():
    """为 write_review_doc 工具交互式地获取参数。"""
    params = {}
    print("\n--- 配置 'write_review_doc' 工具参数 ---")

    get_manual_input = input("是否获取操作指令 (get_manual=True)? (y/n, 默认 n): ").strip().lower()
    get_manual = get_manual_input == 'y'
    params["get_manual"] = get_manual

    template_type = input("请输入模板类型 (例如: 主变扩建工程模板): ").strip()
    params["template_type"] = template_type

    if not get_manual:
        project_name = input("请输入项目名称 (必需): ").strip()
        while not project_name:
            print("错误: 项目名称不能为空。")
            project_name = input("请输入项目名称 (必需): ").strip()
        params["project_name"] = project_name

        print("请输入包含评审信息的JSON内容。")
        print("示例: {\"item1\": \"意见A\", \"item2\": \"意见B\"}")
        content_str = input("JSON内容 (必需): ").strip()
        while not content_str:
            print("错误: 内容不能为空。")
            content_str = input("JSON内容 (必需): ").strip()
        params["content"] = content_str

    return params

async def get_params_dynamically(tool_definition):
    params = {}
    tool_name = tool_definition.name
    print(f"为工具 '{tool_name}' 输入参数:")

    input_schema = getattr(tool_definition, 'inputSchema', None)
    if not input_schema or not isinstance(input_schema, dict):
        print(f"警告: 工具 '{tool_name}' 没有提供有效的 inputSchema。请手动输入JSON格式的参数。")
        try:
            params_json_str = input("请输入JSON格式的参数对象 (例如 {\"key\": \"value\"}): ").strip()
            if params_json_str:
                params = json.loads(params_json_str)
        except json.JSONDecodeError:
            print("错误的JSON格式。将使用空参数调用。")
        return params

    properties = input_schema.get('properties')
    if not properties or not isinstance(properties, dict):
        print(f"警告: 工具 '{tool_name}' 的 inputSchema 中没有 'properties' 定义。无法动态收集参数。")
        return params

    required_params_list = input_schema.get('required', [])

    for param_name, param_schema in properties.items():
        if not isinstance(param_schema, dict):
            print(f"警告: 参数 '{param_name}' 的 schema 定义无效，已跳过。")
            continue

        param_title = param_schema.get('title', param_name)
        default_value = param_schema.get('default') # Might be None

        # Determine type and if it's truly optional (e.g. due to 'type': 'null' in anyOf)
        raw_param_type = param_schema.get('type')
        actual_param_type = "string" # Default if type info is complex/missing
        is_truly_optional = False

        if isinstance(raw_param_type, str):
            actual_param_type = raw_param_type
        elif isinstance(raw_param_type, list): # Handles cases like "type": ["string", "null"]
            if "null" in raw_param_type:
                is_truly_optional = True
            # Pick the first non-null type as the primary type for prompting
            for t in raw_param_type:
                if t != "null":
                    actual_param_type = t
                    break

        # Check anyOf for null type, which also makes it optional
        any_of_types = param_schema.get('anyOf')
        if isinstance(any_of_types, list):
            found_non_null_type_in_anyof = False
            for type_option in any_of_types:
                if isinstance(type_option, dict) and type_option.get('type') == 'null':
                    is_truly_optional = True
                elif isinstance(type_option, dict) and 'type' in type_option and not found_non_null_type_in_anyof:
                    actual_param_type = type_option['type'] # Use first non-null type from anyOf
                    found_non_null_type_in_anyof = True
            if not found_non_null_type_in_anyof and any_of_types: # if anyOf only had null or was malformed
                 actual_param_type = "string" # fallback

        is_required_by_schema = param_name in required_params_list and not is_truly_optional

        prompt_parts = [f"  {param_title} ({actual_param_type})"]
        if default_value is not None:
            prompt_parts.append(f"(默认: {default_value})")

        if is_required_by_schema:
            prompt_parts.append("(必需): ")
        else:
            prompt_parts.append("(可选, 回车使用默认或跳过): ")

        prompt = " ".join(prompt_parts)
        value_str = input(prompt).strip()

        final_value = None
        use_value = False

        if value_str:
            use_value = True
            try:
                if actual_param_type == "integer":
                    final_value = int(value_str)
                elif actual_param_type == "number": # JSON schema "number" can be float
                    final_value = float(value_str)
                elif actual_param_type == "boolean":
                    final_value = value_str.lower() in ['true', 't', 'yes', 'y', '1']
                elif actual_param_type == "string":
                    final_value = value_str
                # For "array" or "object", direct CLI input is complex.
                # We can ask for JSON string, or skip for now.
                elif actual_param_type in ["array", "object"]:
                    try:
                        final_value = json.loads(value_str)
                        print(f"提示: 参数 '{param_title}' 作为JSON对象/数组解析。")
                    except json.JSONDecodeError:
                        print(f"警告: 为 '{param_title}' 输入的不是有效的JSON字符串。将作为普通字符串处理: '{value_str}'")
                        final_value = value_str # Fallback to string if JSON parse fails
                else: # Unknown type, treat as string
                    final_value = value_str
            except ValueError:
                print(f"警告: 为 '{param_title}' 输入的值 '{value_str}' 类型不匹配 ({actual_param_type})，已忽略。")
                use_value = False # Do not use this malformed value

        elif default_value is not None: # No input, but default exists
            final_value = default_value # Default value is already in its correct type from schema
            use_value = True
            print(f"提示: 参数 '{param_title}' 使用默认值: {final_value}")

        elif is_required_by_schema: # No input, no default, but required
            print(f"错误: 参数 '{param_title}' 是必需的，但未提供值。请重新输入所有参数。")
            return await get_params_dynamically(tool_definition) # Restart parameter collection for this tool

        if use_value:
            params[param_name] = final_value

    return params


async def main():
    print(f"交互式MCP客户端启动，目标服务器: {SERVER_URL}")
    print("按 Ctrl+C 退出。")

    try:
        async with streamablehttp_client(SERVER_URL) as (read_stream, write_stream, _):
            print("成功连接到服务器并获取读写流。")

            async with ClientSession(read_stream, write_stream) as session:
                print("ClientSession 已创建。正在初始化会话...")
                await session.initialize()
                print("会话已初始化。")

                result = await session.list_tools()
                if not result:
                    print("错误: 未能从服务器获取工具列表。")
                    return

                #tool_list = list(session.tools.keys())
                tool_list = result.tools

                while True:
                    print("\n--- 可用工具 ---")
                    for i, tool in enumerate(tool_list):
                        print(f"{i + 1}. {tool.name}")
                    print("0. 退出")

                    try:
                        choice_str = input("请选择要测试的工具编号: ").strip()
                        if not choice_str.isdigit():
                            print("无效输入，请输入数字。")
                            continue

                        choice = int(choice_str)

                        if choice == 0:
                            print("正在退出...")
                            break

                        if 1 <= choice <= len(tool_list):
                            selected_tool_name = tool_list[choice - 1].name
                            print(f"\n--- 测试工具: {selected_tool_name} ---")

                            params = {}
                            tool_definition = tool_list[choice - 1]

                            if selected_tool_name == "query_specification_knowledge_base":
                                params = await get_params_for_query_specification_knowledge_base()
                            elif selected_tool_name == "open_specification_files":
                                params = await get_params_for_query_specification_files()
                            elif selected_tool_name == "query_project_files":
                                params = await get_params_two_stage_vector_query()
                            elif selected_tool_name == "write_review_doc":
                                params = await get_params_for_write_review_doc()
                            elif tool_definition:
                                params = await get_params_dynamically(tool_definition)
                            else:
                                print(f"错误: 未找到工具 '{selected_tool_name}' 的定义。")
                                continue

                            print(f"\n正在使用参数调用 '{selected_tool_name}': {params}")
                            call_result = await session.call_tool(selected_tool_name, params)
                            display_tool_result(call_result)

                        else:
                            print("无效的工具编号，请重新选择。")

                    except KeyboardInterrupt:
                        print("\n捕获到 Ctrl+C，返回工具选择...")
                        continue # Go back to tool selection
                    except EOFError:
                        print("\n输入流结束，正在退出客户端...")
                        return # Exit main loop
                    except Exception as e:
                        print(f"\n在工具测试过程中发生意外错误: {e}")
                        # Log full traceback for debugging if needed:
                        # import traceback
                        # traceback.print_exc()
                        print("将返回工具选择菜单。")


            print("ClientSession 已关闭。")
    except ConnectionRefusedError:
        print(f"错误: 无法连接到服务器 {SERVER_URL}。请确保服务器正在运行且路径正确。")
    except Exception as e:
        print(f"发生未知客户端错误: {e}")
        traceback.print_exc()
    finally:
        print("交互式MCP客户端已关闭。")

if __name__ == "__main__":
    print("MCP客户端 (交互式测试工具)") # <--- 更新描述
    print("===================================================================")
    print("运行此脚本前，请确保:")
    print(f"1. server_v4.py 中的MCP服务器正在运行 (例如: uvicorn server_v4:app --reload --port 8888)") # <--- 更新服务器文件名和端口
    print(f"2. 服务器的MCP挂载点与客户端的 SERVER_URL ('{SERVER_URL}') 一致。")
    print("-------------------------------------------------------------------")

    asyncio.run(main())

    print("\n===================================================================")
    print("MCP客户端测试执行完毕。")
