#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
RAGFlow 图谱注入 API — 支持外部本体系统直接注入知识图谱数据。

部署: 将此文件放入 RAGFlow 的 api/apps/restful_apis/ 目录即可自动注册路由。
路由: POST /api/v1/datasets/{dataset_id}/knowledge_graph/inject
      POST /api/v1/datasets/{dataset_id}/knowledge_graph/inject/ontology

设计要点:
  - 注入格式与 RAGFlow 原生 GraphRAG (graph_node_to_chunk / graph_edge_to_chunk) 完全一致
  - 支持调用方自带向量 (vectors 字段)，或由 RAGFlow 的 embedding 模型生成
  - KGSearch 检索时无需任何修改即可正确检索到注入的图谱数据
"""

import asyncio
import json
import logging
import time

import networkx as nx
from networkx.readwrite import json_graph
from quart import request

from api.apps import login_required
from api.utils.api_utils import (
    get_error_argument_result,
    get_error_data_result,
    get_result,
    add_tenant_id_to_kwargs,
)
from common import settings
from common.constants import ParserType
from common.misc_utils import get_uuid, thread_pool_exec
from rag.nlp import search, rag_tokenizer
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.dialog_service import DialogService
from api.db.services.llm_service import LLMBundle
from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
from common.constants import LLMType
from rag.graphrag.utils import n_neighbor


# ─── 本体系统类型映射 ──────────────────────────────────────────────
# 将本体系统的 OWL 类型映射为 RAGFlow 原生 GraphRAG 的实体类型（大写）
# RAGFlow 默认实体类型: organization, person, geo, event, category
# KGSearch 的 query_rewrite 会用 LLM 从 ty2ents 中选择类型，必须与 ty2ents 中的类型匹配

OWL_TO_RAGFLOW_TYPE = {
    "owl:Class": "CATEGORY",
    "owl:NamedIndividual": "CATEGORY",
    "owl:DatatypeProperty": "CATEGORY",
    "owl:ObjectProperty": "CATEGORY",
    "owl:ActionType": "EVENT",
    "owl:ActionInstance": "EVENT",
}


def _map_entity_type(owl_type: str) -> str:
    """将 OWL 类型映射为 RAGFlow 原生实体类型（大写）。"""
    return OWL_TO_RAGFLOW_TYPE.get(owl_type, "CATEGORY")


def _build_entity_description(ent_name: str, ent_type: str, props: dict) -> str:
    parts = [f"{ent_name}是一个{_map_entity_type(ent_type)}类型的实体"]
    if props:
        prop_parts = []
        for k, v in props.items():
            if k in ("description", "_source_file", "_source_quote", "_source_chunk_index"):
                continue
            if v is None or v == "":
                continue
            prop_parts.append(f"{k}: {v}")
        if prop_parts:
            parts.append("，属性包括" + "，".join(prop_parts))
    return "".join(parts)


def _build_relation_description(from_ent: str, rel_label: str, to_ent: str) -> str:
    return f"{from_ent} {rel_label} {to_ent}"


# ─── 格式转换 ──────────────────────────────────────────────────────

def _ontology_to_nx_graph(nodes: list[dict], edges: list[dict], doc_id: str) -> nx.Graph:
    """将 OntologySystem GraphData 格式转换为 RAGFlow 的 networkx.Graph。

    关键：与 RAGFlow 原生 graph_node_to_chunk 保持一致：
      - 实体名大写 (entity_kwd = ent_name.upper())
      - entity_type 大写 (entity_type_kwd = entity_type.upper())
      - 每个节点属性包含: entity_type, description, source_id, pagerank, rank
    """
    graph = nx.Graph()
    graph.graph["source_id"] = [doc_id]

    node_id_to_label: dict[str, str] = {}

    for node in nodes:
        data = node.get("data", {})
        label = data.get("label", node.get("id", ""))
        node_type = data.get("type", "owl:Class")
        props = data.get("properties", {})
        desc = data.get("description", "") or props.get("description", "")
        node_id = node.get("id", "")

        # 动作类型识别
        raw_id = data.get("raw_id", "")
        is_action = raw_id.startswith("AT_") or node_id.startswith("AT_")
        if is_action and node_type == "owl:Class":
            node_type = "owl:ActionType"

        # 映射为 RAGFlow 原生实体类型（大写）
        entity_type = _map_entity_type(node_type)

        if not desc:
            desc = _build_entity_description(label, node_type, props)

        node_id_to_label[node_id] = label
        if label:
            node_id_to_label[label] = label

        # 实体名大写，与 RAGFlow 原生一致
        ent_name_upper = label.upper()

        graph.add_node(
            ent_name_upper,
            entity_type=entity_type,
            description=desc,
            source_id=[doc_id],
        )

    for edge in edges:
        data = edge.get("data", {})
        source_id = edge.get("source", "")
        target_id = edge.get("target", "")
        relation = data.get("relation", "")
        rel_label = data.get("label", relation or "相关")

        source_name = node_id_to_label.get(source_id, source_id)
        target_name = node_id_to_label.get(target_id, target_id)

        if not source_name or not target_name:
            continue

        rel_desc = _build_relation_description(source_name, rel_label, target_name)

        # 实体名大写，与 RAGFlow 原生一致
        pair = sorted([source_name.upper(), target_name.upper()])
        existing = graph.get_edge_data(pair[0], pair[1])
        if existing:
            existing["weight"] += 1.0
            existing["description"] += f"<SEP>{rel_desc}"
            existing["keywords"].append(rel_label)
            existing["source_id"].append(doc_id)
        else:
            graph.add_edge(
                pair[0], pair[1],
                weight=1.0,
                description=rel_desc,
                keywords=[rel_label],
                source_id=[doc_id],
            )

    # 计算 pagerank 和 rank，与 RAGFlow 原生一致
    try:
        pagerank = nx.pagerank(graph)
        for node, pr in pagerank.items():
            graph.nodes[node]["pagerank"] = pr
    except Exception:
        for node in graph.nodes():
            graph.nodes[node]["pagerank"] = 0.001

    for node_degree in graph.degree:
        graph.nodes[str(node_degree[0])]["rank"] = int(node_degree[1])

    return graph


def _ragflow_to_nx_graph(graph_data: dict, doc_id: str) -> nx.Graph:
    """将 RAGFlow 原生格式 (networkx node_link_data) 转换为 networkx.Graph。"""
    try:
        g = json_graph.node_link_graph(graph_data, edges="edges")
    except Exception:
        g = nx.Graph()

    if "source_id" not in g.graph:
        g.graph["source_id"] = [doc_id]

    for node, attrs in g.nodes(data=True):
        if "description" not in attrs:
            attrs["description"] = node
        if "source_id" not in attrs:
            attrs["source_id"] = [doc_id]
        if "entity_type" not in attrs:
            attrs["entity_type"] = "CATEGORY"
        # 确保实体类型大写
        attrs["entity_type"] = attrs["entity_type"].upper()

    for source, target, attrs in g.edges(data=True):
        if "description" not in attrs:
            attrs["description"] = f"{source} relates to {target}"
        if "source_id" not in attrs:
            attrs["source_id"] = [doc_id]
        if "keywords" not in attrs:
            attrs["keywords"] = []
        if "weight" not in attrs:
            attrs["weight"] = 1.0

    # 计算 pagerank 和 rank
    try:
        pagerank = nx.pagerank(g)
        for node, pr in pagerank.items():
            g.nodes[node]["pagerank"] = pr
    except Exception:
        for node in g.nodes():
            g.nodes[node]["pagerank"] = 0.001

    for node_degree in g.degree:
        g.nodes[str(node_degree[0])]["rank"] = int(node_degree[1])

    return g


# ─── 直接写 ES 的图谱注入 ─────────────────────────────────────────

# 向量编码配置：分批并行
# - BATCH_SIZE: 单次 encode 调用的文本数。Ollama bge-m3 单次处理超过 32 条会显著变慢，
#   小批次能让 Ollama 更快产出第一批结果，并允许多个批次重叠等待
# - MAX_CONCURRENCY: 最大并发 encode 调用数。Ollama 内部对同一模型通常会串行/限流，
#   设置过高反而会因排队抵消并行优势。CPU/GPU 资源有限时 4-8 较合适
_VEC_BATCH_SIZE = 32
_VEC_MAX_CONCURRENCY = 8
_VEC_BATCH_TIMEOUT = 120  # 单批次超时（秒）


async def _encode_in_batches(embd_mdl, texts: list[str]) -> list:
    """分批并行调用 embedding 模型编码文本。

    将 texts 切分为大小为 _VEC_BATCH_SIZE 的批次，使用 Semaphore 限制并发数为
    _VEC_MAX_CONCURRENCY。返回与 texts 顺序对齐的向量列表，编码失败的位置为 None。
    """
    if not texts:
        return []

    # 切分批次
    batches: list[tuple[int, list[str]]] = []
    for i in range(0, len(texts), _VEC_BATCH_SIZE):
        batches.append((i, texts[i:i + _VEC_BATCH_SIZE]))

    results: list = [None] * len(texts)
    semaphore = asyncio.Semaphore(_VEC_MAX_CONCURRENCY)

    async def encode_batch(start_idx: int, batch: list[str]):
        async with semaphore:
            try:
                embd_results, _ = await asyncio.wait_for(
                    asyncio.to_thread(embd_mdl.encode, batch),
                    timeout=_VEC_BATCH_TIMEOUT,
                )
                for j in range(len(batch)):
                    if j < len(embd_results) and len(embd_results[j]) > 0:
                        results[start_idx + j] = embd_results[j]
            except Exception as e:
                logging.warning(
                    f"[GraphInject] Batch encoding failed (start={start_idx}, size={len(batch)}): {e}"
                )

    # 并行执行所有批次
    await asyncio.gather(*[encode_batch(idx, batch) for idx, batch in batches])
    return results


async def _generate_vectors(graph: nx.Graph, embd_mdl) -> dict:
    """使用 RAGFlow 的 embedding 模型为实体和关系生成向量（分批并行）。

    与 RAGFlow 原生 graph_node_to_chunk / graph_edge_to_chunk 保持一致：
      - 实体：编码实体名称（ent_name）
      - 关系：编码 "from->to: description"

    优化：将文本切分为多批，使用 asyncio.gather + Semaphore 并行调用 embedding 模型，
         相比单次大批量调用，可显著减少注入时长（特别是图谱较大时）。
    """
    vectors = {"entities": {}, "relations": {}}

    # 收集所有需要编码的文本
    ent_names: list[str] = []
    ent_texts: list[str] = []
    for node, attrs in graph.nodes(data=True):
        ent_names.append(node)
        ent_texts.append(node)  # 与 RAGFlow 原生一致：只编码实体名称

    rel_keys: list[str] = []
    rel_texts: list[str] = []
    for source, target, attrs in graph.edges(data=True):
        description = attrs.get("description", f"{source} relates to {target}")
        rel_keys.append(f"{source}->{target}")
        # 与 RAGFlow 原生一致：编码 "from->to: description"
        rel_texts.append(f"{source}->{target}: {description}")

    # 实体和关系并行编码（互相独立，可同时进行）
    t_start = time.time()
    ent_results, rel_results = await asyncio.gather(
        _encode_in_batches(embd_mdl, ent_texts),
        _encode_in_batches(embd_mdl, rel_texts),
    )
    elapsed = time.time() - t_start

    # 装配实体向量
    for i, name in enumerate(ent_names):
        if i < len(ent_results) and ent_results[i] is not None:
            vectors["entities"][name] = ent_results[i]

    # 装配关系向量
    for i, key in enumerate(rel_keys):
        if i < len(rel_results) and rel_results[i] is not None:
            vectors["relations"][key] = rel_results[i]

    logging.info(
        f"[GraphInject] Encoded {len(vectors['entities'])}/{len(ent_texts)} entities, "
        f"{len(vectors['relations'])}/{len(rel_texts)} relations in {elapsed:.1f}s "
        f"(batch_size={_VEC_BATCH_SIZE}, max_concurrency={_VEC_MAX_CONCURRENCY})"
    )

    return vectors


async def _write_graph_to_es(tenant_id: str, kb_id: str, graph: nx.Graph,
                              doc_id: str, vectors: dict | None = None,
                              embd_mdl=None) -> dict:
    """
    直接将图谱数据写入 ES。

    与 RAGFlow 原生 GraphRAG 存储格式完全兼容（graph_node_to_chunk / graph_edge_to_chunk），
    KGSearch 检索时无需任何修改即可正确检索到注入的图谱数据。

    写入:
      - entity chunks (knowledge_graph_kwd="entity")
      - relation chunks (knowledge_graph_kwd="relation")
      - graph structure chunk (knowledge_graph_kwd="graph")
      - ty2ents chunk (knowledge_graph_kwd="ty2ents")

    Args:
        tenant_id: 租户 ID
        kb_id: 知识库 ID
        graph: networkx.Graph 图谱
        doc_id: 文档 ID
        vectors: 预计算的向量，格式:
            {
                "entities": {"实体名": [0.1, 0.2, ...]},
                "relations": {"源->目标": [0.1, 0.2, ...]}
            }
            如果为 None 且 embd_mdl 不为 None，则自动用 embedding 模型生成向量
        embd_mdl: RAGFlow 的 embedding 模型实例，用于自动生成向量
    """
    index_name = search.index_name(tenant_id)
    chunks = []

    # 自动生成向量（如果没有预计算向量但有 embedding 模型）
    if vectors is None and embd_mdl is not None:
        vectors = await _generate_vectors(graph, embd_mdl)

    # 1. 写入实体 chunks — 与 RAGFlow 原生 graph_node_to_chunk 完全一致
    for node, attrs in graph.nodes(data=True):
        ent_name = node  # 已经是大写
        entity_type = attrs.get("entity_type", "CATEGORY")  # 已经是大写
        description = attrs.get("description", ent_name)
        source_id = attrs.get("source_id", [doc_id])
        pagerank = attrs.get("pagerank", 0.001)
        rank = attrs.get("rank", 1)

        # 与 RAGFlow 原生 graph_node_to_chunk 的 meta 格式一致
        meta = {
            "entity_name": ent_name,
            "entity_type": entity_type,
            "description": description,
            "source_id": source_id,
            "pagerank": pagerank,
            "rank": rank,
        }

        # 计算 n-hop 邻居路径（与 RAGFlow 原生 set_graph 一致）
        nhop_neighbors = n_neighbor(graph, node)

        chunk = {
            "id": get_uuid(),
            "important_kwd": [ent_name],
            "title_tks": rag_tokenizer.tokenize(ent_name),
            "entity_kwd": ent_name,
            "knowledge_graph_kwd": "entity",
            "entity_type_kwd": entity_type,  # 大写，与 RAGFlow 原生一致
            "content_with_weight": json.dumps(meta, ensure_ascii=False),
            "content_ltks": rag_tokenizer.tokenize(description),
            "content_sm_ltks": rag_tokenizer.fine_grained_tokenize(
                rag_tokenizer.tokenize(description)
            ),
            "source_id": source_id,
            "kb_id": kb_id,
            "available_int": 0,  # 与 RAGFlow 原生一致
            "rank_flt": float(pagerank),  # pagerank 值，用于排序
            "n_hop_with_weight": json.dumps(nhop_neighbors, ensure_ascii=False),
        }

        # 写入向量
        if vectors and "entities" in vectors and ent_name in vectors["entities"]:
            vec = vectors["entities"][ent_name]
            chunk[f"q_{len(vec)}_vec"] = vec

        chunks.append(chunk)

    # 2. 写入关系 chunks — 与 RAGFlow 原生 graph_edge_to_chunk 完全一致
    for source, target, attrs in graph.edges(data=True):
        description = attrs.get("description", f"{source} relates to {target}")
        keywords = attrs.get("keywords", [])
        source_id = attrs.get("source_id", [doc_id])
        weight = attrs.get("weight", 1.0)

        # 与 RAGFlow 原生 graph_edge_to_chunk 的 meta 格式一致
        meta = {
            "src_id": source,
            "tgt_id": target,
            "description": description,
            "keywords": keywords,
            "weight": int(weight),
            "source_id": source_id,
        }

        chunk = {
            "id": get_uuid(),
            "from_entity_kwd": source,  # 已经是大写
            "to_entity_kwd": target,    # 已经是大写
            "knowledge_graph_kwd": "relation",
            "content_with_weight": json.dumps(meta, ensure_ascii=False),
            "content_ltks": rag_tokenizer.tokenize(description),
            "content_sm_ltks": rag_tokenizer.fine_grained_tokenize(
                rag_tokenizer.tokenize(description)
            ),
            "important_kwd": keywords,
            "source_id": source_id,
            "weight_int": int(weight),
            "kb_id": kb_id,
            "available_int": 0,  # 与 RAGFlow 原生一致
        }

        # 写入向量 — 与 RAGFlow 原生一致：编码 "from->to: description"
        rel_key = f"{source}->{target}"
        if vectors and "relations" in vectors and rel_key in vectors["relations"]:
            vec = vectors["relations"][rel_key]
            chunk[f"q_{len(vec)}_vec"] = vec

        chunks.append(chunk)

    # 3. 写入图谱结构 chunk — 与 RAGFlow 原生 set_graph() 一致
    # 原生使用 nx.node_link_data(graph, edges="edges") 序列化
    graph_content = json.dumps(json_graph.node_link_data(graph, edges="edges"), ensure_ascii=False)
    graph_chunk = {
        "id": get_uuid(),
        "content_with_weight": graph_content,
        "knowledge_graph_kwd": "graph",
        "kb_id": kb_id,
        "source_id": [doc_id],
        "available_int": 0,  # 与 RAGFlow 原生一致
        "removed_kwd": "N",
    }
    chunks.append(graph_chunk)

    # 4. 写入 subgraph chunk — 与 RAGFlow 原生 set_graph() 一致，供图谱页面/文档级恢复使用
    subgraph_chunk = {
        "id": get_uuid(),
        "content_with_weight": graph_content,
        "knowledge_graph_kwd": "subgraph",
        "kb_id": kb_id,
        "source_id": [doc_id],
        "available_int": 0,
        "removed_kwd": "N",
    }
    chunks.append(subgraph_chunk)

    # 5. 写入 ty2ents chunk — KGSearch 的 query_rewrite 需要此数据
    # ty2ents 格式: {"CATEGORY": ["实体1", "实体2"], "EVENT": ["动作1", "动作2"]}
    ty2ents: dict[str, list[str]] = {}
    for node, attrs in graph.nodes(data=True):
        entity_type = attrs.get("entity_type", "CATEGORY")
        if entity_type not in ty2ents:
            ty2ents[entity_type] = []
        ty2ents[entity_type].append(node)

    ty2ents_chunk = {
        "id": get_uuid(),
        "content_with_weight": json.dumps(ty2ents, ensure_ascii=False),
        "knowledge_graph_kwd": "ty2ents",
        "kb_id": kb_id,
        "available_int": 0,
        "removed_kwd": "N",
    }
    chunks.append(ty2ents_chunk)

    # 6. 批量写入 ES
    await thread_pool_exec(
        settings.docStoreConn.insert,
        chunks,
        index_name,
        kb_id,
    )

    return {
        "entities_created": len([c for c in chunks if c.get("knowledge_graph_kwd") == "entity"]),
        "relations_created": len([c for c in chunks if c.get("knowledge_graph_kwd") == "relation"]),
        "graph_updated": True,
        "ty2ents_updated": True,
        "has_vectors": vectors is not None,
    }


async def _delete_graph_from_es(tenant_id: str, kb_id: str):
    """删除数据集的整个图谱数据。"""
    index_name = search.index_name(tenant_id)
    await thread_pool_exec(
        settings.docStoreConn.delete,
        {"knowledge_graph_kwd": ["graph", "subgraph", "entity", "relation", "ty2ents", "community_report"]},
        index_name,
        kb_id,
    )


def _enable_kg_for_dataset_chats(tenant_id: str, kb_id: str) -> int:
    """自动启用引用该知识库的对话的知识图谱检索。"""
    updated = 0
    try:
        dialogs = DialogService.query(tenant_id=tenant_id)
        for dialog in dialogs:
            kb_ids = dialog.kb_ids or []
            if kb_id not in kb_ids:
                continue
            prompt_config = dialog.prompt_config or {}
            if prompt_config.get("use_kg") is True:
                continue
            prompt_config["use_kg"] = True
            DialogService.update_by_id(dialog.id, {"prompt_config": prompt_config})
            updated += 1
    except Exception as e:
        logging.warning(f"[GraphInject] Failed to enable use_kg for dialogs: {e}")
    return updated


# ─── 核心注入逻辑 ──────────────────────────────────────────────────

async def _do_inject(tenant_id, dataset_id, graph_data, fmt, doc_id, merge_mode, vectors):
    """核心注入逻辑。"""
    # 1. 验证权限
    if not KnowledgebaseService.accessible(dataset_id, tenant_id):
        return False, "No authorization."

    ok, kb = KnowledgebaseService.get_by_id(dataset_id)
    if not ok:
        return False, f"Dataset '{dataset_id}' not found"

    # 2. 将知识库的 parser_id 设置为 "knowledge_graph"
    #    RAGFlow 在 dialog_service.py 中判断是否激活图谱检索的逻辑是：
    #    is_knowledge_graph = all([kb.parser_id == ParserType.KG for kb in kbs])
    if kb.parser_id != ParserType.KG:
        logging.info(f"[GraphInject] Setting parser_id to 'knowledge_graph' for dataset {dataset_id} "
                     f"(was '{kb.parser_id}')")
        KnowledgebaseService.update_by_id(dataset_id, {"parser_id": ParserType.KG})

    # 3. 生成虚拟 doc_id
    if not doc_id:
        doc_id = f"injected_graph_{int(time.time())}"

    # 4. 转换为 networkx.Graph
    try:
        if fmt == "ontology":
            nodes = graph_data.get("nodes", [])
            edges = graph_data.get("edges", [])
            if not nodes:
                return False, "Graph data has no nodes"
            new_graph = _ontology_to_nx_graph(nodes, edges, doc_id)
        else:
            if not graph_data.get("nodes"):
                return False, "Graph data has no nodes"
            new_graph = _ragflow_to_nx_graph(graph_data, doc_id)
    except Exception as e:
        logging.exception(f"Failed to convert graph data: {e}")
        return False, f"Failed to convert graph data: {str(e)}"

    if len(new_graph.nodes) == 0:
        return False, "Converted graph has no nodes"

    # 5. 获取 RAGFlow 的 embedding 模型，为实体和关系生成向量
    #    KGSearch 的 get_relevant_ents_by_keywords 和 get_relevant_relations_by_txt
    #    都依赖向量检索，没有向量就无法匹配到图谱数据
    embd_mdl = None
    if vectors is None:
        try:
            # RAGFlow 0.26.0: 使用 get_tenant_default_model_by_type 获取租户默认的 embedding 模型
            embd_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.EMBEDDING)
            embd_mdl = LLMBundle(tenant_id, embd_model_config)
            logging.info(f"[GraphInject] Got embedding model: {embd_mdl.llm_name}")
        except Exception as e:
            logging.warning(f"[GraphInject] Failed to get embedding model, vectors will not be generated: {e}")

    # 6. 写入 ES（自动用 embedding 模型生成向量）
    try:
        if merge_mode == "replace":
            await _delete_graph_from_es(tenant_id, dataset_id)

        stats = await _write_graph_to_es(tenant_id, dataset_id, new_graph, doc_id, vectors, embd_mdl)
        stats["dialogs_use_kg_enabled"] = _enable_kg_for_dataset_chats(tenant_id, dataset_id)
    except Exception as e:
        logging.exception(f"Failed to inject graph: {e}")
        return False, f"Failed to inject graph: {str(e)}"

    return True, stats


# ═══════════════════════════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════════════════════════

@manager.route('/datasets/<dataset_id>/knowledge_graph/inject', methods=['POST'])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def inject_knowledge_graph(tenant_id, dataset_id):
    """
    注入自定义知识图谱数据到指定数据集。
    ---
    tags:
      - Knowledge Graph Injection
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - graph
          properties:
            graph:
              type: object
              description: |
                图谱数据，支持两种格式:
                1. RAGFlow 原生 (format="ragflow"):
                   {"nodes": [{"id": "实体名", "entity_type": "类", "description": "..."}],
                    "edges": [{"source": "A", "target": "B", "weight": 1, "description": "...", "keywords": []}]}
                2. 本体系统 (format="ontology"):
                   {"nodes": [{"id": "...", "data": {"label": "实体名", "type": "owl:Class", "properties": {}}}],
                    "edges": [{"source": "...", "target": "...", "data": {"label": "关系名", "relation": "rel_id"}}]}
            format:
              type: string
              enum: ["ragflow", "ontology"]
              default: "ragflow"
            doc_id:
              type: string
              description: 关联的文档 ID，不提供则自动生成。
            merge_mode:
              type: string
              enum: ["replace", "merge"]
              default: "merge"
            vectors:
              type: object
              description: |
                预计算的向量（可选）。如果不提供，则由 RAGFlow 的 embedding 模型生成。
                格式: {"entities": {"实体名": [0.1, ...]}, "relations": {"源->目标": [0.1, ...]}}
                向量维度必须一致。
    """
    req = await request.get_json()
    if not req:
        return get_error_argument_result("Request body is empty")

    graph_data = req.get("graph")
    if not graph_data:
        return get_error_argument_result("'graph' field is required")

    fmt = req.get("format", "ragflow")
    if fmt not in ("ragflow", "ontology"):
        return get_error_argument_result(f"Invalid format '{fmt}', must be 'ragflow' or 'ontology'")

    doc_id = req.get("doc_id")
    merge_mode = req.get("merge_mode", "merge")
    if merge_mode not in ("replace", "merge"):
        return get_error_argument_result(f"Invalid merge_mode '{merge_mode}', must be 'replace' or 'merge'")

    vectors = req.get("vectors")

    try:
        success, result = await _do_inject(tenant_id, dataset_id, graph_data, fmt, doc_id, merge_mode, vectors)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message=f"Internal server error: {str(e)}")


@manager.route('/datasets/<dataset_id>/knowledge_graph/inject/ontology', methods=['POST'])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def inject_ontology_graph(tenant_id, dataset_id):
    """
    注入本体系统 (OntologySystem) 格式的知识图谱数据。
    ---
    tags:
      - Knowledge Graph Injection
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - nodes
          properties:
            nodes:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                  data:
                    type: object
                    properties:
                      label:
                        type: string
                      type:
                        type: string
                      properties:
                        type: object
            edges:
              type: array
              items:
                type: object
                properties:
                  source:
                    type: string
                  target:
                    type: string
                  data:
                    type: object
                    properties:
                      label:
                        type: string
                      relation:
                        type: string
            doc_id:
              type: string
            merge_mode:
              type: string
              enum: ["replace", "merge"]
              default: "merge"
            vectors:
              type: object
              description: |
                预计算的向量（可选）。格式:
                {"entities": {"实体名": [0.1, ...]}, "relations": {"源->目标": [0.1, ...]}}
    """
    req = await request.get_json()
    if not req:
        return get_error_argument_result("Request body is empty")

    nodes = req.get("nodes", [])
    edges = req.get("edges", [])
    if not nodes:
        return get_error_argument_result("'nodes' field is required and must not be empty")

    doc_id = req.get("doc_id")
    merge_mode = req.get("merge_mode", "merge")
    vectors = req.get("vectors")
    graph_data = {"nodes": nodes, "edges": edges}

    try:
        success, result = await _do_inject(tenant_id, dataset_id, graph_data, "ontology", doc_id, merge_mode, vectors)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message=f"Internal server error: {str(e)}")
