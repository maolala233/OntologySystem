"""
LLM客户端封装
支持vLLM和Ollama两种服务
"""
import json
import asyncio
import logging
from typing import Optional, Dict, Any, List, AsyncIterator
import httpx
from app.core.config import settings
from app.core.exceptions import LLMCallException


# Ontology Schema提取的JSON Schema定义（基于Palantir Ontology核心概念）
ONTOLOGY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "object_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "对象类型名称，使用英文驼峰命名"},
                    "description": {"type": "string", "description": "对象类型描述，必须使用中文，说明该对象代表什么实体或事件"},
                    "primary_key": {"type": "string", "description": "该对象类型的主键字段名，用于唯一标识对象实例"},
                    "properties": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "属性名称，使用snake_case命名风格（如registration_code）"},
                                "description": {"type": "string", "description": "属性描述，必须使用中文"},
                                "data_type": {"type": "string", "enum": ["string", "number", "boolean", "date", "datetime", "array", "object"], "description": "属性数据类型"}
                            },
                            "required": ["name", "description", "data_type"]
                        }
                    }
                },
                "required": ["name", "description", "properties"]
            }
        },
        "link_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "链接类型名称，使用英文驼峰命名"},
                    "description": {"type": "string", "description": "链接类型描述，必须使用中文，说明关系的含义"},
                    "source_object_type": {"type": "string", "description": "链接的源对象类型"},
                    "target_object_type": {"type": "string", "description": "链接的目标对象类型"},
                    "cardinality": {"type": "string", "enum": ["one-to-one", "one-to-many", "many-to-one", "many-to-many"], "description": "关系基数"}
                },
                "required": ["name", "description", "source_object_type", "target_object_type"]
            }
        },
        "action_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "动作类型名称，使用英文驼峰命名"},
                    "description": {"type": "string", "description": "动作描述，必须使用中文"},
                    "target_object_type": {"type": "string", "description": "动作作用的对象类型"},
                    "parameters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "参数名称，使用snake_case命名"},
                                "data_type": {"type": "string", "description": "参数类型"},
                                "required": {"type": "boolean", "description": "是否必需"}
                            },
                            "required": ["name", "data_type"]
                        }
                    }
                },
                "required": ["name", "description", "target_object_type"]
            }
        }
    },
    "required": ["object_types", "link_types"]
}


class LLMClient:
    """
    大语言模型客户端
    自动检测并使用OpenAI兼容格式(vLLM)或Ollama格式
    """

    def __init__(self):
        self.base_url: str = settings.llm.base_url.rstrip("/")
        self.api_key: Optional[str] = settings.llm.api_key
        self.model: str = settings.llm.model
        self.timeout: int = settings.llm.timeout
        self.max_tokens: int = settings.llm.max_tokens
        self.logger = logging.getLogger("graph_injector.llm_client")
        self._is_ollama: Optional[bool] = None

    def _detect_api_type(self) -> str:
        """
        检测API类型 (vLLM/OpenAI兼容 或 Ollama)

        Returns:
            'openai' 或 'ollama'
        """
        if self._is_ollama is not None:
            return "ollama" if self._is_ollama else "openai"

        # 根据URL特征判断
        if "ollama" in self.base_url.lower() or ":11434" in self.base_url:
            self._is_ollama = True
            return "ollama"
        else:
            # 默认使用OpenAI兼容格式(vLLM)
            self._is_ollama = False
            return "openai"

    async def chat(self, messages: List[Dict[str, str]], temperature: float = 0.1,
                   response_format: Optional[Dict[str, Any]] = None, stream: bool = False) -> str:
        """
        发送对话请求

        Args:
            messages: 消息列表，格式为[{"role": "user/system/assistant", "content": "..."}]
            temperature: 温度参数，控制输出随机性
            response_format: 响应格式，如{"type": "json_object"} 或 {"type": "json_schema", "json_schema": {...}}
            stream: 是否使用流式输出（默认False）

        Returns:
            模型回复的文本内容
        """
        api_type = self._detect_api_type()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if api_type == "ollama":
                    return await self._chat_ollama(client, messages, temperature, response_format, stream)
                else:
                    return await self._chat_openai(client, messages, temperature, response_format, stream)
        except LLMCallException:
            raise
        except httpx.TimeoutException:
            raise LLMCallException(f"LLM请求超时(>{self.timeout}s)")
        except Exception as e:
            raise LLMCallException(f"LLM调用异常: {str(e)}")

    async def chat_stream(self, messages: List[Dict[str, str]], temperature: float = 0.1,
                          response_format: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """
        发送对话请求并流式返回响应内容

        Args:
            messages: 消息列表
            temperature: 温度参数
            response_format: 响应格式

        Yields:
            每次生成的文本片段
        """
        api_type = self._detect_api_type()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if api_type == "ollama":
                    async for chunk in self._chat_ollama_stream(client, messages, temperature, response_format):
                        yield chunk
                else:
                    async for chunk in self._chat_openai_stream(client, messages, temperature, response_format):
                        yield chunk
        except LLMCallException:
            raise
        except httpx.TimeoutException:
            raise LLMCallException(f"LLM请求超时(>{self.timeout}s)")
        except Exception as e:
            raise LLMCallException(f"LLM调用异常: {str(e)}")

    async def _chat_openai(self, client: httpx.AsyncClient, messages: List[Dict[str, str]],
                           temperature: float, response_format: Optional[Dict[str, Any]] = None,
                           stream: bool = False) -> str:
        """
        使用OpenAI兼容格式(vLLM/OpenRouter)调用模型
        """
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in self.base_url.lower():
            headers["HTTP-Referer"] = "https://github.com/palantir-ontology-extractor"
            headers["X-Title"] = "Palantir Ontology Extractor"

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        if response_format:
            body["response_format"] = response_format

        if stream:
            # 流式调用
            full_content = []
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=body) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise LLMCallException(f"LLM调用失败: HTTP {response.status_code} - {error_text.decode()}")

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_content.append(content)
                        except json.JSONDecodeError:
                            continue
            return "".join(full_content)
        else:
            # 非流式调用
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )

            if response.status_code != 200:
                raise LLMCallException(f"LLM调用失败: HTTP {response.status_code} - {response.text}")

            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                raise LLMCallException("LLM返回内容为空")

            self.logger.debug(f"LLM回复长度: {len(content)}")
            return content

    async def _chat_openai_stream(self, client: httpx.AsyncClient, messages: List[Dict[str, str]],
                                  temperature: float, response_format: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """
        使用OpenAI兼容格式(vLLM/OpenRouter)流式调用模型
        """
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in self.base_url.lower():
            headers["HTTP-Referer"] = "https://github.com/palantir-ontology-extractor"
            headers["X-Title"] = "Palantir Ontology Extractor"

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if response_format:
            body["response_format"] = response_format

        async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=body) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                raise LLMCallException(f"LLM调用失败: HTTP {response.status_code} - {error_text.decode()}")

            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    async def _chat_ollama(self, client: httpx.AsyncClient, messages: List[Dict[str, str]],
                           temperature: float, response_format: Optional[Dict[str, Any]] = None,
                           stream: bool = False) -> str:
        """
        使用Ollama格式调用模型
        使用Ollama的chat API端点，支持messages格式和JSON Schema
        """
        body = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": self.max_tokens,
            }
        }

        # 如果指定了JSON Schema，添加到Ollama的format参数
        if response_format:
            if response_format.get("type") == "json_schema" and "json_schema" in response_format:
                body["format"] = response_format["json_schema"]
            else:
                body["format"] = "json"

        # 对于thinking模型，确保num_predict足够大
        # thinking过程会消耗大量token，如果num_predict太小会导致content为空
        # qwen3.5等模型的thinking可能消耗数万token，需要大幅增加配额
        model_lower = self.model.lower()
        is_thinking_model = any(kw in model_lower for kw in ["gemma", "qwen3", "granite4", "deepseek-r1"])
        if is_thinking_model:
            body["options"]["num_predict"] = 32768
            body["options"]["num_ctx"] = 32768
            if "qwen3" in model_lower:
                body["think"] = False

        if stream:
            full_content = []
            async with client.stream("POST", f"{self.base_url}/api/chat", json=body) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise LLMCallException(f"Ollama调用失败: HTTP {response.status_code} - {error_text.decode()}")

                async for line in response.aiter_lines():
                    if line.strip():
                        try:
                            chunk = json.loads(line)
                            message = chunk.get("message", {})
                            content = message.get("content", "")
                            if content:
                                full_content.append(content)
                        except json.JSONDecodeError:
                            continue
            result_content = "".join(full_content)
        else:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=body,
            )

            if response.status_code != 200:
                raise LLMCallException(f"Ollama调用失败: HTTP {response.status_code} - {response.text}")

            result = response.json()
            message = result.get("message", {})
            content = message.get("content", "")
            thinking = message.get("thinking", "")
            done_reason = result.get("done_reason", "")
            
            # 处理thinking模型返回空content的情况
            if not content and thinking:
                self.logger.warning(f"Ollama thinking模型返回空content (thinking长度={len(thinking)}, done_reason={done_reason})")
                if done_reason == "length":
                    current_np = body.get("options", {}).get("num_predict", 32768)
                    new_np = max(current_np * 2, len(thinking) + self.max_tokens + 1024)
                    body["options"]["num_predict"] = new_np
                    self.logger.info(f"自动增大num_predict: {current_np} -> {new_np}, 重试中...")
                    retry_response = await client.post(
                        f"{self.base_url}/api/chat",
                        json=body,
                    )
                    if retry_response.status_code == 200:
                        result2 = retry_response.json()
                        msg2 = result2.get("message", {})
                        content2 = msg2.get("content", "")
                        if content2:
                            self.logger.info(f"增大num_predict后成功获取content (长度={len(content2)})")
                            return content2
                    raise LLMCallException(f"Ollama thinking消耗了所有token配额 (thinking长度={len(thinking)})，增大num_predict后仍失败")
                raise LLMCallException(f"Ollama返回content为空 (thinking长度={len(thinking)}, done_reason={done_reason})")
            
            if not content:
                raise LLMCallException("Ollama返回内容为空")

            self.logger.debug(f"Ollama回复长度: {len(content)}, thinking长度: {len(thinking)}")
            result_content = content

        return result_content

    async def _chat_ollama_stream(self, client: httpx.AsyncClient, messages: List[Dict[str, str]],
                                  temperature: float, response_format: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """
        使用Ollama格式流式调用模型
        """
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": self.max_tokens,
            }
        }

        if response_format:
            if response_format.get("type") == "json_schema" and "json_schema" in response_format:
                body["format"] = response_format["json_schema"]
            else:
                body["format"] = "json"

        # 对于thinking模型，确保num_predict足够大
        model_lower = self.model.lower()
        is_thinking_model = any(kw in model_lower for kw in ["gemma", "qwen3", "granite4", "deepseek-r1"])
        if is_thinking_model:
            body["options"]["num_predict"] = 32768
            body["options"]["num_ctx"] = 32768
            if "qwen3" in model_lower:
                body["think"] = False

        async with client.stream("POST", f"{self.base_url}/api/chat", json=body) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                raise LLMCallException(f"Ollama调用失败: HTTP {response.status_code} - {error_text.decode()}")

            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        chunk = json.loads(line)
                        message = chunk.get("message", {})
                        content = message.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    async def extract_json(self, messages: List[Dict[str, str]], temperature: float = 0.1,
                           json_schema: Optional[Dict[str, Any]] = None, stream: bool = False,
                           max_retries: int = 2) -> Dict[str, Any]:
        """
        发送对话请求并解析JSON响应

        Args:
            messages: 消息列表
            temperature: 温度参数
            json_schema: 可选的JSON Schema定义，用于约束LLM输出格式
            stream: 是否使用流式输出（默认False）
            max_retries: 最大重试次数（默认2次）

        Returns:
            解析后的JSON对象
        """
        # 如果提供了JSON Schema，使用json_schema格式；否则使用json_object
        if json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": json_schema
            }
        else:
            response_format = {"type": "json_object"}

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                content = await self.chat(messages, temperature, response_format, stream)

                # 清理内容并提取JSON
                content = content.strip()

                # 尝试从内容中提取JSON（支持各种格式）
                json_str = self._extract_json_string(content)

                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as e:
                    last_error = LLMCallException(f"LLM返回的JSON解析失败: {str(e)}\n原始内容: {content[:500]}")
                    self.logger.warning(f"JSON解析失败 (尝试 {attempt+1}/{max_retries+1}): {str(e)[:100]}")
                    if attempt < max_retries:
                        await asyncio.sleep(1)
                        continue
                    raise last_error
            except LLMCallException as e:
                last_error = e
                self.logger.warning(f"LLM调用失败 (尝试 {attempt+1}/{max_retries+1}): {str(e)[:100]}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                raise last_error

        raise last_error or LLMCallException("JSON提取失败")

    def _extract_json_string(self, content: str) -> str:
        """
        从LLM返回的内容中提取JSON字符串

        Args:
            content: LLM返回的原始文本内容

        Returns:
            提取的JSON字符串
        """
        if not content or not content.strip():
            raise ValueError("返回内容为空")

        content = content.strip()
        
        # 0. 清理控制字符（换行、制表符等在JSON字符串值内的非法字符）
        content = self._sanitize_json_content(content)

        # 1. 尝试从markdown代码块中提取
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                return content[start:end].strip()

        if "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                return content[start:end].strip()

        # 2. 尝试找到JSON对象的起始位置
        json_str = self._find_json_object(content)
        if json_str:
            return json_str

        # 3. 如果都没找到，返回原始内容（让JSON解析报错）
        return content.strip()

    @staticmethod
    def _sanitize_json_content(content: str) -> str:
        """
        清理JSON内容中的非法控制字符

        Args:
            content: 原始JSON字符串

        Returns:
            清理后的JSON字符串
        """
        # 移除JSON字符串值内的非法控制字符（0x00-0x1F，除了允许的\t\n\r）
        # 手动遍历字符并替换，避免使用正则表达式
        result = []
        i = 0
        in_string = False
        while i < len(content):
            char = content[i]
            
            if char == '"' and (i == 0 or content[i-1] != '\\'):
                in_string = not in_string
                result.append(char)
                i += 1
                continue
            
            if in_string:
                # 在字符串内部，替换非法控制字符
                if ord(char) < 0x20:
                    if char == '\n':
                        result.append('\\n')
                    elif char == '\r':
                        result.append('\\r')
                    elif char == '\t':
                        result.append('\\t')
                    else:
                        result.append(' ')
                    i += 1
                    continue
            
            result.append(char)
            i += 1
        
        return ''.join(result)

    @staticmethod
    def _find_json_object(content: str) -> Optional[str]:
        """
        从内容中找到完整的JSON对象

        Args:
            content: 文本内容

        Returns:
            找到的JSON字符串，或None
        """
        brace_count = 0
        bracket_count = 0
        in_string = False
        escape_next = False
        start_idx = -1

        for i, char in enumerate(content):
            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == '{':
                if brace_count == 0 and bracket_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx != -1:
                    return content[start_idx:i + 1]
            elif char == '[':
                bracket_count += 1
            elif char == ']':
                bracket_count -= 1

        # 如果找到了起始但未找到结束，尝试修复截断的JSON
        if start_idx != -1 and brace_count > 0:
            # 尝试修复：关闭未闭合的括号
            truncated = content[start_idx:]
            # 尝试找到最后一个完整的元素
            # 简单策略：在最后一个逗号或冒号处截断，然后关闭所有括号
            fixed = LLMClient._try_fix_truncated_json(truncated, brace_count, bracket_count)
            if fixed:
                return fixed

        return None

    @staticmethod
    def _try_fix_truncated_json(content: str, open_braces: int, open_brackets: int) -> Optional[str]:
        """
        尝试修复截断的JSON字符串

        Args:
            content: 截断的JSON内容
            open_braces: 未闭合的花括号数
            open_brackets: 未闭合的方括号数

        Returns:
            修复后的JSON字符串，或None
        """
        # 找到最后一个完整的值结束位置
        # 策略：从末尾向前找，找到最后一个完整的键值对
        trimmed = content.rstrip()
        
        # 移除末尾的不完整部分
        # 如果末尾是逗号、冒号或不完整的字符串/值，需要回退
        while trimmed and trimmed[-1] in ',:':
            trimmed = trimmed[:-1].rstrip()
        
        # 如果末尾在字符串中间，找到字符串开始位置并移除不完整的键值对
        # 检查是否在字符串中间被截断
        quote_count = 0
        for ch in trimmed:
            if ch == '"':
                quote_count += 1
        
        # 奇数个引号说明有未闭合的字符串
        if quote_count % 2 == 1:
            # 从末尾向前找最后一个引号，移除不完整的键值对
            last_quote = trimmed.rfind('"')
            if last_quote > 0:
                # 找到这个引号前的逗号或冒号
                before = trimmed[:last_quote].rstrip()
                while before and before[-1] in ',:':
                    before = before[:-1].rstrip()
                trimmed = before
        
        # 关闭未闭合的括号
        for _ in range(open_brackets):
            trimmed += ']'
        for _ in range(open_braces):
            trimmed += '}'
        
        # 验证修复后的JSON是否可解析
        try:
            json.loads(trimmed)
            return trimmed
        except json.JSONDecodeError:
            return None


# 单例LLM客户端
llm_client = LLMClient()
