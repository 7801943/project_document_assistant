from typing import List, Optional, Tuple, Dict, Any # 修正 Tuple 的导入, 添加 Dict, Any
import asyncio
import json
from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState # 新增: 用于检查WebSocket状态
from loguru import logger
import httpx
import re
from config import settings
from core import session
from core import app_state


class SSEWebSocketProxy:
    '''
    SSE to Websocket dify agent api转发代理.
    通过sse协议连接上游ai-agent api,并通过websocket协议向前端转发
    '''
    def __init__(
            self,
            websocket: WebSocket,
            upstream_url: str,
            headers: Dict[str, str],
            username: str,
            session_id: str):

        self.websocket = websocket
        self.http_client = app_state.http_client
        self.payload =""
        self.upstream_url = upstream_url
        self.headers = headers
        self.queue = asyncio.Queue()
        self.stop_event = asyncio.Event()
        self.sse_task: Optional[asyncio.Task] = None
        self.username = username
        self.session_id = session_id
        self.conversation_id =[]


    # 建议的 start 方法
    async def _start(self):
        # 重置状态
        self.stop_event.clear()
        logger.debug(f"SSEWebSocketProxy._start 开始")

        # 创建两个独立的任务
        sse_reader_task = asyncio.create_task(self._stream_from_upstream())
        ws_writer_task = asyncio.create_task(self._forward_to_websocket())

        # self.sse_task 应该指向负责从上游读取的那个任务，以便可以取消它
        self.sse_task = sse_reader_task

        # 使用 asyncio.gather 等待两个任务完成。
        # return_exceptions=True 可以防止一个任务的失败直接中断另一个任务。
        done, pending = await asyncio.wait(
            [sse_reader_task, ws_writer_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # 当一个任务结束后（比如连接断开或 stream 完成），取消另一个挂起的任务
        for task in pending:
            task.cancel()

        logger.debug(f"SSEWebSocketProxy._start: 结束")

    # 在 stop 方法中，取消两个任务会更稳妥
    def _stop(self):
        logger.debug(f"SSEWebSocketProxy._stop stop_event.set()")
        self.stop_event.set()
        if self.sse_task:
            self.sse_task.cancel()
        # 如果 ws_writer_task 也被保存为实例变量，也应该在这里取消。
        # 不过上面的 asyncio.wait 模式已经处理了相互取消，这里保持原样也可以。
        self.sse_task = None

    async def _stream_from_upstream(self):
        buffer = b''
        if self.http_client is None:
            logger.error(f"SSE任务启动失败: http_client 未初始化 (会话: {self.session_id})")
            await self.queue.put(json.dumps({"type": "error", "content": "服务器未就绪，请稍后重试"}))
            await self.queue.put(None)
            # await self.websocket.close(code=1000, reason="服务器未就绪")  # 关闭连接

            return
        #logger.info(f"即将连接上游 SSE: {self.upstream_url}")
        try:
            # timeout很重要，否则http客户端抛出异常退出
            async with self.http_client.stream("POST", self.upstream_url, headers=self.headers, json=self.payload, timeout=settings.DIFY_HTTP_TIMEOUT) as response:
                #
                if response.status_code != 200:
                    error_content = await response.aread()
                    logger.error(f"连接上游SSE失败，状态码: {response.status_code}, 响应: {error_content.decode(errors='ignore')}")
                    await self.queue.put(json.dumps({"type": "error", "content": f"上游服务错误 (状态码: {response.status_code})"}))
                    return

                async for chunk in response.aiter_bytes():
                    if self.stop_event.is_set() or self.websocket.client_state != WebSocketState.CONNECTED:
                        break
                    buffer += chunk
                    while b"\n\n" in buffer:
                        part, buffer = buffer.split(b"\n\n", 1)
                        msg = part.decode("utf-8").strip()
                        await self.queue.put(msg)
        except asyncio.CancelledError:
            logger.info(f"SSE任务被取消：{self.session_id}")
        except httpx.TimeoutException as e:
            logger.error(f"连接上游SSE超时: {e}")
            await self.queue.put(json.dumps({"type": "error", "content": "连接上游服务超时，请稍后重试"}))
        except httpx.RequestError as e:
            logger.error(f"连接上游SSE时发生请求错误: {e}")
            await self.queue.put(json.dumps({"type": "error", "content": f"无法连接到上游服务: {e.__class__.__name__}"}))
        except Exception as e:
            logger.error(f"SSE读取时发生未预料的异常: {e}", exc_info=True)
            await self.queue.put(json.dumps({"type": "error", "content": "代理服务发生内部错误"}))
        finally:
            await self.queue.put(None)  # 结束信号

    async def _forward_to_websocket(self):
        while True:
            msg = await self.queue.get()
            if msg is None:
                break
            if msg.startswith("data:"):
                raw_data = msg[5:].strip()
                if raw_data == "[DONE]":
                    logger.info(f"会话 {self.session_id} 收到 [DONE] 结束标志")
                    break
                try:
                    parsed = json.loads(raw_data)
                except json.JSONDecodeError:
                    parsed = {"type": "raw", "content": raw_data}

                # 工具信息拦截
                # tool_data = self._extract_content_from_sse(msg)
                # if tool_data:
                #     logger.info(f"SSE Intercept (会话: {self.session_id}, 用户: {self.username}): Tool data extracted: {tool_data}")
                await self.websocket.send_json({"type": "chat_event_batch", "payload": [parsed]})

    # --- 聊天转发和工具拦截---
    # def _extract_content_from_sse(self, sse_message_block: str) -> Optional[Dict[str, Any]]:
    #     """
    #     从sse流中获取工具调用信息，与mcp_tools的返回信息相关
    #     """
    #     extracted_info = {}
    #     for line in sse_message_block.strip().split('\n'):
    #         line = line.strip()
    #         if not line.startswith('data:'):
    #             continue
    #         json_str = line[len('data:'):].strip()
    #         if not json_str or json_str == '[DONE]':
    #             continue
    #         try:
    #             data = json.loads(json_str)
    #             event_type = data.get('event')
    #             if event_type == 'agent_thought':
    #                 tool_input_str = data.get('tool_input')
    #                 if tool_input_str:
    #                     try:
    #                         tool_input_obj = json.loads(tool_input_str)
    #                         if isinstance(tool_input_obj, dict) and tool_input_obj:
    #                             dify_tool_key = next(iter(tool_input_obj))
    #                             if dify_tool_key in tool_input_obj and isinstance(tool_input_obj[dify_tool_key], dict):
    #                                 actual_tool_name = tool_input_obj[dify_tool_key].get('tool_name')
    #                                 if actual_tool_name:
    #                                     extracted_info['tool_name'] = actual_tool_name
    #                     except json.JSONDecodeError as e_tool_input:
    #                         logger.warning(f"SSE拦截: 解析 agent_thought 的 tool_input JSON 失败: {e_tool_input}. tool_input: '{tool_input_str}'")
    #                     except Exception as e_tool_input_generic:
    #                         logger.error(f"SSE拦截: 处理 agent_thought 的 tool_input 时发生未知错误: {e_tool_input_generic}. tool_input: '{tool_input_str}'")

    #                 observation_str = data.get('observation')
    #                 if observation_str and isinstance(observation_str, str):
    #                     result_match = re.search(r"《result:(.*?)》", observation_str)
    #                     token_match = re.search(r"《token:(.*?)》", observation_str)
    #                     file_path_match = re.search(r"《file_path:(.*?)》", observation_str)
    #                     directory_match = re.search(r"《directory:(.*?)》", observation_str)
    #                     if result_match: extracted_info['result'] = result_match.group(1)
    #                     if token_match: extracted_info['token'] = token_match.group(1)
    #                     if file_path_match: extracted_info['file_path'] = file_path_match.group(1)
    #                     if directory_match: extracted_info['directory_match'] = directory_match.group(1)

    #                 if 'tool_name' in extracted_info or 'result' in extracted_info or 'token' in extracted_info:
    #                     return extracted_info
    #         except json.JSONDecodeError:
    #             logger.warning(f"SSE拦截: 无法将 data 行解析为 JSON: '{json_str}'")
    #         except Exception as e:
    #             logger.error(f"SSE拦截: 处理 data 行时发生未知错误: {e}. Line: '{line}'")
    #     return None

    async def run(self):
        '''
        运行dify转发服务
        '''
        try:
            while True:
                msg = await self.websocket.receive_json()
                if msg.get("type") == "stop_chat_stream":
                    self._stop()
                    logger.info(f"用户 {self.username} 会话 {self.session_id} 请求停止流任务")
                    await self.websocket.send_json({"type": "stop_request_processed"})
                    logger.info(f"请求停止流任务已发送")
                elif "query" in msg:
                    # 构造dify api的request
                    self.payload = {
                    "query": f"我是用户:{self.username}," + msg.get("query"),
                    # 这是关键：'inputs' 字段必须存在，即使是空对象。
                    "inputs": msg.get("inputs", {}),
                    "user": self.username,
                    "response_mode": "streaming"
                }

                    # （可选但推荐）处理可选参数，如 conversation_id 和 files
                    # 只有当客户端消息中包含这些字段时，才将它们添加到 payload 中
                    if "conversation_id" in msg and msg["conversation_id"]:
                        self.payload["conversation_id"] = msg["conversation_id"]
                        if msg["conversation_id"] not in self.conversation_id:
                            self.conversation_id.append(msg["conversation_id"])
                    else:
                        # 新的对话
                        pass

                    # 启动转发
                    logger.debug(f"启动转发：会话 {self.session_id}, 用户: {self.username}, 对话id{self.payload.get("conversation_id","None")}")
                    asyncio.create_task(self._start())
                else:
                    await self.websocket.send_json({"type": "error", "content": "未知请求类型"})
        except WebSocketDisconnect:
            logger.info(f"WebSocket 会话 {self.session_id} 用户 {self.username} 断开连接")
            self._stop()
        except Exception as e:
            logger.error(f"WebSocket 错误 (会话 {self.session_id}): {e}")
