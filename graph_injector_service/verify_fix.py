"""快速验证修复后的实例抽取"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.file_parser import parse_file
from app.services.instance_service import InstanceBuilder

SCHEMA_PATH = '/home/lenovo/Documents/PythonProject/inject_test/graph_injector_service/data/schemas/ontology_20260520_095615.json'
PDF_DIR = '/home/lenovo/Documents/PythonProject/inject_test/test_file'


async def verify():
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        ontology = json.load(f)
    
    pdf_path = os.path.join(PDF_DIR, '信银理财安盈象固收稳利十四个月封闭式242号理财产品-产品说明书-20260212.pdf')
    text = parse_file(pdf_path)
    test_text = text[:8000]
    
    builder = InstanceBuilder()
    result = await builder.build_instances(
        text_content=test_text,
        ontology=ontology,
        chunk_size=8000,
        overlap_percentage=10,
    )
    
    nodes = result.get('nodes', [])
    edges = result.get('edges', [])
    
    print(f"对象数: {len(nodes)}")
    print(f"链接数: {len(edges)}")
    
    print("\n--- 对象 ---")
    for node in nodes:
        print(f"  [{node['type']}] {node['name']}")
        props = node.get('properties', {})
        for k, v in list(props.items())[:5]:
            print(f"    {k}: {str(v)[:60]}")
    
    print("\n--- 链接 ---")
    for edge in edges:
        print(f"  [{edge['type']}] {edge.get('source_object_type', '')} -> {edge.get('target_object_type', '')}")


if __name__ == "__main__":
    asyncio.run(verify())
