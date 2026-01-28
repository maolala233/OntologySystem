# LLM服务切换指南

## 功能概述
系统支持通过API参数动态切换不同的LLM服务提供商，无需重启服务或修改配置文件。

## 支持的服务商
1. **OpenRouter**
   - Base URL: `https://openrouter.ai/api/v1`
   - 模型: `nex-agi/deepseek-v3.1-nex-n1:free` 等
   - API Key: 从 https://openrouter.ai/keys 获取

2. **vLLM (默认)**
   - Base URL: `http://28.221.28.7:9082/v1`
   - 模型: `DeepSeek-V3`
   - API Key: 从配置获取

3. **Ollama (本地)**
   - Base URL: `http://localhost:11434/v1`
   - 模型: `llama3.1:8b` 等
   - API Key: `ollama`

## API参数说明
在调用 `/api/v1/ontology/generate` 时，可以指定以下参数：

```json
{
  "text_content": "要处理的文本",
  "scenario": "应用场景",
  "rules": [...],
  "api_key": "LLM服务的API密钥",
  "base_url": "LLM服务的基础URL",
  "model": "要使用的模型名称"
}
```

## 使用示例

### 1. 使用OpenRouter
```bash
curl -X POST "http://localhost:3001/api/v1/ontology/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "text_content": "量子技术工作汇报...",
    "scenario": "量子技术工作进展报告本体提取",
    "rules": [
      {
        "cls_name": "量子技术工作",
        "attrs": "工作内容;进展情况;时间范围",
        "rels": "2025年;重大进展;已开展工作"
      }
    ],
    "api_key": "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",
    "base_url": "https://openrouter.ai/api/v1",
    "model": "z-ai/glm-4.5-air:free"
  }'
```

### 2. 使用Ollama (本地)
```bash
curl -X POST "http://localhost:3001/api/v1/ontology/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "text_content": "量子技术工作汇报...",
    "scenario": "量子技术工作进展报告本体提取",
    "rules": [...],
    "api_key": "ollama",
    "base_url": "http://localhost:11434/v1",
    "model": "llama3.1:8b"
  }'
```

### 3. 使用vLLM (默认)
```bash
curl -X POST "http://localhost:3001/api/v1/ontology/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "text_content": "量子技术工作汇报...",
    "scenario": "量子技术工作进展报告本体提取",
    "rules": [...]
    // 不指定api_key, base_url, model时使用默认vLLM配置
  }'
```

## 注意事项
1. 参数优先级：请求参数 > 环境变量 > 默认配置
2. 所有参数都是可选的，如果不提供则使用默认值
3. 确保网络连接允许访问指定的LLM服务
4. API密钥需要有足够的权限访问所选模型

## 代理配置问题
如果遇到 "Unknown scheme for proxy URL" 错误，可能是系统配置了SOCKS代理。解决方法：
1. 临时取消代理：`unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY`
2. 修改系统代理配置以排除本地请求
3. 配置LLM客户端以正确处理SOCKS代理