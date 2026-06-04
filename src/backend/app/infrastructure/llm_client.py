import re
import json
import time
import os
import asyncio
from typing import Dict, Any, Optional
from openai import OpenAI, AsyncOpenAI
from app.core.config import settings
from app.core.logging import logger
from app.infrastructure.task_manager import TaskCancelledError


class LLMClient:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        from app.core.config import settings as _settings

        self.api_key = api_key if api_key is not None else _settings.LLM_API_KEY
        self.model = model if model is not None else _settings.LLM_MODEL_NAME

        raw_url = base_url if base_url is not None else _settings.LLM_BASE_URL
        self.base_url = self._clean_base_url(raw_url)

        logger.info(f"LLMClient 正在初始化：model={self.model}, base_url={self.base_url}")

        is_external_api = False
        if self.base_url:
            lowercase_url = self.base_url.lower()
            is_external_api = ('openrouter' in lowercase_url or 'api.' in lowercase_url or
                              ('http' in lowercase_url and 'localhost' not in lowercase_url and
                               '127.0.0.1' not in lowercase_url and '.lan' not in lowercase_url))

        client_kwargs = {
            "base_url": self.base_url,
            "api_key": self.api_key if self.api_key else "EMPTY",
            "timeout": 600.0,
        }

        headers = {}
        if self.api_key and self.api_key != "EMPTY":
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            headers["Authorization"] = "Bearer EMPTY"
        client_kwargs["default_headers"] = headers

        if is_external_api:
            try:
                client_kwargs["http_client"] = self._create_proxy_http_client()
                logger.info("LLMClient 已启用代理连接")
            except Exception as e:
                logger.warning(f"代理配置失败，使用默认连接：{e}")

        try:
            self.client = OpenAI(**client_kwargs)
            logger.info("LLMClient 同步客户端初始化完成")
        except Exception as e:
            logger.error(f"LLMClient 同步客户端创建失败：{e}")
            raise

        async_client_kwargs = dict(client_kwargs)
        if is_external_api:
            try:
                async_client_kwargs["http_client"] = self._create_proxy_async_http_client()
            except Exception as e:
                logger.warning(f"异步代理配置失败，使用默认连接：{e}")
                async_client_kwargs.pop("http_client", None)
        else:
            async_client_kwargs.pop("http_client", None)

        try:
            self.async_client = AsyncOpenAI(**async_client_kwargs)
            logger.info("LLMClient 异步客户端初始化完成")
        except Exception as e:
            logger.error(f"LLMClient 异步客户端创建失败：{e}")
            self.async_client = None

        self.think_mode = _settings.LLM_THINK_MODE
        self._is_ollama = self._detect_ollama()

    def _detect_ollama(self) -> bool:
        if not self.base_url:
            return False
        return ":11434" in self.base_url or "ollama" in self.base_url.lower()

    def _should_disable_think(self) -> bool:
        if self.think_mode == "disabled":
            return True
        if self.think_mode == "enabled":
            return False
        model_lower = self.model.lower()
        thinking_keywords = ["qwen3", "gemma", "granite4", "deepseek-r1", "think"]
        return any(kw in model_lower for kw in thinking_keywords)

    def _create_proxy_http_client(self):
        import httpx
        from urllib.parse import urlparse

        if self.base_url and 'openrouter' in self.base_url.lower():
            proxy_to_use = "http://127.0.0.1:7890"
        else:
            http_proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
            https_proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
            proxy_to_use = https_proxy or http_proxy

        if proxy_to_use:
            if 'socks' in proxy_to_use.lower():
                try:
                    from httpx_socks import SyncProxyTransport
                    parsed = urlparse(proxy_to_use)
                    if parsed.scheme.startswith('socks'):
                        return httpx.Client(transport=SyncProxyTransport.from_url(proxy_to_use))
                except ImportError:
                    logger.warning("httpx_socks not installed for SOCKS proxy support.")
                    return httpx.Client()
                except Exception as e:
                    logger.warning(f"Failed to create proxy client: {e}. Falling back to no proxy.")
                    return httpx.Client()
            else:
                try:
                    return httpx.Client(proxy=proxy_to_use)
                except ValueError as e:
                    logger.warning(f"Invalid proxy URL format: {e}. Falling back to no proxy.")
                    return httpx.Client()
                except Exception as e:
                    logger.warning(f"Failed to create HTTP proxy client: {e}. Falling back to no proxy.")
                    return httpx.Client()

        return httpx.Client()

    def _create_proxy_async_http_client(self):
        import httpx
        from urllib.parse import urlparse

        if self.base_url and 'openrouter' in self.base_url.lower():
            proxy_to_use = "http://127.0.0.1:7890"
        else:
            http_proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
            https_proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
            proxy_to_use = https_proxy or http_proxy

        if proxy_to_use:
            if 'socks' in proxy_to_use.lower():
                try:
                    from httpx_socks import AsyncProxyTransport
                    parsed = urlparse(proxy_to_use)
                    if parsed.scheme.startswith('socks'):
                        return httpx.AsyncClient(transport=AsyncProxyTransport.from_url(proxy_to_use))
                except ImportError:
                    logger.warning("httpx_socks not installed for SOCKS proxy support.")
                    return httpx.AsyncClient()
                except Exception as e:
                    logger.warning(f"Failed to create async proxy client: {e}. Falling back to no proxy.")
                    return httpx.AsyncClient()
            else:
                try:
                    return httpx.AsyncClient(proxy=proxy_to_use)
                except ValueError as e:
                    logger.warning(f"Invalid proxy URL format: {e}. Falling back to no proxy.")
                    return httpx.AsyncClient()
                except Exception as e:
                    logger.warning(f"Failed to create async HTTP proxy client: {e}. Falling back to no proxy.")
                    return httpx.AsyncClient()

        return httpx.AsyncClient()

    def _clean_base_url(self, url: str) -> str:
        if not url:
            return ""
        url = url.strip()
        if url.endswith("/"):
            url = url[:-1]
        if url.endswith("/chat/completions"):
            url = url.replace("/chat/completions", "")
        if url.endswith("/completions"):
            url = url.replace("/completions", "")
        return url

    def _build_api_kwargs(self, system_prompt: str, user_prompt: str, stream: bool = True,
                          timeout: Optional[float] = None, json_schema: Optional[Dict[str, Any]] = None,
                          max_tokens: int = 16000) -> dict:
        api_kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "stream": stream,
            "max_tokens": max_tokens,
            "stop": ["</s>", "\n\n\n"]
        }
        if self._should_disable_think():
            if self._is_ollama:
                api_kwargs["extra_body"] = {"reasoning_effort": "none"}
            else:
                api_kwargs["extra_body"] = {"think": False}

        if timeout is not None:
            api_kwargs["timeout"] = timeout

        if json_schema:
            logger.info(f"[LLM] 使用 json_schema 参数约束输出格式")
            api_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "output_schema",
                    "schema": json_schema,
                    "strict": True
                }
            }
        else:
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

        return api_kwargs

    def call_llm(self, system_prompt: str, user_prompt: str, max_retries: int = 3, stream: bool = True,
                 timeout: Optional[float] = None, task_id: Optional[str] = None,
                 json_schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
                api_kwargs = self._build_api_kwargs(system_prompt, user_prompt, stream, timeout, json_schema)
                response = self.client.chat.completions.create(**api_kwargs)

                if stream:
                    logger.info(f"[LLM] 开始接收流式响应...")
                    stream_start = time.time()
                    full_content = ""
                    chunk_count = 0
                    finish_reason = None
                    stream_timeout = timeout if timeout is not None else 120.0
                    try:
                        for chunk in response:
                            if task_id and task_manager.is_cancelled(task_id):
                                logger.info(f"[LLM] 检测到任务取消，中断流式响应 (已接收 {chunk_count} 个 chunk)")
                                if hasattr(response, 'close'):
                                    try:
                                        response.close()
                                    except Exception:
                                        pass
                                raise TaskCancelledError(f"Task {task_id} was cancelled during streaming")

                            elapsed_time = time.time() - stream_start
                            if elapsed_time > stream_timeout:
                                logger.error(f"LLM 调用超时（timeout={stream_timeout}s），中断流式响应")
                                if hasattr(response, 'close'):
                                    try:
                                        response.close()
                                    except Exception:
                                        pass
                                raise TimeoutError(f"LLM streaming response timeout after {elapsed_time:.2f}s")

                            if chunk.choices and chunk.choices[0].delta:
                                if chunk.choices[0].finish_reason:
                                    finish_reason = chunk.choices[0].finish_reason
                                    logger.info(f"[LLM] 流式响应 finish_reason: {finish_reason}")

                                chunk_count += 1
                                if chunk.choices[0].delta.content:
                                    full_content += chunk.choices[0].delta.content
                                delta = chunk.choices[0].delta
                                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                                    full_content += delta.reasoning_content

                                if chunk_count % 50 == 0:
                                    logger.info(f"[LLM] 已接收 {chunk_count} 个 chunk, 当前内容长度={len(full_content)} 字符，耗时={time.time()-stream_start:.2f}s")

                        logger.info(f"[LLM] 流式响应完成，总内容长度={len(full_content)} 字符，总耗时={time.time()-stream_start:.2f}s, finish_reason={finish_reason}")

                        if finish_reason == 'length':
                            logger.info(f"[LLM] 检测到输出被截断（finish_reason=length），启动自动续写机制...")
                            continued_content = self._continue_generation(
                                system_prompt=system_prompt, user_prompt=user_prompt,
                                partial_content=full_content, timeout=timeout,
                                task_id=task_id, json_schema=json_schema,
                            )
                            full_content += continued_content
                            logger.info(f"[LLM] 自动续写完成，追加内容长度={len(continued_content)} 字符，总长度={len(full_content)} 字符")

                        return self._repair_truncated_json(full_content)
                    except GeneratorExit:
                        if response and hasattr(response, 'close'):
                            try:
                                response.close()
                            except Exception:
                                pass
                        raise
                else:
                    if not response or not response.choices:
                        logger.warning("LLM 响应为空或无选择项，返回默认结构")
                        return {"classes": [], "instances": []}
                    if len(response.choices) == 0:
                        logger.warning("LLM 响应中 choices 为空，返回默认结构")
                        return {"classes": [], "instances": []}
                    message_content = response.choices[0].message.content
                    finish_reason = response.choices[0].finish_reason

                    if message_content is None:
                        logger.warning("LLM 响应内容为空，返回默认结构")
                        return {"classes": [], "instances": []}

                    logger.info(f"[LLM] 非流式响应，内容长度={len(message_content)} 字符，finish_reason={finish_reason}")

                    if finish_reason == 'length':
                        logger.info(f"[LLM] 检测到输出被截断（finish_reason=length），启动自动续写机制...")
                        continued_content = self._continue_generation(
                            system_prompt=system_prompt, user_prompt=user_prompt,
                            partial_content=message_content, timeout=timeout,
                            task_id=task_id, json_schema=json_schema,
                        )
                        message_content += continued_content
                        logger.info(f"[LLM] 自动续写完成，追加内容长度={len(continued_content)} 字符，总长度={len(message_content)} 字符")

                    return self._repair_truncated_json(message_content)

            except TaskCancelledError as e:
                logger.info(f"任务被取消，停止重试：{e}")
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                raise
            except TimeoutError as e:
                logger.info(f"LLM 调用超时，停止重试：{e}")
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                raise
            except Exception as e:
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                err_msg = str(e).lower()
                if "429" in err_msg or "rate limit" in err_msg:
                    wait_time = (attempt + 1) * 10
                    logger.warning(f"触发 API 限流，正在进行第 {attempt+1} 次重试，等待 {wait_time} 秒...")
                    time.sleep(wait_time)
                elif attempt < max_retries - 1:
                    logger.warning(f"LLM 调用失败：{e}，正在重试... (尝试 {attempt+1}/{max_retries}, 已耗时={time.time()-call_start_time:.2f}s)")
                    time.sleep(2)
                else:
                    total_time = time.time() - call_start_time
                    logger.error(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")
                    raise Exception(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")

    async def async_call_llm(self, system_prompt: str, user_prompt: str, max_retries: int = 3,
                             stream: bool = True, timeout: Optional[float] = None,
                             task_id: Optional[str] = None,
                             json_schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from app.infrastructure.task_manager import task_manager
        model_name = getattr(self, 'model', 'unknown-model')
        call_start_time = time.time()
        timeout_str = f"{timeout}s" if timeout else "default"
        logger.info(f"正在发起异步 LLM 调用：model={model_name}, stream={stream}, timeout={timeout_str}, json_schema={json_schema is not None}")
        logger.info(f"[AsyncLLM] 请求内容长度：system_prompt={len(system_prompt)} 字符，user_prompt={len(user_prompt)} 字符")
        if task_id:
            logger.info(f"[AsyncLLM] 任务 ID: {task_id}，将在流式响应中检查取消状态")

        if not self.async_client:
            logger.warning("[AsyncLLM] 异步客户端不可用，回退到同步调用（在线程池中执行）")
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.call_llm(system_prompt, user_prompt, max_retries, stream, timeout, task_id, json_schema)
            )

        for attempt in range(max_retries):
            response = None
            try:
                api_kwargs = self._build_api_kwargs(system_prompt, user_prompt, stream, timeout, json_schema)
                response = await self.async_client.chat.completions.create(**api_kwargs)

                if stream:
                    logger.info(f"[AsyncLLM] 开始接收流式响应...")
                    stream_start = time.time()
                    full_content = ""
                    chunk_count = 0
                    finish_reason = None
                    stream_timeout = timeout if timeout is not None else 120.0
                    try:
                        async for chunk in response:
                            if task_id and task_manager.is_cancelled(task_id):
                                logger.info(f"[AsyncLLM] 检测到任务取消，中断流式响应 (已接收 {chunk_count} 个 chunk)")
                                if hasattr(response, 'aclose'):
                                    try:
                                        await response.aclose()
                                    except Exception:
                                        pass
                                raise TaskCancelledError(f"Task {task_id} was cancelled during streaming")

                            elapsed_time = time.time() - stream_start
                            if elapsed_time > stream_timeout:
                                logger.error(f"[AsyncLLM] 调用超时（timeout={stream_timeout}s），中断流式响应")
                                if hasattr(response, 'aclose'):
                                    try:
                                        await response.aclose()
                                    except Exception:
                                        pass
                                raise TimeoutError(f"LLM streaming response timeout after {elapsed_time:.2f}s")

                            if chunk.choices and chunk.choices[0].delta:
                                if chunk.choices[0].finish_reason:
                                    finish_reason = chunk.choices[0].finish_reason
                                    logger.info(f"[AsyncLLM] 流式响应 finish_reason: {finish_reason}")

                                chunk_count += 1
                                if chunk.choices[0].delta.content:
                                    full_content += chunk.choices[0].delta.content
                                delta = chunk.choices[0].delta
                                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                                    full_content += delta.reasoning_content

                                if chunk_count % 50 == 0:
                                    logger.info(f"[AsyncLLM] 已接收 {chunk_count} 个 chunk, 当前内容长度={len(full_content)} 字符，耗时={time.time()-stream_start:.2f}s")

                        logger.info(f"[AsyncLLM] 流式响应完成，总内容长度={len(full_content)} 字符，总耗时={time.time()-stream_start:.2f}s, finish_reason={finish_reason}")

                        if finish_reason == 'length':
                            logger.info(f"[AsyncLLM] 检测到输出被截断（finish_reason=length），启动自动续写机制...")
                            continued_content = await self._async_continue_generation(
                                system_prompt=system_prompt, user_prompt=user_prompt,
                                partial_content=full_content, timeout=timeout,
                                task_id=task_id, json_schema=json_schema,
                            )
                            full_content += continued_content
                            logger.info(f"[AsyncLLM] 自动续写完成，追加内容长度={len(continued_content)} 字符，总长度={len(full_content)} 字符")

                        return self._repair_truncated_json(full_content)
                    except GeneratorExit:
                        if response and hasattr(response, 'aclose'):
                            try:
                                await response.aclose()
                            except Exception:
                                pass
                        raise
                else:
                    if not response or not response.choices:
                        logger.warning("[AsyncLLM] 响应为空或无选择项，返回默认结构")
                        return {"classes": [], "instances": []}
                    if len(response.choices) == 0:
                        logger.warning("[AsyncLLM] 响应中 choices 为空，返回默认结构")
                        return {"classes": [], "instances": []}
                    message_content = response.choices[0].message.content
                    finish_reason = response.choices[0].finish_reason

                    if message_content is None:
                        logger.warning("[AsyncLLM] 响应内容为空，返回默认结构")
                        return {"classes": [], "instances": []}

                    logger.info(f"[AsyncLLM] 非流式响应，内容长度={len(message_content)} 字符，finish_reason={finish_reason}")

                    if finish_reason == 'length':
                        logger.info(f"[AsyncLLM] 检测到输出被截断（finish_reason=length），启动自动续写机制...")
                        continued_content = await self._async_continue_generation(
                            system_prompt=system_prompt, user_prompt=user_prompt,
                            partial_content=message_content, timeout=timeout,
                            task_id=task_id, json_schema=json_schema,
                        )
                        message_content += continued_content
                        logger.info(f"[AsyncLLM] 自动续写完成，追加内容长度={len(continued_content)} 字符，总长度={len(message_content)} 字符")

                    return self._repair_truncated_json(message_content)

            except TaskCancelledError as e:
                logger.info(f"[AsyncLLM] 任务被取消，停止重试：{e}")
                if response and hasattr(response, 'aclose'):
                    try:
                        await response.aclose()
                    except Exception:
                        pass
                raise
            except TimeoutError as e:
                logger.info(f"[AsyncLLM] 调用超时，停止重试：{e}")
                if response and hasattr(response, 'aclose'):
                    try:
                        await response.aclose()
                    except Exception:
                        pass
                raise
            except Exception as e:
                if response and hasattr(response, 'aclose'):
                    try:
                        await response.aclose()
                    except Exception:
                        pass
                err_msg = str(e).lower()
                if "429" in err_msg or "rate limit" in err_msg:
                    wait_time = (attempt + 1) * 10
                    logger.warning(f"[AsyncLLM] 触发 API 限流，正在进行第 {attempt+1} 次重试，等待 {wait_time} 秒...")
                    await asyncio.sleep(wait_time)
                elif attempt < max_retries - 1:
                    logger.warning(f"[AsyncLLM] 调用失败：{e}，正在重试... (尝试 {attempt+1}/{max_retries}, 已耗时={time.time()-call_start_time:.2f}s)")
                    await asyncio.sleep(2)
                else:
                    total_time = time.time() - call_start_time
                    logger.error(f"[AsyncLLM] 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")
                    raise Exception(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")

    def _repair_truncated_json(self, json_str: str) -> Dict[str, Any]:
        if not json_str:
            return {"classes": [], "instances": [], "object_types": [], "link_types": [], "action_types": [], "links": []}

        clean = json_str.strip()
        clean = re.sub(r'^```json\s*', '', clean, flags=re.MULTILINE)
        clean = re.sub(r'^```\s*', '', clean, flags=re.MULTILINE)
        clean = re.sub(r'```$', '', clean, flags=re.MULTILINE)
        clean = clean.strip()

        if clean.lower().startswith(('hello', 'hi ', 'i ', 'the ', 'this ', 'that ', 'yes', 'no', 'ok', 'sure', 'sorry', 'cannot', 'unable')):
            logger.warning(f"检测到非 JSON 内容：{clean[:100]}...，返回默认结构")
            return {'classes': [], 'instances': [], 'object_types': [], 'link_types': [], 'action_types': [], 'links': []}

        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.warning(f"检测到 JSON 解析错误，原始内容：{clean[:200]}..., 正在尝试自动修复...")
            logger.debug(f"JSON 解析错误详情：{e}")

            fix_attempts = 0
            try:
                fix_attempts += 1
                fixed_json = self._try_fix_json_structure(clean)
                if fixed_json:
                    logger.debug(f"[JSON 修复尝试 {fix_attempts}] 结构修复：{len(fixed_json)} 字符")
                    parsed = json.loads(fixed_json)
                    for key in ["classes", "object_types"]:
                        if key not in parsed:
                            parsed[key] = []
                    for key in ["instances", "link_types", "action_types", "links"]:
                        if key not in parsed:
                            parsed[key] = []
                    logger.info(f"[JSON 修复成功] 方法：结构修复")
                    return parsed
            except Exception as e:
                logger.debug(f"[JSON 修复尝试 {fix_attempts}] 结构修复失败：{e}")

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
                                for key in ["classes", "object_types"]:
                                    if key not in parsed:
                                        parsed[key] = []
                                for key in ["instances", "link_types", "action_types", "links"]:
                                    if key not in parsed:
                                        parsed[key] = []
                                logger.info(f"[JSON 修复成功] 方法：括号平衡法，提取 {len(possible_json)} 字符")
                                return parsed
                            except json.JSONDecodeError as je:
                                logger.debug(f"[JSON 修复尝试 {fix_attempts}] 括号平衡法失败：{je}")
                            break

            fix_attempts += 1
            last_object_end = clean.rfind("},")
            if last_object_end != -1:
                fixed_json = clean[:last_object_end + 1] + "]}"
                try:
                    parsed = json.loads(fixed_json)
                    for key in ["classes", "object_types"]:
                        if key not in parsed:
                            parsed[key] = []
                    for key in ["instances", "link_types", "action_types", "links"]:
                        if key not in parsed:
                            parsed[key] = []
                    logger.info(f"[JSON 修复成功] 方法：截断到最后对象，提取 {len(fixed_json)} 字符")
                    return parsed
                except json.JSONDecodeError as je:
                    logger.debug(f"[JSON 修复尝试 {fix_attempts}] 截断法失败：{je}")

            fix_attempts += 1
            last_bracket = clean.rfind("}")
            if last_bracket != -1:
                fixed_json = clean[:last_bracket + 1] + "]}"
                try:
                    parsed = json.loads(fixed_json)
                    for key in ["classes", "object_types"]:
                        if key not in parsed:
                            parsed[key] = []
                    for key in ["instances", "link_types", "action_types", "links"]:
                        if key not in parsed:
                            parsed[key] = []
                    logger.info(f"[JSON 修复成功] 方法：最后括号截断，提取 {len(fixed_json)} 字符")
                    return parsed
                except json.JSONDecodeError as je:
                    logger.debug(f"[JSON 修复尝试 {fix_attempts}] 最后括号截断失败：{je}")

            fix_attempts += 1
            try:
                cleaned = re.sub(r',\s*}', '}', clean)
                cleaned = re.sub(r',\s*]', ']', cleaned)
                parsed = json.loads(cleaned)
                for key in ["classes", "object_types"]:
                    if key not in parsed:
                        parsed[key] = []
                for key in ["instances", "link_types", "action_types", "links"]:
                    if key not in parsed:
                        parsed[key] = []
                logger.info(f"[JSON 修复成功] 方法：逗号修复，提取 {len(cleaned)} 字符")
                return parsed
            except json.JSONDecodeError as je:
                logger.debug(f"[JSON 修复尝试 {fix_attempts}] 逗号修复失败：{je}")

            logger.error(f"JSON 修复失败 (共尝试 {fix_attempts} 种方法)，返回默认结构，原始内容前 200 字符：{clean[:200]}...")
            return {"classes": [], "instances": [], "object_types": [], "link_types": [], "action_types": [], "links": []}

    def _clean_continued_content(self, content: str) -> str:
        content = re.sub(r'^```(?:json)?\s*', '', content.lstrip())
        content = re.sub(r'^好的.*?[:：]\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'^[^\{\[\"\'\w]*sure.*?[:：]\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'^[^\{\[\"\'\w]*here.*?is.*?the.*?continuation.*?[:：]\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'^[^\{\[\"\'\w]*continuing.*?from.*?where.*?left.*?off.*?[:：]\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'^[^\{\[\"\'\w\s]+', '', content)
        content = re.sub(r'\s*```$', '', content.rstrip())
        return content

    def _continue_generation(self, system_prompt: str, user_prompt: str, partial_content: str,
                             timeout: Optional[float] = None, task_id: Optional[str] = None,
                             json_schema: Optional[Dict[str, Any]] = None, max_recursion: int = 2) -> str:
        if max_recursion <= 0:
            logger.warning(f"[ContinueGeneration] 达到最大递归深度，停止续写")
            return ""

        # 如果已生成内容超过30000字符，不再续写，防止无限膨胀
        if len(partial_content) > 30000:
            logger.warning(f"[ContinueGeneration] 已生成内容过长({len(partial_content)}字符)，停止续写")
            return ""

        logger.info(f"[ContinueGeneration] 开始续写，已生成内容长度={len(partial_content)} 字符，剩余递归次数={max_recursion}")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": partial_content}
        ]

        api_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
            "max_tokens": 8000,
            "stop": ["</s>", "\n\n\n"]
        }

        if timeout is not None:
            api_kwargs["timeout"] = timeout

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
            requires_json = "json" in system_prompt.lower() or "json" in user_prompt.lower()
            if requires_json:
                api_kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self.client.chat.completions.create(**api_kwargs)

            if response and response.choices and len(response.choices) > 0:
                continued_content = response.choices[0].message.content or ""
                finish_reason = response.choices[0].finish_reason

                logger.info(f"[ContinueGeneration] 续写完成，追加内容长度={len(continued_content)} 字符，finish_reason={finish_reason}")

                continued_content = self._clean_continued_content(continued_content)
                logger.info(f"[ContinueGeneration] 清理后内容长度={len(continued_content)} 字符")

                if finish_reason == 'length':
                    logger.info(f"[ContinueGeneration] 续写内容仍被截断，进行下一轮续写...")
                    new_partial = partial_content + continued_content
                    more_content = self._continue_generation(
                        system_prompt=system_prompt, user_prompt=user_prompt,
                        partial_content=new_partial, timeout=timeout,
                        task_id=task_id, json_schema=json_schema,
                        max_recursion=max_recursion - 1,
                    )
                    return continued_content + more_content

                return continued_content
            else:
                logger.warning("[ContinueGeneration] 续写响应为空")
                return ""

        except Exception as e:
            logger.error(f"[ContinueGeneration] 续写失败：{e}")
            return ""

    async def _async_continue_generation(self, system_prompt: str, user_prompt: str, partial_content: str,
                                          timeout: Optional[float] = None, task_id: Optional[str] = None,
                                          json_schema: Optional[Dict[str, Any]] = None, max_recursion: int = 2) -> str:
        if max_recursion <= 0:
            logger.warning(f"[AsyncContinueGeneration] 达到最大递归深度，停止续写")
            return ""

        # 如果已生成内容超过30000字符，不再续写，防止无限膨胀
        if len(partial_content) > 30000:
            logger.warning(f"[AsyncContinueGeneration] 已生成内容过长({len(partial_content)}字符)，停止续写")
            return ""

        logger.info(f"[AsyncContinueGeneration] 开始续写，已生成内容长度={len(partial_content)} 字符，剩余递归次数={max_recursion}")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": partial_content}
        ]

        api_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
            "max_tokens": 8000,
            "stop": ["</s>", "\n\n\n"]
        }

        if timeout is not None:
            api_kwargs["timeout"] = timeout

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
            requires_json = "json" in system_prompt.lower() or "json" in user_prompt.lower()
            if requires_json:
                api_kwargs["response_format"] = {"type": "json_object"}

        try:
            if not self.async_client:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._continue_generation(
                        system_prompt, user_prompt, partial_content, timeout, task_id, json_schema, max_recursion
                    )
                )
                return result

            response = await self.async_client.chat.completions.create(**api_kwargs)

            if response and response.choices and len(response.choices) > 0:
                continued_content = response.choices[0].message.content or ""
                finish_reason = response.choices[0].finish_reason

                logger.info(f"[AsyncContinueGeneration] 续写完成，追加内容长度={len(continued_content)} 字符，finish_reason={finish_reason}")

                continued_content = self._clean_continued_content(continued_content)
                logger.info(f"[AsyncContinueGeneration] 清理后内容长度={len(continued_content)} 字符")

                if finish_reason == 'length':
                    logger.info(f"[AsyncContinueGeneration] 续写内容仍被截断，进行下一轮续写...")
                    new_partial = partial_content + continued_content
                    more_content = await self._async_continue_generation(
                        system_prompt=system_prompt, user_prompt=user_prompt,
                        partial_content=new_partial, timeout=timeout,
                        task_id=task_id, json_schema=json_schema,
                        max_recursion=max_recursion - 1,
                    )
                    return continued_content + more_content

                return continued_content
            else:
                logger.warning("[AsyncContinueGeneration] 续写响应为空")
                return ""

        except Exception as e:
            logger.error(f"[AsyncContinueGeneration] 续写失败：{e}")
            return ""

    def _try_fix_json_structure(self, json_str: str) -> str:
        balance = 0
        result = json_str

        obj_stack = []
        for i, char in enumerate(result):
            if char == '{':
                obj_stack.append(char)
            elif char == '}':
                if obj_stack:
                    obj_stack.pop()

        while obj_stack:
            result += '}'
            obj_stack.pop()

        arr_stack = []
        for i, char in enumerate(result):
            if char == '[':
                arr_stack.append(char)
            elif char == ']':
                if arr_stack:
                    arr_stack.pop()

        while arr_stack:
            result += ']'
            arr_stack.pop()

        if result.endswith(','):
            result = result.rstrip(',')

        return result

    def call_llm_text(self, system_prompt: str, user_prompt: str, max_retries: int = 3, stream: bool = False,
                      timeout: Optional[float] = None, task_id: Optional[str] = None) -> Dict[str, Any]:
        from app.infrastructure.task_manager import task_manager
        model_name = getattr(self, 'model', 'unknown-model')
        call_start_time = time.time()
        timeout_str = f"{timeout}s" if timeout else "default"
        logger.info(f"正在发起 LLM 调用（文本模式）：model={model_name}, stream={stream}, timeout={timeout_str}")
        logger.info(f"[LLM] 请求内容长度：system_prompt={len(system_prompt)} 字符，user_prompt={len(user_prompt)} 字符")
        if task_id:
            logger.info(f"[LLM] 任务 ID: {task_id}，将在流式响应中检查取消状态")

        for attempt in range(max_retries):
            response = None
            try:
                api_kwargs = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "stream": stream,
                    "max_tokens": 16000,
                    "stop": ["</s>", "\n\n\n"]
                }

                if timeout is not None:
                    api_kwargs["timeout"] = timeout

                response = self.client.chat.completions.create(**api_kwargs)

                if stream:
                    logger.info(f"[LLM] 开始接收流式响应...")
                    stream_start = time.time()
                    full_content = ""
                    chunk_count = 0
                    stream_timeout = timeout if timeout is not None else 120.0
                    try:
                        for chunk in response:
                            if task_id and task_manager.is_cancelled(task_id):
                                logger.info(f"[LLM] 检测到任务取消，中断流式响应 (已接收 {chunk_count} 个 chunk)")
                                if hasattr(response, 'close'):
                                    try:
                                        response.close()
                                    except Exception:
                                        pass
                                raise TaskCancelledError(f"Task {task_id} was cancelled during streaming")

                            elapsed_time = time.time() - stream_start
                            if elapsed_time > stream_timeout:
                                logger.error(f"LLM 调用超时（timeout={stream_timeout}s），中断流式响应")
                                if hasattr(response, 'close'):
                                    try:
                                        response.close()
                                    except Exception:
                                        pass
                                raise TimeoutError(f"LLM streaming response timeout after {elapsed_time:.2f}s")

                            if chunk.choices and chunk.choices[0].delta.content:
                                full_content += chunk.choices[0].delta.content
                                chunk_count += 1
                                if chunk_count % 50 == 0:
                                    logger.info(f"[LLM] 已接收 {chunk_count} 个 chunk, 当前内容长度={len(full_content)} 字符，耗时={time.time()-stream_start:.2f}s")
                        logger.info(f"[LLM] 流式响应完成，总内容长度={len(full_content)} 字符，总耗时={time.time()-stream_start:.2f}s")
                        return {"content": full_content}
                    except GeneratorExit:
                        if response and hasattr(response, 'close'):
                            try:
                                response.close()
                            except Exception:
                                pass
                        raise
                else:
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
                logger.info(f"任务被取消，停止重试：{e}")
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                raise
            except TimeoutError as e:
                logger.info(f"LLM 调用超时，停止重试：{e}")
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                raise
            except Exception as e:
                if response and hasattr(response, 'close'):
                    try:
                        response.close()
                    except Exception:
                        pass
                err_msg = str(e).lower()
                if "429" in err_msg or "rate limit" in err_msg:
                    wait_time = (attempt + 1) * 10
                    logger.warning(f"触发 API 限流，正在进行第 {attempt+1} 次重试，等待 {wait_time} 秒...")
                    time.sleep(wait_time)
                elif attempt < max_retries - 1:
                    logger.warning(f"LLM 调用失败：{e}，正在重试... (尝试 {attempt+1}/{max_retries}, 已耗时={time.time()-call_start_time:.2f}s)")
                    time.sleep(2)
                else:
                    total_time = time.time() - call_start_time
                    logger.error(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")
                    raise Exception(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")

    async def async_call_llm_text(self, system_prompt: str, user_prompt: str, max_retries: int = 3,
                                  stream: bool = False, timeout: Optional[float] = None,
                                  task_id: Optional[str] = None) -> Dict[str, Any]:
        from app.infrastructure.task_manager import task_manager
        model_name = getattr(self, 'model', 'unknown-model')
        call_start_time = time.time()
        timeout_str = f"{timeout}s" if timeout else "default"
        logger.info(f"正在发起异步 LLM 调用（文本模式）：model={model_name}, stream={stream}, timeout={timeout_str}")

        if not self.async_client:
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.call_llm_text(system_prompt, user_prompt, max_retries, stream, timeout, task_id)
            )

        for attempt in range(max_retries):
            response = None
            try:
                api_kwargs = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "stream": stream,
                    "max_tokens": 16000,
                    "stop": ["</s>", "\n\n\n"]
                }

                if timeout is not None:
                    api_kwargs["timeout"] = timeout

                response = await self.async_client.chat.completions.create(**api_kwargs)

                if stream:
                    stream_start = time.time()
                    full_content = ""
                    chunk_count = 0
                    stream_timeout = timeout if timeout is not None else 120.0
                    async for chunk in response:
                        if task_id and task_manager.is_cancelled(task_id):
                            if hasattr(response, 'aclose'):
                                try:
                                    await response.aclose()
                                except Exception:
                                    pass
                            raise TaskCancelledError(f"Task {task_id} was cancelled during streaming")

                        elapsed_time = time.time() - stream_start
                        if elapsed_time > stream_timeout:
                            if hasattr(response, 'aclose'):
                                try:
                                    await response.aclose()
                                except Exception:
                                    pass
                            raise TimeoutError(f"LLM streaming response timeout after {elapsed_time:.2f}s")

                        if chunk.choices and chunk.choices[0].delta.content:
                            full_content += chunk.choices[0].delta.content
                            chunk_count += 1
                            if chunk_count % 50 == 0:
                                logger.info(f"[AsyncLLM] 已接收 {chunk_count} 个 chunk, 当前内容长度={len(full_content)} 字符，耗时={time.time()-stream_start:.2f}s")

                    logger.info(f"[AsyncLLM] 流式响应完成，总内容长度={len(full_content)} 字符，总耗时={time.time()-stream_start:.2f}s")
                    return {"content": full_content}
                else:
                    if not response or not response.choices:
                        return {"content": ""}
                    if len(response.choices) == 0:
                        return {"content": ""}
                    message_content = response.choices[0].message.content
                    if message_content is None:
                        return {"content": ""}
                    logger.info(f"[AsyncLLM] 非流式响应，内容长度={len(message_content)} 字符")
                    return {"content": message_content}

            except TaskCancelledError as e:
                logger.info(f"[AsyncLLM] 任务被取消，停止重试：{e}")
                if response and hasattr(response, 'aclose'):
                    try:
                        await response.aclose()
                    except Exception:
                        pass
                raise
            except TimeoutError as e:
                logger.info(f"[AsyncLLM] 调用超时，停止重试：{e}")
                if response and hasattr(response, 'aclose'):
                    try:
                        await response.aclose()
                    except Exception:
                        pass
                raise
            except Exception as e:
                if response and hasattr(response, 'aclose'):
                    try:
                        await response.aclose()
                    except Exception:
                        pass
                err_msg = str(e).lower()
                if "429" in err_msg or "rate limit" in err_msg:
                    wait_time = (attempt + 1) * 10
                    logger.warning(f"[AsyncLLM] 触发 API 限流，等待 {wait_time} 秒...")
                    await asyncio.sleep(wait_time)
                elif attempt < max_retries - 1:
                    logger.warning(f"[AsyncLLM] 调用失败：{e}，正在重试... (尝试 {attempt+1}/{max_retries})")
                    await asyncio.sleep(2)
                else:
                    total_time = time.time() - call_start_time
                    logger.error(f"[AsyncLLM] 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")
                    raise Exception(f"LLM 调用在 {max_retries} 次尝试后仍然失败，总耗时={total_time:.2f}s: {str(e)}")
