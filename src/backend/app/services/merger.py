# app/services/merger.py - 本体合并器服务
# 功能：提供TTL文件合并功能，去重并生成合并报告

import os
from rdflib import Graph, RDF, OWL, RDFS, Namespace
from datetime import datetime
from collections import defaultdict
from typing import Tuple, List, Dict, Any
from app.core.exceptions import OntologyMergeException
from app.core.logging import logger


class OntologyMerger:
    def __init__(self):
        self.EX = Namespace("http://www.example.org/auto_ontology#")
        
    def merge_files(self, file_paths: List[str]) -> Tuple[str, str, Dict[str, Any]]:
        """
        Merge multiple TTL files into one with detailed change tracking.
        Returns: (merged_filename, report_text, changes_detail)
        """
        master_g = Graph()
        report = []
        changes_detail = {
            "added": [],
            "duplicates_removed": [],
            "conflicts_detected": [],
            "merged_entities": []
        }
        
        report.append(f"### 📂 本体合并报告 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
        report.append("---")
        
        total_triples_source = 0
        file_stats = []
        file_graphs = []
        
        # 记录合并前的状态
        for idx, path in enumerate(file_paths):
            if not os.path.exists(path):
                continue
            
            temp_g = Graph()
            try:
                temp_g.parse(path, format="turtle")
                count = len(temp_g)
                total_triples_source += count
                filename = os.path.basename(path)
                
                # 统计各类型数量
                classes = len(list(temp_g.subjects(RDF.type, OWL.Class)))
                individuals = len(list(temp_g.subjects(RDF.type, OWL.NamedIndividual)))
                obj_props = len(list(temp_g.subjects(RDF.type, OWL.ObjectProperty)))
                dt_props = len(list(temp_g.subjects(RDF.type, OWL.DatatypeProperty)))
                
                file_stats.append({
                    "name": filename,
                    "triples": count,
                    "classes": classes,
                    "individuals": individuals,
                    "obj_props": obj_props,
                    "dt_props": dt_props
                })
                
                file_graphs.append((filename, temp_g))
                report.append(f"✅ 已加载文件: `{filename}` ({count} 条三元组)")
            except Exception as e:
                report.append(f"❌ 加载文件失败: `{os.path.basename(path)}`, 错误: {str(e)}")

        # 详细合并分析
        report.append("\n### 🔍 详细合并分析")
        
        # 跟踪每个三元组的来源
        triple_sources = defaultdict(list)
        
        for filename, g in file_graphs:
            for s, p, o in g:
                triple_key = (str(s), str(p), str(o))
                triple_sources[triple_key].append(filename)
        
        # 分析重复和唯一三元组
        unique_triples = 0
        duplicate_count = 0
        
        for triple_key, sources in triple_sources.items():
            s, p, o = triple_key
            if len(sources) > 1:
                duplicate_count += len(sources) - 1
                changes_detail["duplicates_removed"].append({
                    "triple": f"{self._format_uri(s)} {self._format_uri(p)} {self._format_uri(o)}",
                    "sources": sources,
                    "kept_from": sources[0]
                })
            else:
                unique_triples += 1
                changes_detail["added"].append({
                    "triple": f"{self._format_uri(s)} {self._format_uri(p)} {self._format_uri(o)}",
                    "source": sources[0]
                })
        
        # 合并到主图
        for filename, g in file_graphs:
            master_g += g
        
        # 合并后的统计
        total_triples_merged = len(master_g)
        
        final_classes = len(list(master_g.subjects(RDF.type, OWL.Class)))
        final_individuals = len(list(master_g.subjects(RDF.type, OWL.NamedIndividual)))
        final_obj_props = len(list(master_g.subjects(RDF.type, OWL.ObjectProperty)))
        final_dt_props = len(list(master_g.subjects(RDF.type, OWL.DatatypeProperty)))

        report.append(f"\n### 📊 合并统计结果")
        report.append(f"- **原始三元组总数**: {total_triples_source}")
        report.append(f"- **合并后唯一三元组**: {total_triples_merged}")
        report.append(f"- **自动去重数量**: {duplicate_count}")
        report.append(f"- **最终规模**: {final_classes} 类, {final_obj_props} 对象属性, {final_dt_props} 数据属性, {final_individuals} 实例")
        
        report.append(f"\n### 📋 去重详情")
        if duplicate_count > 0:
            report.append(f"共发现 {duplicate_count} 条重复三元组，已自动去重")
            # 显示前10条重复示例
            dup_examples = [d for d in changes_detail["duplicates_removed"][:10]]
            for dup in dup_examples:
                report.append(f"  - `{dup['triple'][:100]}...` (来源: {', '.join(dup['sources'])})")
            if len(changes_detail["duplicates_removed"]) > 10:
                report.append(f"  - ... 还有 {len(changes_detail['duplicates_removed']) - 10} 条")
        else:
            report.append("未发现重复三元组")
        
        report.append(f"\n### 🔍 详细对比 (各文件贡献)")
        for stat in file_stats:
            report.append(f"- **{stat['name']}**: {stat['triples']} 三元组 | {stat['classes']} 类 | {stat['individuals']} 实例")

        # 检测潜在冲突
        report.append(f"\n### ⚠️ 潜在冲突检测")
        conflicts = self._detect_conflicts(master_g, file_graphs)
        if conflicts:
            for conflict in conflicts[:10]:
                report.append(f"  - {conflict}")
                changes_detail["conflicts_detected"].append(conflict)
            if len(conflicts) > 10:
                report.append(f"  - ... 还有 {len(conflicts) - 10} 个潜在冲突")
        else:
            report.append("未检测到明显冲突")
        
        # 保存合并后的文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("TTL", exist_ok=True)
        merged_filename = os.path.join("TTL", f"merged_ontology_{timestamp}.ttl")
        master_g.serialize(destination=merged_filename, format="turtle")
        
        report.append(f"\n✅ **合并完成！** 文件已保存为: `{merged_filename}`")
        
        return merged_filename, "\n".join(report), changes_detail
    
    def _format_uri(self, uri_str: str) -> str:
        """格式化URI为可读形式"""
        if "#" in uri_str:
            return uri_str.split("#")[-1]
        elif "/" in uri_str:
            return uri_str.split("/")[-1]
        return uri_str
    
    def _detect_conflicts(self, master_g: Graph, file_graphs: List[Tuple[str, Graph]]) -> List[str]:
        """检测潜在的语义冲突"""
        conflicts = []
        
        # 检测同一实体在不同文件中的不同定义
        entity_definitions = defaultdict(lambda: defaultdict(set))
        
        for filename, g in file_graphs:
            for s in g.subjects(RDF.type, OWL.NamedIndividual):
                s_str = str(s)
                # 获取该实体的所有属性
                for p, o in g.predicate_objects(s):
                    if p != RDF.type and p != RDFS.label:
                        entity_definitions[s_str][(filename, str(p))].add(str(o))
        
        # 查找冲突
        for entity, prop_values in entity_definitions.items():
            props_by_file = defaultdict(dict)
            for (filename, prop), values in prop_values.items():
                if prop not in props_by_file[filename]:
                    props_by_file[filename][prop] = values
            
            # 检查同一属性在不同文件中是否有不同值
            all_props = set()
            for file_props in props_by_file.values():
                all_props.update(file_props.keys())
            
            for prop in all_props:
                values_by_file = {}
                for filename, file_props in props_by_file.items():
                    if prop in file_props:
                        values_by_file[filename] = file_props[prop]
                
                if len(values_by_file) > 1:
                    # 检查值是否不同
                    all_values = list(values_by_file.values())
                    if len(set(str(v) for v in all_values)) > 1:
                        conflicts.append(
                            f"实体 `{self._format_uri(entity)}` 的属性 `{self._format_uri(prop)}` "
                            f"在不同文件中有不同值: {dict((f, list(v)[:2]) for f, v in values_by_file.items())}"
                        )
        
        return conflicts