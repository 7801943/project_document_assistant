import asyncio
import json
import openai
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from loguru import logger
from config import settings
from typing import Dict, Any, Optional, List
from openai.types.chat import ChatCompletionMessageParam
import aiofiles
from pathlib import Path
from fastmcp import Client
from openai.types.chat.chat_completion_tool_param import ChatCompletionToolParam
from openai import NOT_GIVEN


class OpenAIWebSocketProxy:
    """
    通过WebSocket接收前端消息，调用OpenAI兼容的API，
    并将SSE流转换为旧格式转发给前端，同时保存对话历史。
    """
    def __init__(self, websocket: WebSocket, username: str, session_id: str, system_prompt):
        self.websocket = websocket
        self.username = username
        self.session_id = session_id
        self.stop_event = asyncio.Event()
        self.history: List[Dict[str, Any]] = []
        self.conversation_id: Optional[str] = None # 新增：用于存储当前多轮对话的ID
        self.openai_client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY.get_secret_value(),
            base_url=str(settings.OPENAI_API_BASE_URL),
        )
        self.model_name = settings.OPENAI_MODEL_NAME
        self.mcp_server = f"http://127.0.0.1:{settings.SERVER_PORT}{settings.MCP_PATH}mcp"
        self.available_tools = None
        self.system_prompt = system_prompt
        self.tool_call_id = 0

    async def _send_event_to_websocket(self, event: Dict[str, Any]):
        try:
            await self.websocket.send_json({"type": "chat_event_batch", "payload": [event]})
        except Exception as e:
            logger.error(f"WebSocket 发送失败: {e}", exc_info=True)
            self._stop()

    async def _handle_stream_chunk(
        self,
        chunk: Any,
        full_response_content: str,
        tool_call_chunks: dict,
    ) -> str:
        choice = chunk.choices[0]
        delta = choice.delta

        # 普通内容
        if delta.content:
            full_response_content += delta.content
            # dify接口兼容
            await self._send_event_to_websocket({
                "event": "agent_message",
                "answer": delta.content,
                "conversation_id": self.conversation_id, # 使用正确的对话ID
                "task_id": self.session_id, # Bug 2 修复：添加 task_id
            })
        # 工具调用
        if delta.tool_calls:
            for tool_call_chunk in delta.tool_calls:
                index = tool_call_chunk.index
                tool_call_chunks.setdefault(index, {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""}
                })

                if tool_call_chunk.id:
                    tool_call_chunks[index]["id"] = tool_call_chunk.id
                else:
                    self.tool_call_id += 1
                    tool_call_chunks[index]["id"] = str(self.tool_call_id)
                if tool_call_chunk.function:
                    if tool_call_chunk.function.name:
                        tool_call_chunks[index]["function"]["name"] = tool_call_chunk.function.name
                    if tool_call_chunk.function.arguments:
                        tool_call_chunks[index]["function"]["arguments"] += tool_call_chunk.function.arguments

        return full_response_content

    def _stop(self):
        logger.debug(f"OpenAIWebSocketProxy._stop stop_event.set()")
        self.stop_event.set()

    async def _start(self):
        try:
            await self._handle_stream(self.history)
        except asyncio.CancelledError:
            logger.info(f"会话 {self.session_id} 被取消")
        finally:
            await self._send_event_to_websocket({"event": "message_end"})
            # 整个会话退出再保存
            #await self._save_history_to_file()


    async def _handle_stream(
        self,
        messages: List[Dict[str, Any]],
        depth: int = 0,
        max_depth: int = 5,
    ) -> Optional[str]:
        if depth > max_depth:
            logger.warning(f"递归深度超过最大限制({max_depth})，终止工具调用")
            return "[系统错误] 工具调用嵌套太深"

        try:
            stream = await self.openai_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                stream=True,
                temperature=0.7,
                tools=self.available_tools or NOT_GIVEN,
            )
        except Exception as e:
            logger.error(f"调用OpenAI API失败: {e}", exc_info=True)
            await self._send_event_to_websocket({"type": "error", "content": f"上游服务错误: {e.__class__.__name__}"})
            return None

        full_content = ""
        tool_call_chunks = {}

        async for chunk in stream:
            if self.stop_event.is_set() or self.websocket.client_state != WebSocketState.CONNECTED:
                logger.info("停止信号收到，中止流处理")
                return None

            full_content = await self._handle_stream_chunk(chunk, full_content, tool_call_chunks)

            choice = chunk.choices[0]
            if choice.finish_reason == "tool_calls":
                final_tool_calls = [tool_call_chunks[i] for i in sorted(tool_call_chunks)]
                logger.info(f"触发工具调用: {final_tool_calls}")
                self.history.append({
                    "role": "assistant",
                    "content": full_content or None,
                    "tool_calls": final_tool_calls,
                })

                tool_messages = await self._execute_tool_calls(final_tool_calls)
                self.history.extend(tool_messages)

                await self._send_event_to_websocket({
                    "event": "agent_thought",
                    "observation": "\n".join(f"工具结果: {m['content']}" for m in tool_messages),
                    "conversation_id": self.session_id,
                    "task_id": self.session_id,
                })

                return await self._handle_stream(self.history, depth=depth + 1)

            elif choice.finish_reason == "stop":
                self.history.append({"role": "assistant", "content": full_content})
                break

        return full_content

    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        tool_messages = []
        for call in tool_calls:
            tool_name = call.get("function", {}).get("name")
            tool_args_str = call.get("function", {}).get("arguments", "{}")
            tool_call_id = call.get("id" ,"none")

            if not all([tool_name, tool_call_id]):
                logger.warning(f"无效的工具调用，缺少名称或ID: {call}")
                continue

            logger.info(f"执行工具调用: {tool_name} with args: {tool_args_str}")
            

            try:
                tool_args = json.loads(tool_args_str)
                async with Client(self.mcp_server) as client:
                    result = await client.call_tool(tool_name, tool_args)
                tool_output = result.get("text", str(result)) if isinstance(result, dict) else str(result)

            except json.JSONDecodeError:
                logger.warning(f"工具 '{tool_name}' 的参数JSON解析失败: {tool_args_str}")
                tool_output = f"[工具错误] 参数不是有效的JSON: {tool_args_str}"
            except Exception as e:
                logger.error(f"执行工具 '{tool_name}' 时发生异常: {e}", exc_info=True)
                tool_output = f"[工具错误] {e.__class__.__name__}: {e}"

            tool_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_output,
            })

        return tool_messages

    async def _save_history_to_file(self):
        try:
            history_dir = Path(settings.CONVERSATION_ROOT_PATH) / self.username
            history_dir.mkdir(parents=True, exist_ok=True)
            history_file = history_dir / f"{self.session_id}.json"

            async with aiofiles.open(history_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(self.history, ensure_ascii=False, indent=4))
            logger.info(f"会话历史已保存到: {history_file}")
        except Exception as e:
            logger.error(f"保存会话历史失败: {e}", exc_info=True)

    async def run(self):
        try:
            # 先拉工具列表
            async with Client(self.mcp_server) as client:
                tools = await client.list_tools()
                self.available_tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.inputSchema,
                        },
                    } for t in tools
                ]
                logger.debug(f"获取到的工具列表: {[t['function']['name'] for t in self.available_tools]}")

            self.history.append({
                "role": "system",
                "content": self.system_prompt + f"下面是用户:{self.username}提问:\n"
            })

            while True:
                msg = await self.websocket.receive_json()
                # 停止
                if msg.get("type") == "stop_chat_stream":
                    self._stop()
                    if self.chat_task:
                        self.chat_task.cancel()
                        logger.info(f"会话 {self.session_id} 用户 {self.username} 中止响应流任务")
                    await self.websocket.send_json({"type": "stop_request_processed"})
                # Bug 1 后端修复：处理新对话开始事件
                elif msg.get("type") == "start_conversation":
                    self.conversation_id = msg.get("conversation_id")
                    logger.info(f"收到新对话开始事件，对话ID: {self.conversation_id}。清空历史记录。")
                    # 清空历史记录并重新初始化
                    self.history.clear()
                    self.history.append({
                        "role": "system",
                        "content": self.system_prompt + f"下面是用户:{self.username}提问:\n"
                    })
                # 正常请求
                elif "query" in msg:
                    self._stop()  # 停止上一个请求（如果还在跑）
                    self.stop_event.clear()
                    # 从前端消息中获取 conversation_id
                    self.conversation_id = msg.get("conversation_id")
                    if not self.conversation_id:
                        logger.warning(f"前端未提供 conversation_id，将使用 session_id 作为 fallback。Session ID: {self.session_id}")
                        self.conversation_id = self.session_id # Fallback to session_id if not provided

                    # TODO: 在这里根据 self.conversation_id 加载或初始化 self.history
                    # 如果 self.conversation_id 是新的，则 self.history 应该为空
                    # 如果 self.conversation_id 对应一个已存在的会话，则应该从文件加载历史
                    # 目前，每次新查询都会清空历史，这需要后续修改来支持多轮对话的持久化

                    self.history.append({"role": "user", "content": msg["query"]})
                    logger.info(f"启动OpenAI转发：用户 {self.username}, 会话 {self.session_id}, 对话ID: {self.conversation_id}, 查询: {msg['query']}")
                    self.chat_task = asyncio.create_task(self._start())
                else:
                    await self.websocket.send_json({"type": "error", "content": "未知请求类型"})
        except WebSocketDisconnect:
            logger.info(f"WebSocket 会话 {self.session_id} 用户 {self.username} 断开连接")
            self._stop()
        except Exception as e:
            logger.error(f"WebSocket 错误 (会话 {self.session_id}): {e}", exc_info=True)
            self._stop()
