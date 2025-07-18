# from openai import OpenAI

# client = OpenAI(
#     api_key="AIzaSyCO_s4PIgu8r1NTVbJIH6VC5L4jpe6WIKw",
#     base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
# )

# response = client.chat.completions.create(
#     model="gemini-2.5-flash",
#     messages=[
#         {"role": "system", "content": "You are a helpful assistant."},
#         {
#             "role": "user",
#             "content": "Explain to me how AI works"
#         }
#     ]
# )

# print(response.choices[0].message)

# from docx import Document
# from docx.shared import RGBColor

# # 创建一个文档
# doc = Document()

# # 添加段落
# paragraph = doc.add_paragraph()

# # 添加文字
# run = paragraph.add_run("helloworld")

# # 设置字体颜色为红色
# run.font.color.rgb = RGBColor(255, 0, 0)  # 红色 (R, G, B)

# # 保存文档
# doc.save("helloworld.docx")

# generate_token_and_serve.py

# main.py

# main.py

# from fastapi import FastAPI, Request
# from fastapi.responses import HTMLResponse
# from fastapi.staticfiles import StaticFiles
# import jwt
# import time
# import json
# import uvicorn

# app = FastAPI()

# # 配置
# ONLYOFFICE_SECRET = '6955cd123336471f917122f777e35f227f97d2c7bfa1f54612cbac3d73d11995'
# DOCUMENT_NAME = 'helloworld.docx'
# DOCUMENT_PATH = 'docs'

# # 提供静态文档服务
# app.mount("/docs", StaticFiles(directory=DOCUMENT_PATH), name="docs")

# # 生成 JWT token
# def generate_token():
#     payload = {
#         "document": {
#             "fileType": "docx",
#             "key": "doc-12345",
#             "title": DOCUMENT_NAME,
#             "url": f"http://192.168.43.48:8002/docs/{DOCUMENT_NAME}"
#         },
#         "editorConfig": {
#             "callbackUrl": "http://192.168.43.48:8002/callback",
#             "user": {
#                 "id": "user-1",
#                 "name": "FastAPI User"
#             }
#         },
#         "permissions": {
#             "edit": True,
#             "download": True
#         },
#         "iat": int(time.time())
#     }

#     token = jwt.encode(payload, ONLYOFFICE_SECRET, algorithm="HS256")
#     return token

# # 主页面：嵌入 OnlyOffice 编辑器
# @app.get("/", response_class=HTMLResponse)
# async def open_document():
#     token = generate_token()
#     config = {
#         "document": {
#             "fileType": "docx",
#             "key": "doc-12345",
#             "title": DOCUMENT_NAME,
#             "url": f"http://192.168.43.48:8002/docs/{DOCUMENT_NAME}"
#         },
#         "documentType": "text",
#         "editorConfig": {
#             "callbackUrl": "http://192.168.43.48:8002/callback",
#             "user": {
#                 "id": "user-1",
#                 "name": "FastAPI User"
#             }
#         },
#         "permissions": {
#             "edit": True,
#             "download": True
#         },
#         "token": token
#     }

#     html = f"""
#     <!DOCTYPE html>
#     <html>
#     <head>
#         <title>OnlyOffice 文档预览</title>
#         <meta charset="utf-8">
#         <script type="text/javascript" src="http://192.168.43.48:8080/web-apps/apps/api/documents/api.js"></script>
#         <style>
#             html, body {{
#                 margin: 0;
#                 padding: 0;
#                 height: 100%;
#             }}
#             #placeholder {{
#                 width: 100%;
#                 height: 100%;
#             }}
#         </style>
#     </head>
#     <body>
#         <div id="placeholder"></div>

#         <script type="text/javascript">
#             var config = {json.dumps(config)};
#             var docEditor = new DocsAPI.DocEditor("placeholder", config);
#         </script>
#     </body>
#     </html>
#     """

#     return HTMLResponse(content=html)

# # OnlyOffice 编辑完成后回调（测试用）
# @app.post("/callback")
# async def callback():
#     return {"status": "success"}

# # 自动运行 uvicorn（内置启动）
# if __name__ == "__main__":
#     uvicorn.run("temp:app", host="0.0.0.0", port=8002, reload=True)

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
import jwt
import time
import json
import uvicorn
import os

app = FastAPI()

# 配置
ONLYOFFICE_SECRET = '6955cd123336471f917122f777e35f227f97d2c7bfa1f54612cbac3d73d11995'
DOCUMENT_NAME = 'helloworld.docx'
DOCUMENT_PATH = 'docs'

# 自定义静态文件服务 GET 接口
@app.get("/docs/{filename}", response_class=FileResponse)
async def get_document(filename: str):
    file_path = os.path.join(DOCUMENT_PATH, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=file_path, filename=filename, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

# 生成 JWT token
def generate_token():
    payload = {
        "document": {
            "fileType": "docx",
            "key": "doc-12345",
            "title": DOCUMENT_NAME,
            "url": f"http://192.168.43.48:8002/docs/{DOCUMENT_NAME}"
        },
        "editorConfig": {
            "callbackUrl": "http://192.168.43.48:8002/callback",
            "user": {
                "id": "user-1",
                "name": "FastAPI User"
            }
        },
        "permissions": {
            "edit": True,
            "download": True
        },
        "iat": int(time.time())
    }

    token = jwt.encode(payload, ONLYOFFICE_SECRET, algorithm="HS256")
    return token

# 主页面：嵌入 OnlyOffice 编辑器
@app.get("/", response_class=HTMLResponse)
async def open_document():
    token = generate_token()
    config = {
        "document": {
            "fileType": "docx",
            "key": "doc-12345",
            "title": DOCUMENT_NAME,
            "url": f"http://192.168.43.48:8002/docs/{DOCUMENT_NAME}"
        },
        "documentType": "text",
        "editorConfig": {
            "callbackUrl": "http://192.168.43.48:8002/callback",
            "user": {
                "id": "user-1",
                "name": "FastAPI User"
            }
        },
        "permissions": {
            "edit": True,
            "download": True
        },
        "token": token
    }

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>OnlyOffice 文档预览</title>
        <meta charset="utf-8">
        <script type="text/javascript" src="http://192.168.43.48:8080/web-apps/apps/api/documents/api.js"></script>
        <style>
            html, body {{
                margin: 0;
                padding: 0;
                height: 100%;
            }}
            #placeholder {{
                width: 100%;
                height: 100%;
            }}
        </style>
    </head>
    <body>
        <div id="placeholder"></div>

        <script type="text/javascript">
            var config = {json.dumps(config)};
            var docEditor = new DocsAPI.DocEditor("placeholder", config);
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html)

# OnlyOffice 编辑完成后回调（测试用）
@app.post("/callback")
async def callback():
    return {"status": "success"}

# 自动运行 uvicorn（内置启动）
if __name__ == "__main__":
    uvicorn.run("temp:app", host="0.0.0.0", port=8002, reload=True)
