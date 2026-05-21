#!/usr/bin/env python3
"""
1. 更新系统配置：切换到 Ollama (localhost:11434) + qwen3.5:9b
2. 执行本体构建测试：上传两个 PDF，Schema 提取 + 实例提取
确保 disable_think=True 关闭思考模式
"""
import requests
import json
import time
import sys

BASE_URL = "http://localhost:3001"
PDF1 = "/home/lenovo/Documents/PythonProject/OntologySystem/testfile/信银理财安盈象固收稳利十四个月封闭式242号理财产品-产品说明书-20260212.pdf"
PDF2 = "/home/lenovo/Documents/PythonProject/OntologySystem/testfile/信银理财慧盈象固收增利六个月持有期108号理财产品-产品说明书-20260210.pdf"

session = requests.Session()

def step(name):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

def login():
    step("步骤 0/6: 登录管理员账号")
    resp = session.post(f"{BASE_URL}/api/auth/login", data={
        "username": "admin",
        "password": "123456",
    })
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    data = resp.json()
    token = data["access_token"]
    session.headers.update({"Authorization": f"Bearer {token}"})
    print(f"  ✅ 登录成功，用户名: admin")
    return token

def update_system_config():
    step("步骤 1/6: 更新系统配置为 Ollama + qwen3.5:9b + 关闭 think")
    # 获取当前配置
    resp = session.get(f"{BASE_URL}/api/system/config/llm_config")
    current = resp.json().get("value", {}) if resp.status_code == 200 else {}
    print(f"  📋 当前配置:")
    print(f"     - model: {current.get('model', 'N/A')}")
    print(f"     - base_url: {current.get('base_url', 'N/A')}")
    print(f"     - disable_think: {current.get('disable_think', 'N/A')}")
    
    config = {
        "value": {
            "api_key": "",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3.5:9b",
            "chunk_size": 8000,
            "chunk_overlap": 10,
            "request_interval": 2,
            "llm_timeout": 600,
            "streaming_enabled": True,
            "milvus_enabled": False,
            "disable_think": True,
            "neo4j_uri": "bolt://localhost:7687",
            "neo4j_username": "neo4j",
            "neo4j_password": "password",
            "embedding_base_url": "http://localhost:11434/v1",
            "embedding_model": "nomic-embed-text:latest",
            "milvus_host": "127.0.0.1",
            "milvus_port": "19530"
        }
    }
    resp = session.put(f"{BASE_URL}/api/system/config/llm_config", json=config)
    assert resp.status_code == 200, f"更新配置失败: {resp.text}"
    print(f"  ✅ 配置已更新:")
    print(f"     - model: qwen3.5:9b")
    print(f"     - base_url: http://localhost:11434/v1")
    print(f"     - disable_think: True")

def verify_ollama():
    step("步骤 2/6: 验证 Ollama 服务可用")
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=10)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            model_names = [m["name"] for m in models]
            print(f"  ✅ Ollama 服务正常，可用模型: {', '.join(model_names[:5])}")
            if any("qwen3.5" in m for m in model_names):
                print(f"  ✅ qwen3.5 模型已就绪")
            else:
                print(f"  ⚠️  未找到 qwen3.5 模型，可用模型: {', '.join(model_names)}")
        else:
            print(f"  ⚠️  Ollama 返回 {resp.status_code}")
    except Exception as e:
        print(f"  ❌ Ollama 服务不可用: {e}")
        print(f"  💡 请先启动 Ollama: ollama serve")
        sys.exit(1)

def create_project():
    step("步骤 3/6: 创建测试项目")
    resp = session.post(f"{BASE_URL}/api/projects", json={
        "name": "理财本体测试-Ollama",
        "description": "使用 Ollama qwen3.5:9b + disable_think=True 测试"
    })
    assert resp.status_code in [200, 201], f"创建项目失败: {resp.text}"
    data = resp.json()
    pid = data["id"]
    print(f"  ✅ 项目创建成功，ID: {pid}")
    return pid

def upload_and_extract_schema(pid):
    step("步骤 4/6: 上传两个 PDF 并执行 Schema 骨架提取")
    print(f"   文件1: 信银理财安盈象固收稳利十四个月封闭式242号")
    print(f"   文件2: 信银理财慧盈象固收增利六个月持有期108号")
    print(f"  ⚙️  配置: Ollama qwen3.5:9b, chunk_size=8000, overlap=10%, disable_think=True")
    print(f"  ⏱️  预计耗时: 2-5 分钟（Ollama + disable_think 应快速响应）")
    
    with open(PDF1, "rb") as f1, open(PDF2, "rb") as f2:
        files = [
            ("files", ("pdf1.pdf", f1, "application/pdf")),
            ("files", ("pdf2.pdf", f2, "application/pdf")),
        ]
        data = {
            "user_intent": "提取理财产品领域的本体概念：产品类型、风险等级、收益率、投资范围、费用等",
            "chunk_size": "8000",
            "chunk_overlap": "10",
            "request_interval": "2",
            "disable_think": "True",
            "async_mode": "False",
            "save_documents": "True",
        }
        print(f"  🚀 开始 Schema 提取...")
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/projects/{pid}/extract-schema", files=files, data=data, timeout=600)
        elapsed = time.time() - start
        
        assert resp.status_code == 200, f"Schema 提取失败: HTTP {resp.status_code}\n{resp.text}"
        result = resp.json()
        classes = result.get("schema_graph", {}).get("classes", [])
        object_props = result.get("schema_graph", {}).get("object_properties", [])
        nodes = result.get("graph_data", {}).get("nodes", [])
        edges = result.get("graph_data", {}).get("edges", [])
        metadata = result.get("metadata", {})
        text_content = result.get("text_content", "")
        message = result.get("message", "")
        
        print(f"\n  ✅ Schema 提取成功（耗时 {elapsed:.1f}s）")
        print(f"  📊 结果摘要:")
        print(f"     - 消息: {message}")
        print(f"     - 提取类: {len(classes)} 个")
        print(f"     - 提取关系: {len(object_props)} 个")
        print(f"     - 图节点: {len(nodes)} 个")
        print(f"     - 图边: {len(edges)} 条")
        if metadata.get("total_chunks"):
            sc = metadata.get("successful_chunks", 0)
            sr = metadata.get("success_rate", 0) * 100
            print(f"     - 处理分块: {sc}/{metadata['total_chunks']} ({sr:.0f}%)")
        print(f"     - 文本长度: {len(text_content)} 字符")
        
        if classes:
            print(f"\n  📋 提取的类:")
            for c in classes:
                label = c.get("label", "?")
                cid = c.get("id", "?")
                dps = c.get("data_properties", [])
                prop_defs = c.get("property_definitions", [])
                type_tags = [f"{pd.get('name','?')}({pd.get('data_type','?')})" for pd in prop_defs[:3]]
                print(f"     [{cid}] {label} | 属性{len(dps)}个 {'| '.join(type_tags)}")
                if len(prop_defs) > 3:
                    print(f"          ... +{len(prop_defs)-3} more")
        
        if object_props:
            print(f"\n  🔗 提取的关系:")
            for op in object_props:
                line = f"     [{op.get('id','?')}] {op.get('label','?')}: {op.get('domain','?')} -> {op.get('range','?')}"
                if op.get("cardinality"):
                    line += f" ({op['cardinality']})"
                print(line)
                
        if metadata.get("discarded_edges_count"):
            print(f"\n  ⚠️  丢弃的边: {metadata['discarded_edges_count']}")
        
        # ★ 保存 Schema 中间结果到本地文件
        schema_output = {
            "schema_graph": result.get("schema_graph", {}),
            "text_content": text_content,
            "metadata": metadata,
            "message": message,
        }
        schema_file = f"project_{pid}_schema.json"
        with open(schema_file, 'w', encoding='utf-8') as f:
            json.dump(schema_output, f, ensure_ascii=False, indent=2)
        print(f"\n  💾 Schema 已保存到: {schema_file}")
            
        return result

def extract_instances(pid, schema_result):
    step("步骤 5/6: 执行实例提取（两步抽取第二阶段 - 异步模式）")
    schema_graph = schema_result.get("schema_graph", {})
    text_content = schema_result.get("text_content", "")
    
    if len(text_content) > 80000:
        print(f"  ⏭️  文本过长 ({len(text_content)} 字符)，截断为 80000 字符")
        text_content = text_content[:80000]
    
    data = {
        "text_content": text_content,
        "schema_graph": schema_graph,
        "chunk_size": 8000,
        "chunk_overlap": 10,
        "request_interval": 2,
        "disable_think": True,
        "async_mode": False,
    }
    
    print(f"  ⚙️  配置: Ollama qwen3.5:9b, disable_think=True")
    print(f"  🚀 开始实例提取...")
    start = time.time()
    resp = session.post(
        f"{BASE_URL}/api/projects/{pid}/extract-instances",
        data={"request_body": json.dumps(data), "async_mode": "False"},
        timeout=1800,
    )
    elapsed = time.time() - start
    
    assert resp.status_code == 200, f"实例提取失败: HTTP {resp.status_code}\n{resp.text}"
    result = resp.json()
    instances = result.get("instances", [])
    full_nodes = result.get("graph_data", {}).get("nodes", [])
    full_edges = result.get("graph_data", {}).get("edges", [])
    discarded = result.get("discarded_edges_count", 0)
    metadata = result.get("metadata", {})
    message = result.get("message", "")
    
    print(f"\n  ✅ 实例提取成功（耗时 {elapsed:.1f}s）")
    print(f"   结果摘要:")
    print(f"     - 消息: {message}")
    print(f"     - 提取实例: {len(instances)} 个")
    print(f"     - 总节点: {len(full_nodes)} 个")
    print(f"     - 总边: {len(full_edges)} 条")
    print(f"     - 丢弃边: {discarded} 条")
    if metadata.get("total_chunks"):
        sc = metadata.get("successful_chunks", 0)
        print(f"     - 处理分块: {sc}/{metadata['total_chunks']}")
    
    if instances:
        print(f"\n  📋 提取的实例（前10个）:")
        for inst in instances[:10]:
            iid = inst.get("id", "?")
            label = inst.get("label", "?")
            itype = inst.get("type", "?")
            obj_props = inst.get("object_props", {})
            dp = inst.get("data_props", {})
            rels = [f"{k}={v}" for k,v in list(obj_props.items())[:2]]
            props = [f"{k}={v}" for k,v in list(dp.items())[:2]]
            line = f"     [{iid}] ({itype}) {label}"
            if rels:
                line += f" | 关系: {', '.join(rels)}"
            if props:
                line += f" | 属性: {', '.join(props)}"
            print(line)
        if len(instances) > 10:
            print(f"     ... 还有 {len(instances) - 10} 个实例")
    
    # ★ 保存实例提取中间结果到本地文件
    instances_output = {
        "instances": instances,
        "schema_graph": schema_graph,
        "graph_data": result.get("graph_data", {}),
        "discarded_edges_count": discarded,
        "metadata": metadata,
        "message": message,
    }
    instances_file = f"project_{pid}_instances.json"
    with open(instances_file, 'w', encoding='utf-8') as f:
        json.dump(instances_output, f, ensure_ascii=False, indent=2)
    print(f"\n  💾 实例提取结果已保存到: {instances_file}")
    print(f"     - 实例数量: {len(instances)} 个")
    print(f"     - 文件路径: {instances_file}")

def main():
    try:
        login()
        update_system_config()
        verify_ollama()
        pid = create_project()
        schema = upload_and_extract_schema(pid)
        extract_instances(pid, schema)
        print(f"\n{'='*60}")
        print(f"  🎉 所有测试通过！项目 ID: {pid}")
        print(f"  💡 可通过前端访问项目查看完整本体图")
        print(f"{'='*60}")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
