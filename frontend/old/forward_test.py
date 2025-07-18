import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
import os
from contextlib import asynccontextmanager
import base64
from urllib.parse import quote
import uvicorn

# --- 配置 ---
KKFILEVIEW_BASE_URL = "http://127.0.0.1:8012/kkfileview"

# --- Lifespan 管理器 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient()
    print("应用启动：HTTPX 客户端已创建。")
    yield
    await client.aclose()
    print("应用关闭：HTTPX 客户端已关闭。")

# --- FastAPI 应用 ---
app = FastAPI(
    title="多服务代理",
    description="使用路径前缀代理多个后端服务。",
    version="3.0.0", # 最终正确编码版
    lifespan=lifespan
)

client: httpx.AsyncClient = None

# 辅助端点
@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件未找到")
    return FileResponse(file_path)


# --- 专用于处理初始预览请求的路由 ---
#
# !!!!! 核心修改 !!!!!
# 1. 函数签名中增加了 request: Request，以便我们能访问原始请求头。
# 2. build_request 时增加了 headers=request.headers，将原始请求头转发出去。
#



# --- 专用于处理初始预览请求的路由 ---
@app.get("/kkfileview/onlinePreview")
async def preview_encoder_proxy(file_url: str):
    """
    专门处理初始预览请求。
    1. 接收原始文件链接 (file_url)。
    2. 对其进行 Base64 编码。
    3. 将编码后的结果作为 'url' 参数转发给 kkFileView。
    """
    if not file_url:
        raise HTTPException(status_code=400, detail="缺少 'file_url' 参数。")
    if not client:
        raise HTTPException(status_code=503, detail="服务尚未准备就绪")

    # 1. 对原始 URL 进行 Base64 编码
    base64_encoded_url = base64.b64encode(file_url.encode('utf-8')).decode('utf-8')
    # 2. 对 Base64 字符串进行 URL-encode，以防特殊字符
    final_encoded_url = quote(base64_encoded_url)
    
    # 3. 构建发往 kkFileView 的最终 URL
    kk_url = f"{KKFILEVIEW_BASE_URL}/onlinePreview?url={final_encoded_url}"
    print(f"[预览编码代理] 编码并转发至: {kk_url}")

    try:
        req = client.build_request("GET", kk_url)
        req.headers["host"] = httpx.URL(kk_url).netloc.decode('ascii')
        
        r = await client.send(req, stream=True)
        return StreamingResponse(
            r.aiter_bytes(), status_code=r.status_code, headers=r.headers, background=BackgroundTask(r.aclose)
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"kkFileView 代理请求失败: {e}")

# --- 通用代理路由 (用于静态文件等) ---
@app.api_route("/kkfileview/{full_path:path}")
async def generic_kkfileview_proxy(request: Request, full_path: str):
    """
    通用代理，用于转发 kkFileView 的静态资源请求 (如 JS, CSS, images)。
    这些请求不需要特殊编码，直接透传。
    """
    if not client:
        raise HTTPException(status_code=503, detail="服务尚未准备就绪")

    url = httpx.URL(url=f"{KKFILEVIEW_BASE_URL}/{full_path}", query=str(request.query_params).encode("utf-8"))
    print(f"[通用代理] 转发静态资源: {url}")
    
    try:
        req = client.build_request(
            method=request.method, url=url, headers=request.headers, content=await request.body()
        )
        req.headers["host"] = url.netloc.decode('ascii')
        
        r = await client.send(req, stream=True)
        return StreamingResponse(
            r.aiter_bytes(), status_code=r.status_code, headers=r.headers, background=BackgroundTask(r.aclose)
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"kkFileView 代理请求失败: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host=None, port=8888)
