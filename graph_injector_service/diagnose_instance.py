"""快速诊断：测试LLM实例抽取返回了什么"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.file_parser import parse_file
from app.services.schema_service import SchemaBuilder
from app.services.instance_service import InstanceBuilder, ONTOLOGY_INSTANCE_JSON_SCHEMA
from app.infrastructure.llm_client import llm_client

PDF_DIR = '/home/lenovo/Documents/PythonProject/inject_test/test_file'
SCHEMA_PATH = '/home/lenovo/Documents/PythonProject/inject_test/graph_injector_service/data/schemas/ontology_20260520_095615.json'


async def diagnose():
    # 加载已有Schema
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        ontology = json.load(f)
    
    print("=== Schema信息 ===")
    print(f"对象类型: {[ot['name'] for ot in ontology.get('object_types', [])]}")
    print(f"链接类型: {[lt['name'] for lt in ontology.get('link_types', [])]}")
    
    # 解析第一个PDF的前2000字符
    pdf_path = os.path.join(PDF_DIR, '信银理财安盈象固收稳利十四个月封闭式242号理财产品-产品说明书-20260212.pdf')
    text = parse_file(pdf_path)
    test_text = text[:3000]
    
    print(f"\n=== 测试文本(前500字) ===")
    print(test_text[:500])
    
    # 手动构建ontology描述
    builder = InstanceBuilder()
    object_types = ontology.get('object_types', [])
    link_types = ontology.get('link_types', [])
    ontology_description = builder._build_ontology_description(object_types, link_types)
    
    print(f"\n=== Ontology描述 ===")
    print(ontology_description[:800])
    
    # 直接调用LLM看返回
    system_prompt = builder.SYSTEM_PROMPT.format(ontology_description=ontology_description)
    user_prompt = f"请从以下文档中提取Ontology对象实例和链接实例:\n\n{test_text}\n\n输出JSON格式(objects和links两个列表)。"
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    
    print(f"\n=== 直接调用LLM ===")
    print("等待LLM响应...")
    
    result = await llm_client.extract_json(messages, temperature=0.1, json_schema=ONTOLOGY_INSTANCE_JSON_SCHEMA, max_retries=1)
    
    print(f"\n=== LLM返回结果 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
    
    # 分析结果
    objects = result.get('objects', [])
    links = result.get('links', [])
    
    print(f"\n=== 诊断分析 ===")
    print(f"对象数: {len(objects)}")
    print(f"链接数: {len(links)}")
    
    valid_object_type_names = {ot['name'] for ot in object_types}
    print(f"\n有效对象类型名: {valid_object_type_names}")
    
    for i, obj in enumerate(objects):
        obj_type = obj.get('object_type', obj.get('type', ''))
        props = obj.get('properties', {})
        name = props.get('name', '')
        type_valid = obj_type in valid_object_type_names
        has_name = bool(name)
        print(f"\n  对象[{i}]: type={obj_type}, name={name[:50] if name else 'MISSING'}")
        print(f"    类型有效: {type_valid}, 有name: {has_name}")
        if not type_valid:
            print(f"    ⚠️ 类型'{obj_type}'不在有效类型列表中!")
        if not has_name:
            print(f"    ⚠️ properties中缺少name字段! properties keys: {list(props.keys())}")
    
    for i, link in enumerate(links):
        link_type = link.get('link_type', link.get('type', ''))
        source_name = link.get('source_object_name', '')
        target_name = link.get('target_object_name', '')
        print(f"\n  链接[{i}]: type={link_type}, source={source_name[:30]}, target={target_name[:30]}")


if __name__ == "__main__":
    asyncio.run(diagnose())
