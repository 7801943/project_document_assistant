

#!/usr/bin/env python3
"""
OpenAI兼容API流式接口测试脚本 - 使用OpenAI官方客户端
支持测试各种OpenAI兼容的API端点
"""

import time
import sys
from typing import Iterator
from openai import OpenAI

class StreamingAPITester:
    def __init__(self, base_url: str, api_key: str, model: str ="kimi-thinking-preview"):
        self.model = model
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )

    def test_completion_stream(self, prompt: str, max_tokens: int = 500) -> Iterator[str]:
        """测试流式completion接口"""
        print(f"🤖 模型: {self.model}")
        print(f"📝 提示词: {prompt}")
        print(f"🔢 最大token数: {max_tokens}")
        print("\n" + "="*50)
        print("📡 开始流式响应:")
        print("="*50)

        try:
            # 创建流式completion请求
            stream = self.client.completions.create(
                model=self.model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=0.7,
                stream=True
            )

            print("✅ 连接成功，开始接收流式数据...\n")

            full_response = ""
            chunk_count = 0
            start_time = time.time()

            # 处理流式响应
            for chunk in stream:
                chunk_count += 1

                # 提取文本内容
                if chunk.choices and len(chunk.choices) > 0:
                    choice = chunk.choices[0]

                    if hasattr(choice, 'text') and choice.text:
                        text = choice.text
                        print(text, end='', flush=True)
                        full_response += text
                        yield text

                    # 检查是否完成
                    if hasattr(choice, 'finish_reason') and choice.finish_reason:
                        print(f"\n🎯 完成原因: {choice.finish_reason}")
                        break

            end_time = time.time()
            duration = end_time - start_time

            print(f"\n\n📊 统计信息:")
            print(f"   - 收到数据块: {chunk_count}")
            print(f"   - 总字符数: {len(full_response)}")
            print(f"   - 总字数: {len(full_response.split())}")
            print(f"   - 耗时: {duration:.2f}秒")
            if duration > 0:
                print(f"   - 平均速度: {len(full_response)/duration:.1f}字符/秒")

        except Exception as e:
            print(f"❌ 请求错误: {e}")
            print(f"🔍 错误类型: {type(e).__name__}")

    def test_chat_completion_stream(self, message: str, max_tokens: int = 500) -> Iterator[str]:
        """测试流式chat completion接口"""
        print(f"🤖 模型: {self.model}")
        print(f"💬 消息: {message}")
        print(f"🔢 最大token数: {max_tokens}")
        print("\n" + "="*50)
        print("📡 开始流式响应:")
        print("="*50)

        try:
            # 创建流式chat completion请求
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": message}
                ],
                max_tokens=max_tokens,
                temperature=0.7,
                stream=True
            )

            print("✅ 连接成功，开始接收流式数据...\n")

            full_response = ""
            chunk_count = 0
            start_time = time.time()

            # 处理流式响应
            for chunk in stream:
                chunk_count += 1

                # 提取文本内容
                if chunk.choices and len(chunk.choices) > 0:
                    choice = chunk.choices[0]

                    if hasattr(choice, 'delta') and choice.delta:
                        if hasattr(choice.delta, 'content') and choice.delta.content:
                            content = choice.delta.content
                            print(content, end='', flush=True)
                            full_response += content
                            yield content

                    # 检查是否完成
                    if hasattr(choice, 'finish_reason') and choice.finish_reason:
                        print(f"\n🎯 完成原因: {choice.finish_reason}")
                        break

            end_time = time.time()
            duration = end_time - start_time

            print(f"\n\n📊 统计信息:")
            print(f"   - 收到数据块: {chunk_count}")
            print(f"   - 总字符数: {len(full_response)}")
            print(f"   - 总字数: {len(full_response.split())}")
            print(f"   - 耗时: {duration:.2f}秒")
            if duration > 0:
                print(f"   - 平均速度: {len(full_response)/duration:.1f}字符/秒")

        except Exception as e:
            print(f"❌ 请求错误: {e}")
            print(f"🔍 错误类型: {type(e).__name__}")

    def test_models(self):
        """测试获取可用模型列表"""
        print("🔍 获取可用模型列表...")
        try:
            models = self.client.models.list()
            print("✅ 成功获取模型列表:")
            for model in models.data:
                print(f"   - {model.id}")
        except Exception as e:
            print(f"❌ 获取模型列表失败: {e}")

    def test_connection(self):
        """测试连接是否正常"""
        print("🔗 测试API连接...")
        try:
            # 尝试获取模型列表来测试连接
            models = self.client.models.list()
            print("✅ API连接正常")
            return True
        except Exception as e:
            print(f"❌ API连接失败: {e}")
            return False


def main():
    print("🤖 OpenAI客户端流式接口测试工具")
    print("="*50)

    # 获取用户输入
    try:
        api_key="sk-7GUfyabVxWR9iTurMSCfd3Ln3aFF8DMgmOj8M2N40XLlXvEL"
        base_url="https://api.moonshot.cn/v1"
        model = "kimi-k2-0711-preview"


        # 创建测试器
        tester = StreamingAPITester(base_url, api_key, model)

        # 测试连接
        print(f"\n🔍 测试连接到: {base_url}")
        if not tester.test_connection():
            print("❌ 连接失败，请检查端点和密钥")
            return

        while True:
            print("\n📋 请选择测试类型:")
            print("1. Completion接口 (/v1/completions)")
            print("2. Chat Completion接口 (/v1/chat/completions)")
            print("3. 获取可用模型列表")
            print("4. 退出")

            choice = input("请输入选择 (1-4): ").strip()

            if choice == "1":
                # 测试completion接口
                prompt = input("\n📝 请输入测试提示词: ").strip()
                if not prompt:
                    prompt = "什么是人工智能？请详细解释。"
                    print(f"使用默认提示词: {prompt}")

                max_tokens = input("🔢 请输入最大token数 (默认500): ").strip()
                try:
                    max_tokens = int(max_tokens) if max_tokens else 500
                except ValueError:
                    max_tokens = 500
                    print("使用默认值: 500")

                print(f"\n🚀 开始测试Completion接口...")
                list(tester.test_completion_stream(prompt, max_tokens))

            elif choice == "2":
                # 测试chat completion接口
                message = input("\n💬 请输入测试消息: ").strip()
                if not message:
                    message = "你好，请介绍一下自己。"
                    print(f"使用默认消息: {message}")

                max_tokens = input("🔢 请输入最大token数 (默认500): ").strip()
                try:
                    max_tokens = int(max_tokens) if max_tokens else 500
                except ValueError:
                    max_tokens = 500
                    print("使用默认值: 500")

                print(f"\n🚀 开始测试Chat Completion接口...")
                list(tester.test_chat_completion_stream(message, max_tokens))

            elif choice == "3":
                # 获取模型列表
                print(f"\n🔍 获取模型列表...")
                tester.test_models()

            elif choice == "4":
                print("👋 再见！")
                break

            else:
                print("❌ 无效选择，请重新输入")

    except KeyboardInterrupt:
        print("\n\n⏹️  用户中断测试")
    except Exception as e:
        print(f"\n❌ 程序错误: {e}")


if __name__ == "__main__":
    # 检查是否安装了openai库
    try:
        import openai
        print(f"✅ OpenAI库版本: {openai.__version__}")
    except ImportError:
        print("❌ 未安装OpenAI库，请运行: pip install openai")
        sys.exit(1)

    main()