"""
合并抽取Ontology Schema和实例（3个PDF文档）
策略：3个PDF各取代表性文本 → 合并提取统一Schema → 基于统一Schema分别提取各PDF全文实例 → 生成报告
关键优化：
1. Schema合并时语义去重（Product/FinancialProduct等视为同一类型）
2. 实例合并时区分不同来源的属性值（同一对象在不同PDF中的属性值不同时保留来源信息）
3. 支持Action实例提取
"""
import asyncio
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.file_parser import parse_file
from app.services.schema_service import SchemaBuilder
from app.services.instance_service import InstanceBuilder

PDF_DIR = '/home/lenovo/Documents/PythonProject/inject_test/test_file'
OUTPUT_DIR = '/home/lenovo/Documents/PythonProject/inject_test/data/instances'

PDF_FILES = [
    '信银理财安盈象固收稳利十四个月封闭式242号理财产品-产品说明书-20260212.pdf',
    '信银理财慧盈象固收增利六个月持有期108号理财产品-产品说明书-20260210.pdf',
    '信银理财日盈象天天利55号现金管理型理财产品-产品说明书-20260205.pdf',
]

SCHEMA_SAMPLE_SIZE = 8000


async def main():
    start_time = datetime.now()
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    print(f"{'='*80}")
    print(f"Palantir Ontology 合并抽取测试（v2 - 通用化+属性区分）")
    print(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")

    # ==========================================
    # 阶段1: 解析所有PDF
    # ==========================================
    print(f"\n{'='*80}")
    print("阶段1: 解析所有PDF文档")
    print(f"{'='*80}")

    pdf_texts = {}
    for i, pdf_filename in enumerate(PDF_FILES, 1):
        pdf_path = os.path.join(PDF_DIR, pdf_filename)
        print(f"\n[{i}/3] 解析: {pdf_filename}")
        text = parse_file(pdf_path)
        pdf_texts[pdf_filename] = text
        print(f"  全文长度: {len(text)} 字符")

    # ==========================================
    # 阶段2: 合并提取统一Ontology Schema
    # ==========================================
    print(f"\n{'='*80}")
    print("阶段2: 从合并代表性文本提取统一Ontology Schema")
    print(f"{'='*80}")

    schema_sample_text = ""
    for pdf_filename, text in pdf_texts.items():
        sample = text[:SCHEMA_SAMPLE_SIZE]
        schema_sample_text += f"\n\n===== 文档: {pdf_filename} =====\n\n{sample}"
        print(f"  [{pdf_filename[:30]}...] 取样: {len(sample)} 字符")

    print(f"\n合并取样文本总长度: {len(schema_sample_text)} 字符")

    schema_builder = SchemaBuilder()

    ontology = await schema_builder.build_schema(
        text_content=schema_sample_text,
        chunk_size=8000,
        overlap_percentage=10,
    )

    schema_path = schema_builder.save_schema(ontology)
    print(f"\n统一Ontology Schema提取成功!")
    print(f"  保存路径: {schema_path}")
    print(f"  对象类型数: {len(ontology.get('object_types', []))}")
    print(f"  链接类型数: {len(ontology.get('link_types', []))}")
    print(f"  动作类型数: {len(ontology.get('action_types', []))}")

    print("\n--- Object Types ---")
    for ot in ontology.get('object_types', []):
        props = ot.get('properties', [])
        prop_names = [p['name'] for p in props]
        print(f"  [{ot['name']}] {ot.get('description', '')[:60]}")
        print(f"    primary_key: {ot.get('primary_key', 'N/A')}")
        print(f"    properties({len(props)}): {prop_names[:8]}{'...' if len(props) > 8 else ''}")

    print("\n--- Link Types ---")
    for lt in ontology.get('link_types', []):
        print(f"  [{lt['name']}] {lt.get('source_object_type', '')} -> {lt.get('target_object_type', '')}")

    print("\n--- Action Types ---")
    for at in ontology.get('action_types', []):
        params = at.get('parameters', [])
        param_names = [p['name'] for p in params]
        print(f"  [{at['name']}] 作用于 {at.get('target_object_type', '')}")
        if param_names:
            print(f"    parameters: {param_names}")

    # ==========================================
    # 阶段3: 基于统一Schema分别提取各PDF全文实例
    # ==========================================
    print(f"\n{'='*80}")
    print("阶段3: 基于统一Schema提取各PDF全文的Ontology实例")
    print(f"{'='*80}")

    instance_builder = InstanceBuilder()
    all_instance_results = {}

    for i, (pdf_filename, text) in enumerate(pdf_texts.items(), 1):
        print(f"\n--- [{i}/3] 提取实例: {pdf_filename} ---")
        print(f"  全文长度: {len(text)} 字符")

        try:
            instance_result = await instance_builder.build_instances(
                text_content=text,
                ontology=ontology,
                chunk_size=8000,
                overlap_percentage=15,
            )

            nodes = instance_result.get('nodes', [])
            edges = instance_result.get('edges', [])
            actions = instance_result.get('action_instances', [])
            metadata = instance_result.get('metadata', {})

            print(f"  对象数: {len(nodes)}")
            print(f"  链接数: {len(edges)}")
            print(f"  动作数: {len(actions)}")
            print(f"  成功chunks: {metadata.get('chunk_stats', {}).get('successful_chunks', 0)}/{metadata.get('chunk_stats', {}).get('total_chunks', 0)}")

            for node in nodes[:3]:
                print(f"    [{node['type']}] {node.get('name', '')[:50]}")
            for edge in edges[:3]:
                print(f"    ({edge['type']}) {edge.get('source_object_type', '')} -> {edge.get('target_object_type', '')}")
            for action in actions[:3]:
                print(f"    [Action:{action.get('action_type', '')}] -> {action.get('target_object_name', '')[:40]}")

            all_instance_results[pdf_filename] = {
                "status": "success",
                "nodes": nodes,
                "edges": edges,
                "action_instances": actions,
                "metadata": metadata,
            }

        except Exception as e:
            print(f"  实例提取失败: {e}")
            import traceback
            traceback.print_exc()
            all_instance_results[pdf_filename] = {
                "status": "failed",
                "error": str(e),
                "nodes": [],
                "edges": [],
                "action_instances": [],
                "metadata": {},
            }

    # ==========================================
    # 阶段4: 合并所有实例（区分不同来源的属性值）
    # ==========================================
    print(f"\n{'='*80}")
    print("阶段4: 合并所有实例（区分不同来源的属性值）")
    print(f"{'='*80}")

    merged_nodes = []
    merged_edges = []
    merged_actions = []
    for pdf_filename, inst_result in all_instance_results.items():
        if inst_result["status"] == "success":
            for node in inst_result["nodes"]:
                node["source_pdf"] = pdf_filename
                merged_nodes.append(node)
            for edge in inst_result["edges"]:
                edge["source_pdf"] = pdf_filename
                merged_edges.append(edge)
            for action in inst_result.get("action_instances", []):
                action["source_pdf"] = pdf_filename
                merged_actions.append(action)

    print(f"合并后总对象数: {len(merged_nodes)}")
    print(f"合并后总链接数: {len(merged_edges)}")
    print(f"合并后总动作数: {len(merged_actions)}")

    # 去重合并：同一类型+同一名称的对象，但区分不同来源的属性值
    dedup_nodes = {}
    for node in merged_nodes:
        key = f"{node['type']}::{node['name']}"
        if key not in dedup_nodes:
            dedup_nodes[key] = {
                "id": node["id"],
                "type": node["type"],
                "name": node["name"],
                "properties_by_source": {},
                "source_pdfs": [],
            }
        
        entry = dedup_nodes[key]
        pdf_name = node.get("source_pdf", "unknown")
        
        # 保留每个来源的属性值（不合并覆盖）
        entry["properties_by_source"][pdf_name] = node.get("properties", {})
        
        if pdf_name not in entry["source_pdfs"]:
            entry["source_pdfs"].append(pdf_name)

    # 构建最终节点列表：合并属性，冲突属性标注来源
    final_nodes = []
    for key, entry in dedup_nodes.items():
        sources = entry["source_pdfs"]
        props_by_source = entry["properties_by_source"]
        
        if len(sources) == 1:
            # 只有一个来源，直接使用
            merged_props = props_by_source[sources[0]]
        else:
            # 多个来源，合并属性，冲突值标注来源
            merged_props = {}
            all_keys = set()
            for props in props_by_source.values():
                all_keys.update(props.keys())
            
            for prop_key in sorted(all_keys):
                values_by_pdf = {}
                for pdf_name, props in props_by_source.items():
                    if prop_key in props and props[prop_key]:
                        values_by_pdf[pdf_name] = props[prop_key]
                
                if len(values_by_pdf) == 0:
                    continue
                elif len(values_by_pdf) == 1:
                    merged_props[prop_key] = list(values_by_pdf.values())[0]
                else:
                    unique_values = set(str(v) for v in values_by_pdf.values())
                    if len(unique_values) == 1:
                        merged_props[prop_key] = list(values_by_pdf.values())[0]
                    else:
                        # 不同来源有不同的值，保留所有值并标注来源
                        short_names = {}
                        for pdf_name, val in values_by_pdf.items():
                            short = pdf_name.split('-')[0].replace('信银理财', '')[:6]
                            short_names[short] = val
                        merged_props[prop_key] = short_names
        
        final_nodes.append({
            "id": entry["id"],
            "type": entry["type"],
            "name": entry["name"],
            "properties": merged_props,
            "source_pdfs": sources,
        })

    # 去重边
    valid_node_ids = {n['id'] for n in final_nodes}
    final_edges = []
    seen_edges = set()
    for edge in merged_edges:
        edge_sig = f"{edge['type']}::{edge.get('source_id', '')}::{edge.get('target_id', '')}"
        if edge['source_id'] in valid_node_ids and edge['target_id'] in valid_node_ids:
            if edge_sig not in seen_edges:
                seen_edges.add(edge_sig)
                pdf_name = edge.get("source_pdf", "")
                final_edges.append({
                    "id": edge["id"],
                    "type": edge["type"],
                    "source_id": edge["source_id"],
                    "target_id": edge["target_id"],
                    "source_object_type": edge.get("source_object_type", ""),
                    "target_object_type": edge.get("target_object_type", ""),
                    "properties": edge.get("properties", {}),
                    "source_location": edge.get("source_location"),
                    "source_pdf": pdf_name,
                })

    # 去重动作
    final_actions = []
    seen_actions = set()
    for action in merged_actions:
        action_sig = f"{action.get('action_type', '')}::{action.get('target_object_name', '')}::{action.get('source_pdf', '')}"
        if action_sig not in seen_actions:
            seen_actions.add(action_sig)
            final_actions.append({
                "id": action["id"],
                "action_type": action.get("action_type", ""),
                "target_object_id": action.get("target_object_id"),
                "target_object_name": action.get("target_object_name", ""),
                "target_object_type": action.get("target_object_type", ""),
                "parameters": action.get("parameters", {}),
                "source_location": action.get("source_location"),
                "source_pdf": action.get("source_pdf", ""),
            })

    print(f"去重后对象数: {len(final_nodes)}")
    print(f"去重后链接数: {len(final_edges)}")
    print(f"去重后动作数: {len(final_actions)}")

    # 保存完整结果
    output_path = os.path.join(OUTPUT_DIR, f"ontology_merged_result_{timestamp}.json")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    final_result = {
        "timestamp": timestamp,
        "merged_ontology_schema": ontology,
        "schema_path": schema_path,
        "merged_nodes": final_nodes,
        "merged_edges": final_edges,
        "merged_action_instances": final_actions,
        "per_pdf_results": {},
    }

    for pdf_filename, inst_result in all_instance_results.items():
        final_result["per_pdf_results"][pdf_filename] = {
            "status": inst_result["status"],
            "nodes_count": len(inst_result.get("nodes", [])),
            "edges_count": len(inst_result.get("edges", [])),
            "actions_count": len(inst_result.get("action_instances", [])),
            "metadata": inst_result.get("metadata", {}),
        }
        if inst_result["status"] == "failed":
            final_result["per_pdf_results"][pdf_filename]["error"] = inst_result.get("error", "")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)

    print(f"\n完整结果已保存到: {output_path}")

    # ==========================================
    # 阶段5: 生成最终报告
    # ==========================================
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    type_counts = {}
    for node in final_nodes:
        t = node.get('type', 'Unknown')
        type_counts[t] = type_counts.get(t, 0) + 1

    link_type_counts = {}
    for edge in final_edges:
        t = edge.get('type', 'Unknown')
        link_type_counts[t] = link_type_counts.get(t, 0) + 1

    action_type_counts = {}
    for action in final_actions:
        t = action.get('action_type', 'Unknown')
        action_type_counts[t] = action_type_counts.get(t, 0) + 1

    # 检查属性区分效果
    multi_source_nodes = [n for n in final_nodes if len(n.get('source_pdfs', [])) > 1]
    differentiated_props = 0
    for node in multi_source_nodes:
        for k, v in node.get('properties', {}).items():
            if isinstance(v, dict):
                differentiated_props += 1

    print(f"\n{'='*80}")
    print("最终报告")
    print(f"{'='*80}")
    print(f"总耗时: {duration:.1f}秒 ({duration/60:.1f}分钟)")
    print(f"\n1. 统一Ontology Schema:")
    print(f"   对象类型: {len(ontology.get('object_types', []))}")
    print(f"   链接类型: {len(ontology.get('link_types', []))}")
    print(f"   动作类型: {len(ontology.get('action_types', []))}")

    print(f"\n2. 实例抽取结果:")
    for pdf_filename, inst_result in all_instance_results.items():
        short_name = pdf_filename[:30]
        if inst_result["status"] == "success":
            print(f"   [{short_name}...] 对象={len(inst_result.get('nodes', []))}, 链接={len(inst_result.get('edges', []))}, 动作={len(inst_result.get('action_instances', []))}")
        else:
            print(f"   [{short_name}...] 失败: {inst_result.get('error', '')[:50]}")

    print(f"\n3. 合并去重结果:")
    print(f"   总对象: {len(final_nodes)}")
    print(f"   总链接: {len(final_edges)}")
    print(f"   总动作: {len(final_actions)}")
    print(f"   多来源对象: {len(multi_source_nodes)}")
    print(f"   区分属性数: {differentiated_props}")

    print(f"\n4. 对象类型分布:")
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"   {t}: {c}")

    print(f"\n5. 链接类型分布:")
    for t, c in sorted(link_type_counts.items(), key=lambda x: -x[1]):
        print(f"   {t}: {c}")

    print(f"\n6. 动作类型分布:")
    for t, c in sorted(action_type_counts.items(), key=lambda x: -x[1]):
        print(f"   {t}: {c}")

    if multi_source_nodes:
        print(f"\n7. 多来源对象属性区分示例:")
        for node in multi_source_nodes[:3]:
            print(f"   [{node['type']}] {node['name'][:40]}")
            for k, v in node.get('properties', {}).items():
                if isinstance(v, dict):
                    print(f"     {k}: {v}")

    report_path = os.path.join(OUTPUT_DIR, f"ontology_report_{timestamp}.json")
    report = {
        "timestamp": timestamp,
        "duration_seconds": duration,
        "schema_summary": {
            "object_types_count": len(ontology.get('object_types', [])),
            "link_types_count": len(ontology.get('link_types', [])),
            "action_types_count": len(ontology.get('action_types', [])),
            "object_type_names": [ot['name'] for ot in ontology.get('object_types', [])],
            "link_type_names": [lt['name'] for lt in ontology.get('link_types', [])],
            "action_type_names": [at['name'] for at in ontology.get('action_types', [])],
        },
        "instance_summary": {
            "total_nodes": len(final_nodes),
            "total_edges": len(final_edges),
            "total_actions": len(final_actions),
            "type_distribution": type_counts,
            "link_type_distribution": link_type_counts,
            "action_type_distribution": action_type_counts,
            "multi_source_nodes": len(multi_source_nodes),
            "differentiated_properties": differentiated_props,
        },
        "per_pdf_summary": final_result["per_pdf_results"],
        "output_files": {
            "schema": schema_path,
            "merged_result": output_path,
            "report": report_path,
        },
    }

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n报告已保存到: {report_path}")
    print(f"\n{'='*80}")
    print("全部处理完成!")
    print(f"{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
