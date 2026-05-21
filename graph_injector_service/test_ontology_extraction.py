"""
快速测试Ontology Schema和实例抽取（使用少量chunks）
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.file_parser import parse_file
from app.services.schema_service import SchemaBuilder
from app.services.instance_service import InstanceBuilder
from app.core.config import settings


async def test_ontology_extraction():
    """测试Ontology抽取"""
    
    # 1. 解析PDF
    pdf_path = '/home/lenovo/Documents/PythonProject/inject_test/test_file/信银理财安盈象固收稳利十四个月封闭式242号理财产品-产品说明书-20260212.pdf'
    print("=== 步骤1: 解析PDF ===")
    text = parse_file(pdf_path)
    print(f"PDF解析成功，文本长度: {len(text)}")
    
    # 只使用前10000字符进行快速测试
    test_text = text[:10000]
    print(f"使用测试文本长度: {len(test_text)}")
    
    # 2. 提取Ontology Schema
    print("\n=== 步骤2: 提取Ontology Schema ===")
    schema_builder = SchemaBuilder()
    
    try:
        ontology = await schema_builder.build_schema(
            text_content=test_text,
            chunk_size=4000,
            overlap_percentage=10,
        )
        
        schema_path = schema_builder.save_schema(ontology)
        print(f"\nOntology Schema提取成功，已保存: {schema_path}")
        print(f"对象类型数: {len(ontology.get('object_types', []))}")
        print(f"链接类型数: {len(ontology.get('link_types', []))}")
        print(f"动作类型数: {len(ontology.get('action_types', []))}")
        
        print("\n--- 对象类型 ---")
        for ot in ontology.get('object_types', []):
            print(f"  - {ot['name']}: {ot['description'][:50]}...")
            props = ot.get('properties', [])
            if props:
                print(f"    属性: {[p['name'] for p in props]}")
        
        print("\n--- 链接类型 ---")
        for lt in ontology.get('link_types', []):
            print(f"  - {lt['name']}: {lt.get('source_object_type', '')} -> {lt.get('target_object_type', '')}")
            print(f"    描述: {lt.get('description', '')[:50]}...")
            
        # 3. 提取实例
        print("\n=== 步骤3: 提取Ontology实例 ===")
        instance_builder = InstanceBuilder()
        
        instance_result = await instance_builder.build_instances(
            text_content=test_text,
            ontology=ontology,
            chunk_size=5000,
            overlap_percentage=15,
        )
        
        print(f"\n实例提取成功:")
        print(f"  对象数: {len(instance_result['nodes'])}")
        print(f"  链接数: {len(instance_result.get('edges', []))}")
        
        # 显示部分节点
        print("\n--- 前5个对象 ---")
        for node in instance_result['nodes'][:5]:
            print(f"  [{node['type']}] {node.get('name', '')}")
            print(f"    属性: {list(node.get('properties', {}).keys())}")
        
        # 显示部分边
        print("\n--- 前5个链接 ---")
        for edge in instance_result.get('edges', [])[:5]:
            print(f"  {edge['type']}: {edge['source_object_type']} -> {edge['target_object_type']}")
            print(f"    source_location: {edge.get('source_location', '')}")
        
        # 4. 保存结果
        output_path = '/home/lenovo/Documents/PythonProject/inject_test/data/instances/ontology_test_result.json'
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        result = {
            "status": "success",
            "schema_path": schema_path,
            "ontology": ontology,
            "nodes": instance_result['nodes'],
            "edges": instance_result.get('edges', []),
            "metadata": instance_result.get('metadata', {}),
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        print(f"\n结果已保存到: {output_path}")
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_ontology_extraction())
