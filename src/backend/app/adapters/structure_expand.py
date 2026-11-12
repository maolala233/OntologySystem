# app/adapters/structure_expand.py - 结构化切片的确定性实例展开（2026-11-08）
# 外部 skill 对照（get_* 登记表 + 列→关系一次性映射 + 逐行确定性应用）：
#   Excel 交付 272 实例 vs 平台同文档 61 实例的根因是「LLM 逐格现判 + 无登记表」，
#   引用列被当属性、跨切片提及连不上边。本模块把"建模期一次性决策"（结构映射）
#   与"数据期确定性应用"（逐行展开）分离：
#     表格型切片：表头签名 → 结构映射（schema 阶段 LLM 定一次）→ 逐行展开
#     条款型切片：编号条款（第X条 / N.N / 一、）→ 条款号=自然键 → 逐条实例化
#     叙事型（含 txt）：无结构信号 → 原样走 LLM，登记表兜底
# 零 LLM：展开本身确定性，同输入同产物；全部产物带逐字证据与切片溯源。
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Optional

from app.adapters.resolution import normalize_label

# 单项目结构映射上限（按切片频次取前 N 个签名）
MAX_STRUCTURE_MAPS = 6
# 条款型判定：切片内编号行数下限
CLAUSE_MIN_LINES = 4
# 单元格哨兵值（外部对照：'无'/'/'/'-' 等无信息占位）
SENTINEL_VALUES = {"无", "/", "-", "—", "—", "N/A", "NA", "null", "None", ""}
# 展开实体置信度（确定性产物，闸门口径里属"有据"）
EXPANSION_CONFIDENCE = 0.95

# ── 条款编号模式（Word 制度文档 / txt 规章）──
_CLAUSE_PATTERNS = [
    re.compile(r"^#{0,4}\s*第[一二三四五六七八九十百千\d]+条"),
    re.compile(r"^#{0,4}\s*\d{1,3}\.\d{1,2}[^0-9]"),      # 5.1 / 12.3
    re.compile(r"^#{0,4}\s*[一二三四五六七八九十]+、"),
]

_CLAUSE_SPLIT_RE = re.compile(
    r"^(#{0,4}\s*(?:第[一二三四五六七八九十百千\d]+条|\d{1,3}\.\d{1,2}(?![0-9])|[一二三四五六七八九十]+、))\s*(.*)$")


def parse_md_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """解析切片文本里的全部 markdown 表格 → [(表头, 数据行)]（纯函数）。"""
    tables: list[tuple[list[str], list[list[str]]]] = []
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in text.split("\n"):
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            if header is not None:
                tables.append((header, rows))
                header, rows = None, []
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or all(not c for c in cells):
            continue
        if re.fullmatch(r"[-: ]+", "".join(cells)):
            continue  # 分隔行
        if header is None:
            header = cells
        else:
            rows.append(cells)
    if header is not None:
        tables.append((header, rows))
    return tables


def table_signature(text: str) -> Optional[str]:
    """切片的表格签名：meta 表头缺失时取首个表格块的首行作签名。"""
    tables = parse_md_tables(text)
    if not tables:
        return None
    return "|".join(tables[0][0])


def detect_clause_style(text: str) -> Optional[str]:
    """条款型判定：编号行密度 ≥ CLAUSE_MIN_LINES → 返回编号风格签名，否则 None。"""
    n = 0
    for line in text.split("\n"):
        if any(p.match(line.strip()) for p in _CLAUSE_PATTERNS):
            n += 1
    if n >= CLAUSE_MIN_LINES:
        return "clause"
    return None


def _norm_signature(hdr: str) -> str:
    """meta 表头 → 单行签名：docling 表头块（"| A | B |\n| --- |...|"）取首行单元格
    以 "|" 连接，与 parse_md_tables 的行签名同构。"""
    h = str(hdr or "").strip()
    if not h or "|" not in h:
        return h
    first = h.split("\n")[0].strip()
    cells = [c.strip() for c in first.strip("|").split("|")]
    return "|".join(cells)


def _normalize_chunks(prepared: list) -> list:
    """兼容两种入参形态：已是 (idx, text, doc, hdr) 元组直接通过（表头归一）；
    dict/str/属性对象走 _chunk_iter 归一（extract_tasks 的 chunks 是 3 键 dict）。"""
    from app.adapters.extraction import _chunk_iter

    out: list = []
    for c in prepared or []:
        if isinstance(c, tuple) and len(c) == 4:
            out.append((c[0], c[1], c[2], _norm_signature(c[3])))
        else:
            out.extend((_idx, _t, _d, _norm_signature(_h))
                       for _idx, _t, _d, _h in _chunk_iter([c]))
    return out


def detect_patterns(prepared: list) -> dict[str, int]:
    """切片形态盘点（确定性）：{签名: 出现切片数}。表格签名按表头串，条款统一 'clause'。"""
    counts: dict[str, int] = {}
    for _idx, text, _doc, hdr in prepared:
        sig = _norm_signature(hdr) or table_signature(text) or detect_clause_style(text)
        if sig:
            counts[sig] = counts.get(sig, 0) + 1
    return counts


# ── 结构映射生成（schema 阶段，每签名一次 LLM 决策，缓存）──

STRUCTURE_MAP_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "subject_class": {"type": "string",
                          "description": "每行/每条的类名，必须逐字取自【类清单】"},
        "key_column": {"type": "string",
                       "description": "作实例名的列名/字段（自然键），没有合适的给空串"},
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "column": {"type": "string", "description": "原列名，逐字一致"},
                    "kind": {"type": "string",
                             "enum": ["relation", "attribute", "ignore"],
                             "description": "relation=取值是其他实体的引用（每值一条边）；"
                                            "attribute=取值是属性值；ignore=无语义"},
                    "predicate": {"type": "string",
                                  "description": "relation：关系谓词（中文动词短语；"
                                                 "【关系清单】里没有的可新造）"},
                    "target_class": {"type": "string",
                                     "description": "relation：目标类名，逐字取自类清单"},
                    "attribute_name": {"type": "string",
                                       "description": "attribute：属性名（缺省用列名）"},
                },
                "required": ["column", "kind"],
            },
        },
        "new_relations": {
            "type": "array",
            "description": "映射需要而【关系清单】没有的谓词，正式声明（domain=主体类）",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "domain": {"type": "string"},
                    "range": {"type": "string"},
                },
                "required": ["label", "domain", "range"],
            },
        },
    },
    "required": ["subject_class", "columns"],
}

STRUCTURE_MAP_SYSTEM_PROMPT = (
    "你是知识图谱建模专家。给定一个结构化内容模式（表格表头/条款编号）的采样"
    "（列名 + 每列的取值样例）和现有本体类清单，请产出该模式的【结构映射】："
    "每行/每条是什么类（主体），每一列是关系、属性还是无语义。\n"
    "规则：\n"
    "1. 取值是别的实体（接口编码、系统名、产品号、合同名、字段名等在别处独立存在的东西）"
    "→ relation，给出谓词与目标类；取值是数值/状态/描述文字 → attribute；无业务语义 → ignore；\n"
    "2. 同一列的取值样例里既有编码又有描述时，看多数：引用列选 relation；\n"
    "3. subject_class / target_class 必须逐字取自类清单；谓词用中文动词短语（2-4 字），"
    "【关系清单】里已有贴合的优先沿用，没有才在新 relations 里声明；\n"
    "4. 拿不准的列给 ignore，宁缺勿滥。\n"
    "只输出一个 JSON 对象，结构严格遵循给定的 JSON Schema。"
)

_MAP_SAMPLE_VALUES = 5      # 每列取值样例数
_MAP_SAMPLE_CHARS = 24      # 单个样例截断


def _signature_material(sig: str, sample_rows: list[list[str]],
                        sample_values: dict[str, list[str]]) -> str:
    lines = [f"【模式签名】{sig[:400]}"]
    if sample_values:
        lines.append("【列取值样例】")
        for col, vals in sample_values.items():
            sample = "、".join(v[:_MAP_SAMPLE_CHARS] for v in vals[:_MAP_SAMPLE_VALUES])
            lines.append(f"- {col}：{sample}")
    if sample_rows:
        lines.append("【数据行样例】")
        for r in sample_rows[:2]:
            lines.append("  | " + " | ".join(c[:_MAP_SAMPLE_CHARS] for c in r[:12]))
    return "\n".join(lines)


def generate_structure_maps(prepared: list, classes: list[str],
                            obj_props: dict[str, dict],
                            llm_call: Callable, model_name: str,
                            use_cache: bool = True,
                            cancel_cb: Optional[Callable] = None,
                            existing_maps: Optional[list[dict]] = None,
                            material_limit: int = 12000,
                            progress_cb: Optional[Callable[[int, int], None]] = None,
                            ) -> tuple[list[dict], int]:
    """schema 阶段的结构映射生成：每个签名一次 LLM 决策（缓存）。

    返回 (maps, generated_n)。maps 元素：
    {signature, subject_class, key_column, columns: [{column,kind,predicate?,target_class?,attribute_name?}]}
    调用失败/超限的签名跳过（该模式回落 LLM 逐片抽取，不阻断）。
    progress_cb(done, total)：按签名上报进度，供前端显示“结构映射 3/6”。
    """
    from app.adapters.extraction import _cache_get, _cache_key, _cache_put

    def _notify(done: int, total: int) -> None:
        # 不吞异常：回调里的取消检查必须向上传播
        if progress_cb is not None:
            progress_cb(done, total)

    have = {str(m.get("signature") or "") for m in (existing_maps or [])}
    prepared = _normalize_chunks(prepared)
    counts = detect_patterns(prepared)
    # 排序保证确定性（频次降序 → 签名序）
    sigs = sorted(counts, key=lambda s: (-counts[s], s))
    sigs = [s for s in sigs if s not in have][:MAX_STRUCTURE_MAPS]
    if not sigs:
        _notify(0, 0)
        return list(existing_maps or []), 0

    class_list_text = "\n".join(f"- {c}" for c in classes[:150])
    rel_list_text = "\n".join(
        f"- {v.get('domain') or '?'} -[{v.get('label')}]-> {v.get('range') or '?'}"
        for v in list(obj_props.values())[:80]) or "（无）"

    maps = list(existing_maps or [])
    generated = 0
    for sig_i, sig in enumerate(sigs):
        _notify(sig_i, len(sigs))
        if cancel_cb is not None:
            cancel_cb()
        # 采样：该签名下的取值样例与数据行
        sample_values: dict[str, list[str]] = {}
        sample_rows: list[list[str]] = []
        for idx, text, _doc, hdr in prepared:
            cur_sig = str(hdr or "").strip() or table_signature(text)
            if cur_sig != sig or len(sample_rows) >= 4:
                continue
            for header, rows in parse_md_tables(text):
                if "|".join(header) != sig and header != sig.split("|"):
                    continue
                if not sample_rows:
                    sample_rows = rows[:2]
                for ci, col in enumerate(header):
                    bucket = sample_values.setdefault(col, [])
                    for row in rows:
                        if ci < len(row) and row[ci].strip():
                            bucket.append(row[ci].strip())
                            if len(bucket) >= _MAP_SAMPLE_VALUES:
                                break
        # 签名是数据行而非表头（无表头表格，docling 首行即数据）：签名单元格多为
        # 长自由文本 → 列语义无法可靠映射，丢弃该签名（续行靠 meta 真表头的映射展开）
        sig_cell_list = [c.strip() for c in sig.split("|") if c.strip()]
        if sig_cell_list and sum(1 for c in sig_cell_list if len(c) >= 10) > len(sig_cell_list) / 2:
            continue
        material = _signature_material(sig, sample_rows, sample_values)
        user = (material + "\n\n【类清单】\n" + class_list_text
                + "\n\n【关系清单】\n" + rel_list_text)
        key = _cache_key(model_name, STRUCTURE_MAP_SYSTEM_PROMPT, user)
        payload = _cache_get(key) if use_cache else None
        if payload is None:
            feedback = ""
            for _attempt in range(2):
                if cancel_cb is not None:
                    cancel_cb()
                try:
                    raw = llm_call(STRUCTURE_MAP_SYSTEM_PROMPT,
                                   user + (f"\n\n【上次输出错误，必须修正】\n{feedback}"
                                           if feedback else ""),
                                   STRUCTURE_MAP_JSON_SCHEMA)
                except Exception:  # noqa: BLE001
                    raw = None
                ok = isinstance(raw, dict) and isinstance(raw.get("columns"), list) \
                    and str(raw.get("subject_class") or "").strip()
                if ok:
                    payload = raw
                    break
                feedback = "输出必须含 subject_class 与 columns 数组"
            if payload is None:
                continue
            if use_cache:
                _cache_put(key, payload)
        # 规范化 + 校验（subject_class 必须在类清单里，谓词/target 逐字收敛）
        subject = str(payload.get("subject_class") or "").strip()
        if subject not in classes:
            continue
        cols = []
        for c in (payload.get("columns") or []):
            if not isinstance(c, dict):
                continue
            kind = str(c.get("kind") or "ignore")
            entry: dict[str, Any] = {"column": str(c.get("column") or "").strip(),
                                     "kind": kind}
            if kind == "relation":
                if str(c.get("predicate") or "").strip() and str(c.get("target_class") or "").strip() in classes:
                    entry["predicate"] = str(c["predicate"]).strip()
                    entry["target_class"] = str(c["target_class"]).strip()
                else:
                    entry["kind"] = "ignore"   # 目标类不在清单 → 降级，不硬连
            elif kind == "attribute":
                entry["attribute_name"] = str(c.get("attribute_name") or c.get("column") or "").strip()
            if entry["kind"] != "ignore" and entry["column"]:
                cols.append(entry)
        if not cols:
            continue
        maps.append({"signature": sig, "subject_class": subject,
                     "key_column": str(payload.get("key_column") or "").strip(),
                     "columns": cols})
        generated += 1
        if sum(len(m.get("columns") or []) for m in maps) > material_limit // 50:
            break  # 映射总量防护（极端多签名场景）
    _notify(len(sigs), len(sigs))
    return maps, generated


class EntityRegistry:
    """自然键实体登记表（外部 get_* 模式）：同键必同例，提及必可连边。"""

    def __init__(self):
        self.by_key: dict[tuple[str, str], dict] = {}

    def key(self, label: str, class_label: str) -> tuple[str, str]:
        return (normalize_label(str(label or "").strip()),
                normalize_label(str(class_label or "").strip()))

    def get_or_create(self, label: str, class_label: str, props: Optional[dict] = None,
                      evidence: str = "", chunk_index: int = 0) -> tuple[dict, bool]:
        k = self.key(label, class_label)
        if not k[0] or not k[1]:
            raise ValueError("label/class_label 不能为空")
        hit = self.by_key.get(k)
        if hit is not None:
            # 属性并集（后到的补空缺）
            for kk, vv in (props or {}).items():
                if kk not in hit["props"] or (not hit["props"].get(kk) and vv):
                    hit["props"][kk] = vv
            if evidence and not hit.get("evidence"):
                hit["evidence"] = evidence
            return hit, False
        ent = {"label": str(label).strip(), "class_label": str(class_label).strip(),
               "props": dict(props or {}), "confidence": EXPANSION_CONFIDENCE,
               "evidence": evidence, "chunk_index": chunk_index}
        self.by_key[k] = ent
        return ent, True


# ── 确定性展开 ──

def _clean_cell(v: str) -> str:
    v = str(v or "").strip()
    return "" if v in SENTINEL_VALUES else v


def _split_multi(v: str) -> list[str]:
    return [t.strip() for t in re.split(r"[;；,，、/\n]", v) if t.strip()
            and t.strip() not in SENTINEL_VALUES]


def expand_chunk(chunk_index: int, text: str, doc: str, table_header: str,
                 maps: list[dict], registry: EntityRegistry) -> tuple[dict, dict]:
    """按映射确定性展开单个切片。返回 ({entities: n, relations: n}, orphans 统计)。

    产物直接写进 registry（含行主体与引用目标）。展开产物在调用方统一转
    ExtractedEntity/Relation 后与 LLM 结果合并去重。
    """
    by_sig = {m["signature"]: m for m in maps}
    stats = {"entities": 0, "relations": 0, "rows": 0, "clauses": 0}
    sig = _norm_signature(table_header) or table_signature(text)
    mapping = by_sig.get(sig) if sig else None
    if mapping is None:
        # 列数回退：续行切片 meta 表头为空、首行是数据行——同结构表格（同列数）
        # 挂到同列数的映射上，首行一并当数据行展开。多候选取签名最长者（最具体）。
        tables0 = parse_md_tables(text)
        if tables0:
            n_cols = len(tables0[0][0])
            cands = [m for m in maps if len(m.get("signature").split("|")) == n_cols]
            if cands:
                mapping = max(cands, key=lambda m: len(m.get("signature")))
                sig = mapping["signature"]

    # ── 表格型：逐行展开 ──
    if mapping:
        subject_class = mapping["subject_class"]
        col_map = {c["column"]: c for c in mapping.get("columns") or []}
        sig_cells = sig.split("|")
        for header, rows in parse_md_tables(text):
            joined = "|".join(header)
            if joined == sig:
                col_names = header  # 真表头：rows 即数据行
            elif len(header) == len(sig_cells):
                # 续行切片：表格被切片切开、首行是数据行（meta 表头才是真表头）——
                # 列名必须取自映射签名（首行是值，不是列名！），首行一并当数据行
                col_names = sig_cells
                rows = [header] + rows
            else:
                continue  # 只展开映射命中的表
            for row in rows:
                stats["rows"] += 1
                # 行主体：键列优先，否则首个非哨兵值
                subj_label = ""
                if mapping.get("key_column"):
                    ci = col_names.index(mapping["key_column"]) \
                        if mapping["key_column"] in col_names else -1
                    if ci >= 0:
                        subj_label = _clean_cell(row[ci] if ci < len(row) else "")
                if not subj_label:
                    for ci, col in enumerate(col_names):
                        cm = col_map.get(col)
                        if cm and cm["kind"] == "attribute" and ci < len(row) \
                                and _clean_cell(row[ci]):
                            subj_label = _clean_cell(row[ci])
                            break
                if not subj_label:
                    continue
                # 伪个体防御：label 与类同名跳过（与闸门口径一致）
                if normalize_label(subj_label) == normalize_label(subject_class):
                    continue
                row_props: dict[str, str] = {}
                subj, created = registry.get_or_create(
                    subj_label, subject_class, props=row_props,
                    evidence=f"{doc} 行：{' | '.join(c[:40] for c in row[:8])}",
                    chunk_index=chunk_index)
                stats["entities"] += 1 if created else 0
                for ci, col in enumerate(col_names):
                    cm = col_map.get(col)
                    if cm is None or ci >= len(row):
                        continue
                    raw = _clean_cell(row[ci])
                    if not raw:
                        continue
                    if cm["kind"] == "attribute":
                        name = cm.get("attribute_name") or col
                        if name not in subj["props"] or not subj["props"].get(name):
                            subj["props"][name] = raw
                    elif cm["kind"] == "relation":
                        pred = cm.get("predicate") or ""
                        tclass = cm.get("target_class") or ""
                        if not pred or not tclass:
                            continue
                        for tok in _split_multi(raw):
                            tgt, tgt_created = registry.get_or_create(
                                tok, tclass, evidence=f"{doc} {col}={tok[:60]}",
                                chunk_index=chunk_index)
                            stats["entities"] += 1 if tgt_created else 0
                            # 多值单元格：引用目标若像编码/多值，登记表记为属性以便溯源
                            if normalize_label(tok) == normalize_label(tclass):
                                continue  # 伪个体防御
                            rel = {"subject_label": subj["label"],
                                   "predicate": pred,
                                   "object_label": tgt["label"],
                                   "confidence": EXPANSION_CONFIDENCE,
                                   "evidence": f"{doc} 行值：{col}={raw[:80]}",
                                   "chunk_index": chunk_index}
                            rel["_expansion"] = True
                            if registry.setdefault_rel(rel):
                                stats["relations"] += 1
        return stats, {}

    # ── 条款型：逐条实例化（编号=自然键，正文=属性，引用列映射为 relation 的情形
    #    由映射给出；无映射时只做主体实例化，关系仍由 LLM 通道负责）──
    clause_map = next((m for m in maps if m.get("signature") == "clause"), None)
    if clause_map and detect_clause_style(text):
        subject_class = clause_map["subject_class"]
        col_map = {c["column"]: c for c in clause_map.get("columns") or []}
        cur_no, cur_body = "", []
        clauses: list[tuple[str, str]] = []

        def _flush():
            if cur_no and cur_body:
                clauses.append((cur_no, "\n".join(cur_body).strip()))

        for line in text.split("\n"):
            m_ = _CLAUSE_SPLIT_RE.match(line.strip())
            if m_:
                _flush()
                cur_no, cur_body = m_.group(1).strip().lstrip("#").strip(), [m_.group(2)]
            elif cur_no:
                cur_body.append(line.rstrip())
        _flush()
        for no, body in clauses:
            if not body:
                continue
            label = no[:80]
            if normalize_label(label) == normalize_label(subject_class):
                continue
            props = {"条款号": no[:60], "条款正文": body[:1000]}
            ent, created = registry.get_or_create(
                label, subject_class, props=props,
                evidence=body[:200], chunk_index=chunk_index)
            stats["entities"] += 1 if created else 0
            stats["clauses"] += 1
            # 条款正文里映射声明的引用（正则二遍：值样例 = 正文 token）
            for cm in col_map.values():
                if cm.get("kind") != "relation":
                    continue
                pred, tclass = cm.get("predicate") or "", cm.get("target_class") or ""
                if not pred or not tclass:
                    continue
                for tok in _split_multi(body[:400]):
                    if len(tok) < 2 or len(tok) > 40:
                        continue
                    tgt, tgt_created = registry.get_or_create(
                        tok, tclass, evidence=body[:160], chunk_index=chunk_index)
                    stats["entities"] += 1 if tgt_created else 0
                    rel = {"subject_label": ent["label"], "predicate": pred,
                           "object_label": tgt["label"],
                           "confidence": EXPANSION_CONFIDENCE,
                           "evidence": f"{no}：{body[:80]}", "chunk_index": chunk_index}
                    rel["_expansion"] = True
                    if registry.setdefault_rel(rel):
                        stats["relations"] += 1
                    break  # 每条款每谓词只连首例（防正文长串刷边）
        return stats, {}

    return stats, {}  # 叙事型：原样走 LLM


def _registry_relations_impl(registry: EntityRegistry) -> list[dict]:
    return list(getattr(registry, "_rels", {}).values())


def expand_all(prepared: list, maps: list[dict]) -> tuple[EntityRegistry, dict]:
    """全切片确定性展开。返回 (registry, stats)。

    registry.entities() → 展开实体列表；registry.relations() → 展开关系列表
    （同 (s,p,o) 自动去重）。没有任何映射命中时 stats 全 0（txt/纯叙事文档零开销）。
    """
    registry = EntityRegistry()
    registry._rels: dict[tuple[str, str, str], dict] = {}

    def _setdefault_rel(rel: dict) -> bool:
        key = (normalize_label(rel["subject_label"]),
               normalize_label(rel["predicate"]),
               normalize_label(rel["object_label"]))
        if key in registry._rels:
            return False
        registry._rels[key] = rel
        return True

    registry.setdefault_rel = _setdefault_rel  # type: ignore[attr-defined]
    totals = {"entities": 0, "relations": 0, "rows": 0, "clauses": 0, "chunks": 0}
    if not maps:
        return registry, totals
    for idx, text, doc, hdr in _normalize_chunks(prepared):
        st, _orph = expand_chunk(idx, text, doc, hdr, maps, registry)
        if st.get("rows") or st.get("clauses"):
            totals["chunks"] += 1
        for k in ("entities", "relations", "rows", "clauses"):
            totals[k] += st.get(k, 0)
    return registry, totals


def registry_entities(registry: EntityRegistry) -> list[dict]:
    return list(registry.by_key.values())


def registry_relations(registry: EntityRegistry) -> list[dict]:
    return list(getattr(registry, "_rels", {}).values())
