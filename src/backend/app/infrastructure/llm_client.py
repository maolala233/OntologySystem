# app/infrastructure/llm_client.py - LLM 客户端
# 功能：封装 LLM 调用逻辑，提供统一的接口访问各种语言模型

import re
import json
import time
import os
import threading
from typing import Dict, Any, Optional
from openai import OpenAI
from app.core.config import settings
# 保留这些作为备用配置，但优先使用传入的参数

from app.core.logging import logger
from app.infrastructure.task_manager import TaskCancelledError


class LLMClient:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        """初始化 LLM 客户端"""
        from app.core.config import settings
        from app.core.logging import logger
        
        # 1. 基础参数赋值 - 确保 self.model 最先设置
        self.api_key = api_key if api_key is not None else settings.LLM_API_KEY
        self.model = model if model is not None else settings.LLM_MODEL_NAME
        
        # 2. 地址处理
        raw_url = base_url if base_url is not None else settings.LLM_BASE_URL
        self.base_url = self._clean_base_url(raw_url)
        
        logger.info(f"LLMClient 正在初始化：model={self.model}, base_url={self.base_url}")
        
        # 3. 检查是否为外部 API（需要代理）
        is_external_api = False
        if self.base_url:
            lowercase_url = self.base_url.lower()
            is_external_api = ('openrouter' in lowercase_url or 'api.' in lowercase_url or 
                              ('http' in lowercase_url and 'localhost' not in lowercase_url and 
                               '127.0.0.1' not in lowercase_url and '.lan' not in lowercase_url))
        
        # 4. 准备客户端参数
        client_kwargs = {
            "base_url": self.base_url,
            "api_key": self.api_key if self.api_key else "EMPTY",
            "timeout": 600.0  # 增加到 10 分钟，防止大文件处理超时
        }
        
        # 5. 设置认证头
        headers = {}
        if self.api_key and self.api_key != "EMPTY":
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            headers["Authorization"] = "Bearer EMPTY"
        client_kwargs["default_headers"] = headers
        
        # 6. 代理配置
        if is_external_api:
            try:
                client_kwargs["http_client"] = self._create_proxy_http_client()
                logger.info("LLMClient 已启用代理连接")
            except Exception as e:
                logger.warning(f"代理配置失败，使用默认连接：{e}")
        
        # 7. 最终创建客户端
        try:
            self.client = OpenAI(**client_kwargs)
            logger.info("LLMClient 初始化完成")
        except Exception as e:
            logger.error(f"LLMClient 创建失败：{e}")
            raise
            # 对于外部 API，使用代理
            try:
                client_kwargs["http_client"] = self._create_proxy_http_client()
            except Exception as e:
                from app.core.logging import logger
                logger.warning(f"代理配置失败，使用默认连接：{e}")
                # 移除 http_client 参数以使用默认客户端
                if "http_client" in client_kwargs:
                    del client_kwargs["http_client"]
        # 如果不是外部 API，不设置 http_client，让 OpenAI 使用默认客户端
        
        self.client = OpenAI(**client_kwargs)

    def _create_proxy_http_client(self):
        """创建支持代理的 HTTP 客户端"""
        import httpx
        from urllib.parse import urlparse
        
        # 检查是否为 OpenRouter 服务，如果是则强制使用指定代理
        if self.base_url and 'openrouter' in self.base_url.lower():
            proxy_to_use = "http://127.0.0.1:7890"
        else:
            # 获取环境中的代理设置
            http_proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
            https_proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
            
            proxy_to_use = https_proxy or http_proxy
        
        if proxy_to_use:
            # 检查是否为 SOCKS 代理
            if 'socks' in proxy_to_use.lower():
                try:
                    # type: ignore - httpx_socks 是可选依赖
                    from httpx_socks import SyncProxyTransport  # type: ignore
                    parsed = urlparse(proxy_to_use)
                    if parsed.scheme.startswith('socks'):
                        return httpx.Client(transport=SyncProxyTransport.from_url(proxy_to_use))
                except ImportError:
                    # 如果没有安装 httpx_socks，记录警告
                    from app.core.logging import logger
                    logger.warning("httpx_socks not installed for SOCKS proxy support. Install with: pip install httpx[socks]")
                    # 返回一个没有代理的客户端
                    return httpx.Client()
                except Exception as e:
                    # 处理 URL 格式错误或其他任何与代理相关的问题
                    from app.core.logging import logger
                    logger.warning(f"Failed to create proxy client: {e}. Falling back to no proxy.")
                    return httpx.Client()
            else:
                # 对于 HTTP 代理，使用 httpx 的 proxy 参数
                try:
                    return httpx.Client(proxy=proxy_to_use)
                except ValueError as e:
                    # 捕获代理 URL 格式错误
                    from app.core.logging import logger
                    logger.warning(f"Invalid proxy URL format: {e}. Falling back to no proxy.")
                    return httpx.Client()
                except Exception as e:
                    from app.core.logging import logger
                    logger.warning(f"Failed to create HTTP proxy client: {e}. Falling back to no proxy.")
                    return httpx.Client()
        
        # 如果没有代理设置，返回基本客户端
        return httpx.Client()

    def _clean_base_url(self, url: str) -> str:
        if not url: 
            return ""
        url = url.strip()
        # 移除末尾斜杠
        if url.endswith("/"): 
            url = url[:-1]
        
        # 移除常见的后缀，保留基础 API 路径
        # OpenAI SDK 会自动补全 /chat/completions
        if url.endswith("/chat/completions"): 
            url = url.replace("/chat/completions", "")
        if url.endswith("/completions"):
            url = url.replace("/completions", "")
            
        return url

    def call_llm(self, system_prompt: str, user_prompt: str, max_retries: int = 3, stream: bool = True, timeout: Optional[float] = None, task_id: Optional[str] = None, json_schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        调用 LLM 接口
        
        参数:
        - system_prompt: 系统提示词
        - user_prompt: 用户提示词
        - max_retries: 最大重试次数
        - stream: 是否使用流式响应
        - timeout: 超时时间（秒），如果为 None 则使用客户端默认超时
        - task_id: 任务 ID，用于在流式响应中检查取消状态
        - json_schema: JSON Schema 定义，用于约束输出格式（优先使用）
        """
        from app.core.logging import logger
        from app.infrastructure.task_manager import task_manager
        model_name = getattr(self, 'model', 'unknown-model')
        call_start_time = time.time()
        timeout_str = f"{timeout}s" if timeout else "default"
        logger.info(f"正在发起 LLM 调用：model={model_name}, stream={stream}, timeout={timeout_str}, json_schema={json_schema is not None}")
        logger.info(f"[LLM] 请求内容长度：system_prompt={len(system_prompt)} 字符，user_prompt={len(user_prompt)} 字符")
        if task_id:
            logger.info(f"[LLM] 任务 ID: {task_id}，将在流式响应中检查取消状态")
        
        for attempt in range(max_retries):
            response = None
            try:
                # 准备 API 调用参数
                api_kwargs = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "stream": stream,
                    "max_tokens": 50000,  # 限制 vllm 输出长度，防止无限生成
                    "stop": ["</s>", "\n\n\n"]  # 添加停止 token
                }
                
                # 如果指定了 timeout，添加到参数中
                if timeout is not None:
                    api_kwargs["timeout"] = timeout
                
                # ★ 优先使用 json_schema 参数（如果提供）
                if json_schema:
                    logger.info(f"[LLM] 使用 json_schema 参数约束输出格式")
                    # 使用 response_format 的 json_schema 模式（OpenAI 标准）
                    api_kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "output_schema",
                            "schema": json_schema,
                            "strict": True
                        }
                    }
                else:
                    # 检查 prompt 是否要求 JSON 输出（fallback 方案）
                    requires_json = (
                        "json" in system_prompt.lower() or 
                        "json" in user_prompt.lower() or
                        "JSON" in system_prompt or
                        "JSON" in user_prompt or
                        "返回 json" in system_prompt.lower() or
                        "返回 json" in user_prompt.lower() or
                        "输出 json" in system_prompt.lower() or
                        "输出 json" in user_prompt.lower() or
                        "parse" in system_prompt.lower() and "structure" in system_prompt.lower()
                    )
                    
                    if requires_json:
                        logger.info(f"[LLM] 检测到 JSON 输出要求，使用 json_object 格式")
                        api_kwargs["response_format"] = {"type": "json_object"}
                
                response = self.client.chat.completions.create(**api_kwargs)
                
                if stream:
                    logger.info(f"[LLM] 开始接收流式响应...")
                    stream_start = time.time()
                    full_content = ""
                    chunk_count = 0
                    finish_reason = None
                    stream_timeout = timeout if timeout is not None else 120.0  # 默认 120 秒超时
                    try:
                        for chunk in response:
                            # 检查取消标志
                            if task_id and task_manager.is_cancelled(task_id):
                                logger.info(f"[LLM] 检测到任务取消，中断流式响应 (已接收 {chunk_count} 个 chunk)")
                                # 关闭连接以释放 LLM 服务端显存
                                if hasattr(response, 'close'):
                                    try:
                                        response.close()
                                    except Exception:
                                        pass
                                raise TaskCancelledError(f"Task {task_id} was cancelled during streaming")
                            
                            # 检查超时
                            elapsed_time = time.time() - stream_start
                            if elapsed_time > stream_timeout:
                                logger.error(f"LLM 调用超时（timeout={stream_timeout}s），中断流式响应")
                                # 关闭连接以释放 LLM 服务端显存
                                if hasattr(response, 'close'):
                                    try:
                                        response.close()
                                    except Exception:
                                        pass
                                raise TimeoutError(f"LLM streaming response timeout after {elapsed_time:.2f}s")
                            
                            if chunk.choices and chunk.choices[0].delta:
                                # 捕获 finish_reason
                                if chunk.choices[0].finish_reason:
                                    finish_reason = chunk.choices[0].finish_reason
                                    logger.info(f"[LLM] 流式响应 finish_reason: {finish_reason}")
                                
                                # 只要有 delta 就计数，有内容就追加
                                chunk_count += 1
                                if chunk.choices[0].delta.content:
                                    full_content += chunk.choices[0].delta.content
                                
                                # 每 50 个 chunk 输出一次进度
                                if chunk_count % 50 == 0:
                                    logger.info(f"[LLM] 已接收 {chunk_count} 个 chunk, 当前内容长度={len(full_content)} 字符，耗时={time.time()-stream_start:.2f}s")
                        
                        logger.info(f"[LLM] 流式响应完成，总内容长度={len(full_content)} 字符，总耗时={time.time()-stream_start:.2f}s, finish_reason={finish_reason}")
                        
                        # ★ 自动续写机制：当 finish_reason == 'length' 时，继续生成
                        if finish_reason == 'length':
                            logger.info(f"[LLM] 检测到输出被截断（finish_reason=length），启动自动续写机制...")
                            continued_content = self._continue_generation(
                                system_prompt=system_prompt,
                                user_prompt=user_prompt,
                                partial_content=full_content,
                                timeout=timeout,
                                task_id=task_id,
                                json_schema=json_schema,
                            )
                            full_content += continued_content
                            logger.info(f"[LLM] 自动续写完成，追加内容长度={len(continued_content)} 字符，总长度={len(full_content)} 字符")
                        
                        return self._repair_truncated_json(full_content)
                    except GeneratorExit:
                        # 生成器被关闭，确保关闭连接
                        if response and hasattr(response, 'close'):
                            try:
                                response.close()
                            except Exception:
                                pass
                        raise
                else:
                    # 检查响应是否有效
                    if not response or not response.choices:
                        from app.core.logging import logger
                        logger.warning("LLM 响应为空或无选择项，返回默认结构")
                        return {"classes": [], "instances": []}
                    if len(response.choices) == 0:
                        from app.core.logging import logger
                        logger.warning("LLM 响应中 choices 为空，返回默认结构")
                        return {"classes": [], "instances": []}
                    message_content = response.choices[0].message.content
                    finish_reason = response.choices[0].finish_reason
                    
                    if message_content is None:
                        from app.core.logging import logger
                        logger.warning("LLM 响应内容为空，返回默认结构")
                        return {"classes": [], "instances": []}
                    
                    logger.info(f"[LLM] 非流式响应，内容长度={len(message_content)} 字符，finish_reason={finish_reason}")
                    
                    # ★ 自动续写机制：当 finish_reason == 'length' 时，继续生成
                    if finish_reason == 'length':
                        logger.info(f"[LLM] 检测到输出被截断（finish_reason=length），启动自动续写机制...")
                        continued_content = self._continue_generation(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            partial_content=message_content,
                            timeout=timeout,
                            task_id=task_id,
                            json_schema=json_schema,
                        )
                        message_content += continued_content
                        logger.info(f"[LLM] 自动续写完成，追加内容长度={len(continued_content)} 字符，总长度={len(message_content)} 字符")
                    
                    return self._repair_truncated_json(message_content)
                    
            except TaskCancelledError as e:
                # 任务取消，直接抛出，不重试
                from app.core.logging import logger
                logger.info(f"任务被取消，停止重试：{e}")
                # 确保关闭连接
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                raise
            except TimeoutError as e:
                # 超时错误，直接抛出，不重试
                from app.core.logging import logger
                logger.info(f"LLM 调用超时，停止重试：{e}")
                # 确保关闭连接
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                raise
            except Exception as e:
                # 确保在异常时关闭连接
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                err_msg = str(e).lower()
                if "429" in err_msg or "rate limit" in err_msg:
                    from app.core.logging import logger
                    wait_time = (attempt + 1) * 10  # 第一次报错等 10 秒，第二次 20 秒...
                    logger.warning(f"触发 API 限流，正在进行第 {attempt+1} 次重试，等待 {wait_time} 秒...")
                    time.sleep(wait_time)
                elif attempt < max_retries - 1:
                    from app.core.logging import logger
                    logger.warning(f"LLM 调用失败：{e}，正在重试... (尝试 {attempt+1}/{max_retries}, 已耗时={time.time()-call_start_time:.2f}s)")
                    time.sleep(2)
                else:
                    from app.core.logging import logger
                    total_time = time.time() - call_start_time
                    logger.error(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")
                    raise Exception(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")

    def _repair_truncated_json(self, json_str: str) -> Dict[str, Any]:
        """
        修复可能被截断的 JSON 字符串
        """
        if not json_str: 
            return {"classes": [], "instances": []}
        
        clean = json_str.strip()
        clean = re.sub(r'^```json\s*', '', clean, flags=re.MULTILINE)
        clean = re.sub(r'^```\s*', '', clean, flags=re.MULTILINE)
        clean = re.sub(r'```$', '', clean, flags=re.MULTILINE)
        clean = clean.strip()

        # 检查是否包含明显的非 JSON 内容（比如纯文本回答）
        if clean.lower().startswith(('hello', 'hi ', 'i ', 'the ', 'this ', 'that ', 'yes', 'no', 'ok', 'sure', 'sorry', 'cannot', 'unable')):
            from app.core.logging import logger
            logger.warning(f"检测到非 JSON 内容：{clean[:100]}...，返回默认结构")
            return {'classes': [], 'instances': []}

        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            from app.core.logging import logger
            logger.warning(f"检测到 JSON 解析错误，原始内容：{clean[:200]}..., 正在尝试自动修复...")
            logger.debug(f"JSON 解析错误详情：{e}")
            
            # 尝试更复杂的修复方法
            fix_attempts = 0
            try:
                # 尝试修复完整的 JSON 结构
                fix_attempts += 1
                fixed_json = self._try_fix_json_structure(clean)
                if fixed_json:
                    logger.debug(f"[JSON 修复尝试 {fix_attempts}] 结构修复：{len(fixed_json)} 字符")
                    parsed = json.loads(fixed_json)
                    # 确保必需字段存在
                    if "classes" not in parsed:
                        parsed["classes"] = []
                    if "instances" not in parsed:
                        parsed["instances"] = []
                    logger.info(f"[JSON 修复成功] 方法：结构修复")
                    return parsed
            except Exception as e:
                logger.debug(f"[JSON 修复尝试 {fix_attempts}] 结构修复失败：{e}")
                pass
            
            # 尝试寻找可能的 JSON 部分 - 括号平衡法
            fix_attempts += 1
            json_start = clean.find('{')
            if json_start != -1:
                bracket_count = 0
                for i, char in enumerate(clean[json_start:], json_start):
                    if char == '{':
                        bracket_count += 1
                    elif char == '}':
                        bracket_count -= 1
                        if bracket_count == 0:
                            possible_json = clean[json_start:i+1]
                            try:
                                parsed = json.loads(possible_json)
                                if "classes" not in parsed:
                                    parsed["classes"] = []
                                if "instances" not in parsed:
                                    parsed["instances"] = []
                                logger.info(f"[JSON 修复成功] 方法：括号平衡法，提取 {len(possible_json)} 字符")
                                return parsed
                            except json.JSONDecodeError as je:
                                logger.debug(f"[JSON 修复尝试 {fix_attempts}] 括号平衡法失败：{je}")
                            break
            
            # 尝试截断到最后一个完整对象
            fix_attempts += 1
            last_object_end = clean.rfind("},")
            if last_object_end != -1:
                fixed_json = clean[:last_object_end + 1] + "]}"
                try:
                    parsed = json.loads(fixed_json)
                    if "classes" not in parsed:
                        parsed["classes"] = []
                    if "instances" not in parsed:
                        parsed["instances"] = []
                    logger.info(f"[JSON 修复成功] 方法：截断到最后对象，提取 {len(fixed_json)} 字符")
                    return parsed
                except json.JSONDecodeError as je:
                    logger.debug(f"[JSON 修复尝试 {fix_attempts}] 截断法失败：{je}")
                    pass
            
            # 尝试在最后一个 } 处截断
            fix_attempts += 1
            last_bracket = clean.rfind("}")
            if last_bracket != -1:
                fixed_json = clean[:last_bracket + 1] + "]}"
                try:
                    parsed = json.loads(fixed_json)
                    if "classes" not in parsed:
                        parsed["classes"] = []
                    if "instances" not in parsed:
                        parsed["instances"] = []
                    logger.info(f"[JSON 修复成功] 方法：最后括号截断，提取 {len(fixed_json)} 字符")
                    return parsed
                except json.JSONDecodeError as je:
                    logger.debug(f"[JSON 修复尝试 {fix_attempts}] 最后括号截断失败：{je}")
                    pass
            
            # 新增：尝试使用 json5 或更宽松的解析
            fix_attempts += 1
            try:
                # 尝试移除末尾的逗号等常见问题
                cleaned = re.sub(r',\s*}', '}', clean)
                cleaned = re.sub(r',\s*]', ']', cleaned)
                parsed = json.loads(cleaned)
                if "classes" not in parsed:
                    parsed["classes"] = []
                if "instances" not in parsed:
                    parsed["instances"] = []
                logger.info(f"[JSON 修复成功] 方法：逗号修复，提取 {len(cleaned)} 字符")
                return parsed
            except json.JSONDecodeError as je:
                logger.debug(f"[JSON 修复尝试 {fix_attempts}] 逗号修复失败：{je}")
                pass
            
            logger.error(f"JSON 修复失败 (共尝试 {fix_attempts} 种方法)，返回默认结构，原始内容前 200 字符：{clean[:200]}...")
            return {"classes": [], "instances": []}
    
    def _clean_continued_content(self, content: str) -> str:
        """
        ★ 清理续写内容中的 Markdown 污染和多余引导语。
        
        问题：大模型在续写时，往往控制不住自己，会在开头再次输出 Markdown 的代码块标记！
        例如：continued_content 可能是 "```json\n行",\n  "type": "银行"\n}```"
        如果直接拼接，会变成：`{ "id": "I_001", "label": "中信银```json\n行",...}`
        这会导致 json.loads() 爆炸！
        
        清理步骤：
        1. 去除开头的 ```json 或 ``` 标记
        2. 去除开头的废话引导语（如"好的，以下是继续的内容："）
        3. 去除尾部的 ``` 标记
        """
        # 1. 去除开头的 Markdown 代码块标记（```json 或 ```）
        content = re.sub(r'^```(?:json)?\s*', '', content.lstrip())
        
        # 2. 去除开头的废话引导语（中文或英文）
        # 匹配如："好的，以下是继续的内容："、"Sure, here's the continuation:" 等
        content = re.sub(r'^好的.*?[:：]\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'^[^\{\[\"\'\w]*sure.*?[:：]\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'^[^\{\[\"\'\w]*here.*?is.*?the.*?continuation.*?[:：]\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'^[^\{\[\"\'\w]*continuing.*?from.*?where.*?left.*?off.*?[:：]\s*', '', content, flags=re.IGNORECASE)
        
        # 3. 去除开头的非 JSON 字符（但保留合法的 JSON 起始字符：{ [ " ' 字母 数字 逗号 空格）
        # 这一步会去掉所有不是合法 JSON 起始字符的字符
        content = re.sub(r'^[^\{\[\"\'\w\s]+', '', content)
        
        # 4. 去除尾部的 Markdown 代码块标记
        content = re.sub(r'\s*```$', '', content.rstrip())
        
        return content

    def _continue_generation(
        self,
        system_prompt: str,
        user_prompt: str,
        partial_content: str,
        timeout: Optional[float] = None,
        task_id: Optional[str] = None,
        json_schema: Optional[Dict[str, Any]] = None,
        max_recursion: int = 5,
    ) -> str:
        """
        ★ 自动续写机制：当 LLM 输出被截断（finish_reason == 'length'）时，继续生成剩余内容。
        
        ★ 进阶终极技巧：使用 "Assistant Pre-fill"（角色预填充）
        最高级、最原生、成功率 100% 的方法，不是在 User Prompt 里告诉它"请继续写"，
        而是利用 API 的 messages 数组，把半截 JSON 伪装成大模型自己刚说了一半的话。
        
        原理：
        1. 将已生成的 partial_content 作为 assistant 角色的最后一条消息
        2. 大模型的注意力机制会自然而然地接着预测下一个 Token
        3. 完全不会出现任何多余的 Markdown 标记和废话
        
        参数:
        - system_prompt: 原始系统提示词
        - user_prompt: 原始用户提示词
        - partial_content: 已生成的部分内容
        - timeout: 超时时间
        - task_id: 任务 ID
        - json_schema: JSON Schema（如果原始请求使用了）
        - max_recursion: 最大递归深度，防止无限循环（默认 3 次）
        
        返回:
        - 续写的内容（不包含已生成的部分）
        """
        from app.core.logging import logger
        
        # 递归深度检查
        if max_recursion <= 0:
            logger.warning(f"[ContinueGeneration] 达到最大递归深度，停止续写")
            return ""
        
        logger.info(f"[ContinueGeneration] 开始续写，已生成内容长度={len(partial_content)} 字符，剩余递归次数={max_recursion}")
        
        # ★ 关键优化：使用 Assistant Pre-fill 技术
        # 将已生成的 partial_content 伪装成 assistant 角色的最后一条消息
        # 这样大模型会自然地接着输出，而不会添加多余的 Markdown 标记
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            # ★ 关键：把半截 JSON 作为 assistant 的回复，让模型接着预测
            {"role": "assistant", "content": partial_content}
        ]
        
        logger.info(f"[ContinueGeneration] 使用 Assistant Pre-fill 技术，messages 结构：system -> user -> assistant(partial)")
        
        # 准备 API 参数
        api_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,  # 续写时使用非流式模式，简化处理
            "max_tokens": 50000,
            "stop": ["</s>", "\n\n\n"]
        }
        
        # 如果指定了 timeout
        if timeout is not None:
            api_kwargs["timeout"] = timeout
        
        # 如果原始请求使用了 json_schema，续写时也使用
        if json_schema:
            api_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "output_schema",
                    "schema": json_schema,
                    "strict": True
                }
            }
        else:
            # 检查是否要求 JSON 输出
            requires_json = (
                "json" in system_prompt.lower() or 
                "json" in user_prompt.lower()
            )
            if requires_json:
                api_kwargs["response_format"] = {"type": "json_object"}
        
        try:
            response = self.client.chat.completions.create(**api_kwargs)
            
            if response and response.choices and len(response.choices) > 0:
                continued_content = response.choices[0].message.content or ""
                finish_reason = response.choices[0].finish_reason
                
                logger.info(f"[ContinueGeneration] 续写完成，追加内容长度={len(continued_content)} 字符，finish_reason={finish_reason}")
                
                # ★ 防御性清理：即使使用了 Assistant Pre-fill，仍然清理可能的污染
                continued_content = self._clean_continued_content(continued_content)
                logger.info(f"[ContinueGeneration] 清理后内容长度={len(continued_content)} 字符")
                
                # 如果续写后仍然被截断，递归续写
                if finish_reason == 'length':
                    logger.info(f"[ContinueGeneration] 续写内容仍被截断，进行下一轮续写...")
                    # 将已续写的内容追加到 partial_content
                    new_partial = partial_content + continued_content
                    more_content = self._continue_generation(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        partial_content=new_partial,
                        timeout=timeout,
                        task_id=task_id,
                        json_schema=json_schema,
                        max_recursion=max_recursion - 1,  # 递减递归深度
                    )
                    return continued_content + more_content
                
                return continued_content
            else:
                logger.warning("[ContinueGeneration] 续写响应为空")
                return ""
                
        except Exception as e:
            logger.error(f"[ContinueGeneration] 续写失败：{e}")
            return ""

    def _try_fix_json_structure(self, json_str: str) -> str:
        """
        尝试修复复杂的 JSON 结构
        """
        # 逐字符检查并修复括号平衡
        balance = 0
        result = json_str
        
        # 先尝试修复对象括号
        obj_stack = []
        for i, char in enumerate(result):
            if char == '{':
                obj_stack.append(char)
            elif char == '}':
                if obj_stack:
                    obj_stack.pop()
        
        # 补全缺少的大括号
        while obj_stack:
            result += '}'
            obj_stack.pop()
        
        # 然后修复数组括号
        arr_stack = []
        for i, char in enumerate(result):
            if char == '[':
                arr_stack.append(char)
            elif char == ']':
                if arr_stack:
                    arr_stack.pop()
        
        # 补全缺少的方括号
        while arr_stack:
            result += ']'
            arr_stack.pop()
        
        # 确保 JSON 以合理的结构结尾
        if result.endswith(','):
            result = result.rstrip(',')
        
        return result

    def call_llm_text(self, system_prompt: str, user_prompt: str, max_retries: int = 3, stream: bool = False, timeout: Optional[float] = None, task_id: Optional[str] = None) -> Dict[str, Any]:
        """
        调用 LLM 接口获取文本回答（不要求 JSON 格式）
        
        参数:
        - system_prompt: 系统提示词
        - user_prompt: 用户提示词
        - max_retries: 最大重试次数
        - stream: 是否使用流式响应
        - timeout: 超时时间（秒），如果为 None 则使用客户端默认超时
        - task_id: 任务 ID，用于在流式响应中检查取消状态
        
        返回:
        - {"content": "文本回答内容"}
        """
        from app.core.logging import logger
        from app.infrastructure.task_manager import task_manager
        model_name = getattr(self, 'model', 'unknown-model')
        call_start_time = time.time()
        timeout_str = f"{timeout}s" if timeout else "default"
        logger.info(f"正在发起 LLM 调用（文本模式）：model={model_name}, stream={stream}, timeout={timeout_str}")
        logger.info(f"[LLM] 请求内容长度：system_prompt={len(system_prompt)} 字符，user_prompt={len(user_prompt)} 字符")
        logger.info(f"[LLM] ★ system_prompt 内容:\n{system_prompt}")
        logger.info(f"[LLM] ★ user_prompt 内容:\n{user_prompt}")
        if task_id:
            logger.info(f"[LLM] 任务 ID: {task_id}，将在流式响应中检查取消状态")
        
        for attempt in range(max_retries):
            response = None
            try:
                # 准备 API 调用参数 - 不添加 response_format
                api_kwargs = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "stream": stream,
                    "max_tokens": 50000,  # 限制 vllm 输出长度，防止无限生成
                    "stop": ["</s>", "\n\n\n"]  # 添加停止 token
                }
                
                # 如果指定了 timeout，添加到参数中
                if timeout is not None:
                    api_kwargs["timeout"] = timeout
                
                response = self.client.chat.completions.create(**api_kwargs)
                
                if stream:
                    logger.info(f"[LLM] 开始接收流式响应...")
                    stream_start = time.time()
                    full_content = ""
                    chunk_count = 0
                    stream_timeout = timeout if timeout is not None else 120.0  # 默认 120 秒超时
                    try:
                        for chunk in response:
                            # 检查取消标志
                            if task_id and task_manager.is_cancelled(task_id):
                                logger.info(f"[LLM] 检测到任务取消，中断流式响应 (已接收 {chunk_count} 个 chunk)")
                                # 关闭连接以释放 LLM 服务端显存
                                if hasattr(response, 'close'):
                                    try:
                                        response.close()
                                    except Exception:
                                        pass
                                raise TaskCancelledError(f"Task {task_id} was cancelled during streaming")
                            
                            # 检查超时
                            elapsed_time = time.time() - stream_start
                            if elapsed_time > stream_timeout:
                                logger.error(f"LLM 调用超时（timeout={stream_timeout}s），中断流式响应")
                                # 关闭连接以释放 LLM 服务端显存
                                if hasattr(response, 'close'):
                                    try:
                                        response.close()
                                    except Exception:
                                        pass
                                raise TimeoutError(f"LLM streaming response timeout after {elapsed_time:.2f}s")
                            
                            if chunk.choices and chunk.choices[0].delta.content:
                                full_content += chunk.choices[0].delta.content
                                chunk_count += 1
                                # 每 50 个 chunk 输出一次进度
                                if chunk_count % 50 == 0:
                                    logger.info(f"[LLM] 已接收 {chunk_count} 个 chunk, 当前内容长度={len(full_content)} 字符，耗时={time.time()-stream_start:.2f}s")
                        logger.info(f"[LLM] 流式响应完成，总内容长度={len(full_content)} 字符，总耗时={time.time()-stream_start:.2f}s")
                        return {"content": full_content}
                    except GeneratorExit:
                        # 生成器被关闭，确保关闭连接
                        if response and hasattr(response, 'close'):
                            try:
                                response.close()
                            except Exception:
                                pass
                        raise
                else:
                    # 检查响应是否有效
                    if not response or not response.choices:
                        logger.warning("LLM 响应为空或无选择项，返回空字符串")
                        return {"content": ""}
                    if len(response.choices) == 0:
                        logger.warning("LLM 响应中 choices 为空，返回空字符串")
                        return {"content": ""}
                    message_content = response.choices[0].message.content
                    if message_content is None:
                        logger.warning("LLM 响应内容为空，返回空字符串")
                        return {"content": ""}
                    logger.info(f"[LLM] 非流式响应，内容长度={len(message_content)} 字符")
                    return {"content": message_content}
                    
            except TaskCancelledError as e:
                # 任务取消，直接抛出，不重试
                logger.info(f"任务被取消，停止重试：{e}")
                # 确保关闭连接
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                raise
            except TimeoutError as e:
                # 超时错误，直接抛出，不重试
                logger.info(f"LLM 调用超时，停止重试：{e}")
                # 确保关闭连接
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                raise
            except Exception as e:
                # 确保在异常时关闭连接
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                err_msg = str(e).lower()
                if "429" in err_msg or "rate limit" in err_msg:
                    wait_time = (attempt + 1) * 10  # 第一次报错等 10 秒，第二次 20 秒...
                    logger.warning(f"触发 API 限流，正在进行第 {attempt+1} 次重试，等待 {wait_time} 秒...")
                    time.sleep(wait_time)
                elif attempt < max_retries - 1:
                    logger.warning(f"LLM 调用失败：{e}，正在重试... (尝试 {attempt+1}/{max_retries}, 已耗时={time.time()-call_start_time:.2f}s)")
                    time.sleep(2)
                else:
                    total_time = time.time() - call_start_time
                    logger.error(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")
                    raise Exception(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")
