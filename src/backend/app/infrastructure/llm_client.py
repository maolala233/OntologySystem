# app/infrastructure/llm_client.py - LLM客户端
# 功能：封装LLM调用逻辑，提供统一的接口访问各种语言模型

import re
import json
import time
import os
from typing import Dict, Any, Optional
from openai import OpenAI
from app.core.config import settings
VLLM_BASE_URL = settings.VLLM_BASE_URL
VLLM_API_KEY = settings.VLLM_API_KEY
VLLM_MODEL = settings.VLLM_MODEL
from app.core.logging import logger


class LLMClient:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or VLLM_API_KEY
        self.base_url = self._clean_base_url(base_url or VLLM_BASE_URL)
        self.model = model or VLLM_MODEL
        
        # 检查是否为外部API（需要代理）
        is_external_api = self.base_url and ('openrouter' in self.base_url.lower() or 'api.' in self.base_url.lower() or 
                                             'http' in self.base_url and 'localhost' not in self.base_url and 
                                             '127.0.0.1' not in self.base_url and '.lan' not in self.base_url)
        
        # 准备客户端参数
        client_kwargs = {
            "base_url": self.base_url,
            "api_key": self.api_key if self.api_key else "EMPTY",
            "timeout": 60.0
        }
        
        # 设置默认headers
        if self.api_key:
            client_kwargs["default_headers"] = {"apikey": self.api_key}
        
        if is_external_api:
            # 对于外部API，使用代理
            try:
                client_kwargs["http_client"] = self._create_proxy_http_client()
            except Exception as e:
                logger.warning(f"代理配置失败，使用默认连接: {e}")
                # 移除http_client参数以使用默认客户端
                if "http_client" in client_kwargs:
                    del client_kwargs["http_client"]
        # 如果不是外部API，不设置http_client，让OpenAI使用默认客户端
        
        self.client = OpenAI(**client_kwargs)

    def _create_proxy_http_client(self):
        """创建支持代理的HTTP客户端"""
        import httpx
        from urllib.parse import urlparse
        
        # 检查是否为OpenRouter服务，如果是则强制使用指定代理
        if self.base_url and 'openrouter' in self.base_url.lower():
            proxy_to_use = "http://127.0.0.1:7890"
        else:
            # 获取环境中的代理设置
            http_proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
            https_proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
            
            proxy_to_use = https_proxy or http_proxy
        
        if proxy_to_use:
            # 检查是否为SOCKS代理
            if 'socks' in proxy_to_use.lower():
                try:
                    from httpx_socks import SyncProxyTransport
                    parsed = urlparse(proxy_to_use)
                    if parsed.scheme.startswith('socks'):
                        return httpx.Client(transport=SyncProxyTransport.from_url(proxy_to_use))
                except ImportError:
                    # 如果没有安装httpx_socks，记录警告
                    logger.warning("httpx_socks not installed for SOCKS proxy support. Install with: pip install httpx[socks]")
                    # 返回一个没有代理的客户端
                    return httpx.Client()
                except Exception as e:
                    # 处理URL格式错误或其他任何与代理相关的问题
                    logger.warning(f"Failed to create proxy client: {e}. Falling back to no proxy.")
                    return httpx.Client()
            else:
                # 对于HTTP代理，使用httpx的proxy参数
                try:
                    return httpx.Client(proxy=proxy_to_use)
                except ValueError as e:
                    # 捕获代理URL格式错误
                    logger.warning(f"Invalid proxy URL format: {e}. Falling back to no proxy.")
                    return httpx.Client()
                except Exception as e:
                    logger.warning(f"Failed to create HTTP proxy client: {e}. Falling back to no proxy.")
                    return httpx.Client()
        
        # 如果没有代理设置，返回基本客户端
        return httpx.Client()

    def _clean_base_url(self, url: str) -> str:
        if not url: 
            return ""
        url = url.strip()
        if url.endswith("/"): 
            url = url[:-1]
        if url.endswith("/chat/completions"): 
            url = url.replace("/chat/completions", "")
        return url

    def call_llm(self, system_prompt: str, user_prompt: str, max_retries: int = 3, stream: bool = True) -> Dict[str, Any]:
        """
        调用 LLM 接口
        """
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    stream=stream
                )
                
                if stream:
                    full_content = ""
                    for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            full_content += chunk.choices[0].delta.content
                    return self._repair_truncated_json(full_content)
                else:
                    # 检查响应是否有效
                    if not response or not response.choices:
                        logger.warning("LLM响应为空或无选择项，返回默认结构")
                        return {"classes": [], "instances": []}
                    if len(response.choices) == 0:
                        logger.warning("LLM响应中choices为空，返回默认结构")
                        return {"classes": [], "instances": []}
                    message_content = response.choices[0].message.content
                    if message_content is None:
                        logger.warning("LLM响应内容为空，返回默认结构")
                        return {"classes": [], "instances": []}
                    return self._repair_truncated_json(message_content)
                    
            except Exception as e:
                err_msg = str(e).lower()
                if "429" in err_msg or "rate limit" in err_msg:
                    wait_time = (attempt + 1) * 10  # 第一次报错等10秒，第二次20秒...
                    logger.warning(f"触发 API 限流，正在进行第 {attempt+1} 次重试，等待 {wait_time} 秒...")
                    time.sleep(wait_time)
                elif attempt < max_retries - 1:
                    logger.warning(f"LLM 调用失败: {e}，正在重试...")
                    time.sleep(2)
                else:
                    logger.error(f"LLM 调用在 {max_retries} 次尝试后仍然失败: {str(e)}")
                    raise Exception(f"LLM 调用在 {max_retries} 次尝试后仍然失败: {str(e)}")

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

        # 检查是否包含明显的非JSON内容（比如纯文本回答）
        if clean.lower().startswith(('hello', 'hi ', 'i ', 'the ', 'this ', 'that ', 'yes', 'no', 'ok', 'sure', 'sorry', 'cannot', 'unable')):
            logger.warning(f"检测到非JSON内容: {clean[:100]}...，返回默认结构")
            return {'classes': [], 'instances': []}

        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            logger.warning(f"检测到 JSON 解析错误，原始内容: {clean[:200]}..., 正在尝试自动修复...")
            
            # 尝试更复杂的修复方法
            try:
                # 尝试修复完整的JSON结构
                fixed_json = self._try_fix_json_structure(clean)
                if fixed_json:
                    parsed = json.loads(fixed_json)
                    # 确保必需字段存在
                    if "classes" not in parsed:
                        parsed["classes"] = []
                    if "instances" not in parsed:
                        parsed["instances"] = []
                    return parsed
            except Exception as e:
                logger.debug(f"高级JSON修复失败: {e}")
                pass
            
            # 尝试寻找可能的JSON部分
            # 寻找JSON对象起始位置
            json_start = clean.find('{')
            if json_start != -1:
                # 尝试找到匹配的结束位置
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
                                return parsed
                            except:
                                pass
                            break
            
            # 原有的简单修复方法
            last_object_end = clean.rfind("},")
            if last_object_end != -1:
                fixed_json = clean[:last_object_end + 1] + "]}"
                try:
                    parsed = json.loads(fixed_json)
                    if "classes" not in parsed:
                        parsed["classes"] = []
                    if "instances" not in parsed:
                        parsed["instances"] = []
                    return parsed
                except:
                    pass
            
            last_bracket = clean.rfind("}")
            if last_bracket != -1:
                fixed_json = clean[:last_bracket + 1] + "]}"
                try:
                    parsed = json.loads(fixed_json)
                    if "classes" not in parsed:
                        parsed["classes"] = []
                    if "instances" not in parsed:
                        parsed["instances"] = []
                    return parsed
                except:
                    pass
            
            logger.error(f"JSON 修复失败，返回默认结构，内容: {clean[:100]}...")
            return {"classes": [], "instances": []}
    
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
        
        # 确保JSON以合理的结构结尾
        if result.endswith(','):
            result = result.rstrip(',')
        
        return result