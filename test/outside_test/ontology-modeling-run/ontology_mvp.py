# -*- coding: utf-8 -*-
"""
独立可运行的本体构建 MVP 模块（声明式建模 + 确定性展开架构）。

用法：
  python ontology_mvp.py                          # 默认测 普惠功能梳理-.xlsx
  python ontology_mvp.py --files a.xlsx b.docx c.pdf   # 多文件多格式
配置：model.local.json（与 model_build_test.py 同一份；api_key 留空回退平台 config_id）。
  新增键：max_input_chars（单次 LLM 输入上限，默认 12000 字符 ≈6K tok，20K 上下文模型安全）。

架构（对齐外部 skill 的 harness 能力，平台可平移）：
  A 解析   —— 代码直读原始文件（xlsx 合并单元格感知 / docx / pdf / txt / md / csv / html / json / pptx）
  B 研判   —— 1 次小调用
  C 建模   —— 每表 1 次小调用：LLM 只声明 table_maps（列→谓词映射工件），只看表头+样本行
  D 展开   —— 代码逐行确定性展开全部数据行（实例/属性/关系/矩阵边/规则），0 LLM
  E 叙事   —— 非表格文本分批 map-reduce（每批 ≤ max_input_chars/2，交付节奏协议）
  F 专家   —— 只评审类/关系/规则（不携带实例，预算减半），追加式补丁
  G 公理   —— 代码 domain/range + 1 次小调用；TTL + owlrl 一致性
  H 落地   —— 平台新项目 + graph_data + Neo4j + 检索冒烟
每阶段计时；结束打印分阶段耗时报表 + 行覆盖率 + 各阶段单次最大输入（换 20K 模型前先看它）。
"""
import sys, os, time, json, hashlib, re, argparse

BK = "/home/lenovo/Documents/PythonProject/OntologySystem/src/backend"
sys.path.insert(0, BK)
SKILL = "/home/lenovo/Documents/PythonProject/OntologySystem/test/outside_test/ontology-modeling/scripts"
sys.path.insert(0, SKILL)
HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument("--config", default=os.path.join(HERE, "model.local.json"))
ap.add_argument("--files", nargs="+",
                default=["/home/lenovo/Downloads/普惠功能梳理-.xlsx"])
ap.add_argument("--out", default=os.path.join(HERE, "mvp_result"))
ap.add_argument("--model"); ap.add_argument("--base-url"); ap.add_argument("--api-key"); ap.add_argument("--timeout", type=float)
args = ap.parse_args()

cfg = json.load(open(args.config)) if os.path.exists(args.config) else {}
cfg.update({k: v for k, v in (("model_name", args.model), ("base_url", args.base_url),
                              ("api_key", args.api_key), ("llm_timeout", args.timeout)) if v})
MAX_IN = int(cfg.get("max_input_chars") or 12000)   # 20K 上下文模型的安全线（≈6K tok）
OUT = os.path.abspath(args.out or "mvp_result")   # H 段会 chdir 到 BK，此后相对路径全失效
os.makedirs(OUT, exist_ok=True)
SENTINEL = {"无", "/", "-", "", "—", "N/A", "na"}

def _codeish(v):
    """字母+数字混合的编码 token（201A/401L/C2504050），纯数字与纯字母不算。"""
    return bool(re.match(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{3,8}$", str(v or "").strip()))

_SHAPE_CAP = 1200   # 形态展开的实例总量软上限（防大矩阵/超长文档失控）

TIME = {"A_parse": 0.0, "B_scan": 0.0, "C_schema": 0.0, "D_expand": 0.0,
        "E_narrative": 0.0, "F_expert": 0.0, "F2_check": 0.0, "F5_resolve": 0.0,
        "G_axiom": 0.0, "H_land": 0.0}
IO = {"calls": 0, "secs": 0.0, "in": 0, "stages": {}}
LOG_TXT = os.path.join(OUT, "build_log.txt")
def _log(stage, direction, content, **extra):
    with open(LOG_TXT, "a") as f:
        f.write(f"\n{'='*70}\n[{time.time()-T0:.1f}s] {stage} · {direction} "
                f"{' '.join(f'{k}={v}' for k,v in extra.items())}\n{'='*70}\n{str(content)[:20000]}\n")

# ───── LLM 客户端（与 model_build_test.py 同配置协议） ─────
from app.infrastructure.llm_client import LLMClient
client, call_ = None, None
if cfg.get("api_key") and cfg.get("base_url") and cfg.get("model_name"):
    client = LLMClient(api_key=cfg["api_key"], base_url=cfg["base_url"], model=cfg["model_name"],
                       think_mode="disabled" if cfg.get("disable_think") else None,
                       streaming_enabled=cfg.get("streaming_enabled"))
    model_name = cfg["model_name"]; llm_timeout = float(cfg.get("llm_timeout") or 300)
    max_retries = int(cfg.get("max_retries") or 2)
    print(f"[cfg] 本地配置模型: {model_name} @ {cfg['base_url']}（timeout={llm_timeout}, 输入上限 {MAX_IN} 字符）")
else:
    from app.adapters.extraction import _default_llm_call
    model_name, call_ = _default_llm_call(None, cfg.get("config_id_fallback") or 11)
    llm_timeout = float(cfg.get("llm_timeout") or 300); max_retries = int(cfg.get("max_retries") or 2)
    print(f"[cfg] 回退平台 config_id={cfg.get('config_id_fallback')}（{model_name}），输入上限 {MAX_IN} 字符")

from concurrent.futures import ThreadPoolExecutor
import threading
_IO_LOCK = threading.Lock()
_LLM_CONC = max(1, int(cfg.get("llm_concurrency") or 4))   # 并发度：护栏=网关并发配额（对齐平台 LLM_MAX_RPM 思想）

T0 = time.time()
def ask(stage, system, user, schema=None):
    assert len(user) <= MAX_IN, f"{stage} 输入 {len(user)}B 超预算 {MAX_IN}（拆批）"
    _log(stage, "request", user, in_bytes=len(user))
    t = time.time()
    if call_ is not None:
        out = call_(system, user, schema)
    else:
        out = client.call_llm(system, user, max_retries=max_retries, json_schema=schema, timeout=llm_timeout)
    if isinstance(out, dict) and isinstance(out.get("content"), str):
        from app.adapters.extraction import _loads_loose
        parsed = _loads_loose(out["content"])
        if parsed is None and len(out.get("content") or "") > 200:
            cont = client.call_llm(system, user + "\n\n【输出被截断】从中断处继续输出剩余 JSON。",
                                   max_retries=1, json_schema=schema, timeout=llm_timeout)
            tail = (cont or {}).get("content") if isinstance(cont, dict) else None
            parsed = _loads_loose(tail or "") or _loads_loose(out["content"])
        out = parsed or {"content": out.get("content")}
    dt = time.time() - t
    with _IO_LOCK:
        IO["calls"] += 1; IO["secs"] += dt; IO["in"] += len(user)
        IO["stages"].setdefault(stage, {"n": 0, "secs": 0.0, "in_max": 0})
        IO["stages"][stage]["n"] += 1; IO["stages"][stage]["secs"] += dt
        IO["stages"][stage]["in_max"] = max(IO["stages"][stage]["in_max"], len(user))
    _log(stage, "response", json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else out,
         secs=round(dt, 1))
    return out if isinstance(out, dict) else {}

def _uniq(seq, kf):
    seen, out = set(), []
    for it in seq:
        k = kf(it)
        if k not in seen:
            seen.add(k); out.append(it)
    return out

# ═══════════════ A 解析（代码直读原始文件，0 LLM） ═══════════════
def _fill_merged(ws):
    """合并单元格感知网格：左上值回填整个合并区。"""
    grid = [[c for c in row] for row in ws.iter_rows(values_only=True)]
    for rng in ws.merged_cells.ranges:
        tl = grid[rng.min_row - 1][rng.min_col - 1]
        for r in range(rng.min_row - 1, rng.max_row):
            for c in range(rng.min_col - 1, rng.max_col):
                if r < len(grid) and c < len(grid[r]):
                    grid[r][c] = tl
    return grid

def _rows_from_grid(grid):
    rows = [[("" if v is None else str(v).strip()) for v in r] for r in grid]
    rows = [r for r in rows if any(x for x in r)]
    hdr_i = next((i for i, r in enumerate(rows) if sum(1 for x in r if x) >= 2), 0)
    return rows[hdr_i:], hdr_i

def parse_excel(path):
    ext = os.path.splitext(path)[1].lower()
    sheets = []
    if ext == ".xls":   # 老 BIFF 格式：openpyxl 读不了，走 xlrd（合并单元格同样回填）
        import xlrd
        wb = xlrd.open_workbook(path, formatting_info=True)
        for ws in wb.sheets():
            grid = [[ws.cell_value(r, c) for c in range(ws.ncols)] for r in range(ws.nrows)]
            for (r0, c0, r1, c1) in (ws.merged_cells or []):   # (rlo, clo, rhi_exclusive, chi_exclusive)
                tl = grid[r0][c0] if r0 < len(grid) and c0 < len(grid[r0]) else ""
                for r in range(r0, min(r1, len(grid))):
                    for c in range(c0, min(c1, len(grid[r]) if r < len(grid) else 0)):
                        grid[r][c] = tl
            rows, hdr_i = _rows_from_grid(grid)
            if not rows:
                continue
            sheets.append({"name": ws.name, "headers": [h for h in rows[0]],
                           "rows": rows[1:]})
        return {"kind": "excel(xls)", "sheets": sheets, "narrative": []}
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        grid = _fill_merged(ws)
        rows, hdr_i = _rows_from_grid(grid)
        if not rows:
            continue
        sheets.append({"name": ws.title, "headers": [h for h in rows[0]],
                       "rows": rows[1:]})
    return {"kind": "excel", "sheets": sheets, "narrative": []}

def parse_csv(path):
    import csv as _csv
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        rows = list(_csv.reader(f))
    rows = [r for r in rows if any(x.strip() for x in r)]
    return {"kind": "csv", "sheets": [{"name": os.path.basename(path), "headers": rows[0], "rows": rows[1:]}],
            "narrative": []} if rows else {"kind": "csv", "sheets": [], "narrative": []}

def parse_docx(path):
    from docx import Document
    d = Document(path)
    paras = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    sheets = []
    for ti, t in enumerate(d.tables, 1):
        rows = [[c.text.strip() for c in r.cells] for r in t.rows]
        rows = [r for r in rows if any(x for x in r)]
        if len(rows) >= 2:
            sheets.append({"name": f"{os.path.basename(path)}-表{ti}", "headers": rows[0], "rows": rows[1:]})
    return {"kind": "docx", "sheets": sheets, "narrative": paras}

def parse_pdf(path):
    import fitz
    from collections import Counter as _Cp
    doc = fitz.open(path)
    # 页眉/页脚/水印指纹去重（通用）：跨页重复出现的高频行剔除——
    # 理财说明书/制度 PDF 每页带"风险提示+第N页共M页"，不清会污染全部叙述行
    n_pages = len(doc)
    line_cnt = _Cp()
    page_lines = []
    for p_ in doc:
        ls = [t.strip() for t in p_.get_text().split("\n") if t.strip()]
        page_lines.append(ls)
        for l_ in set(ls):
            line_cnt[l_] += 1
    drop = {l_ for l_, c_ in line_cnt.items()
            if c_ >= max(3, int(n_pages * 0.6)) and len(l_) <= 60}
    text_lines = []
    for ls in page_lines:
        text_lines.extend([l_ for l_ in ls if l_ not in drop])
    if drop:
        print(f"    [pdf] 页眉/页脚指纹剔除 {len(drop)} 种重复行（共 {n_pages} 页）")
    return {"kind": "pdf", "sheets": [], "narrative": text_lines,
            "meta": {"pages": n_pages, "dropped_headers": len(drop)}}

def parse_pptx(path):
    from pptx import Presentation
    prs = Presentation(path)
    lines = []
    for i, slide in enumerate(prs.slides, 1):
        lines.append(f"## 幻灯片{i}")
        for sh in slide.shapes:
            if sh.has_text_frame:
                lines += [p.text.strip() for p in sh.text_frame.paragraphs if p.text.strip()]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            nt = slide.notes_slide.notes_text_frame.text.strip()
            if nt:
                lines.append(f"[备注] {nt}")
    return {"kind": "pptx", "sheets": [], "narrative": lines}

def parse_textish(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    if path.endswith(".json"):
        try:
            raw = json.dumps(json.loads(raw), ensure_ascii=False, indent=1)
        except Exception:
            pass
    return {"kind": os.path.splitext(path)[1].lstrip("."), "sheets": [],
            "narrative": [l for l in raw.splitlines() if l.strip()]}

def parse_doc(path):
    """老 .doc（CDF，含 WPS 生成）：libreoffice 无头转 docx 保表格结构，失败回退 antiword 纯文本。
    转换产物缓存到 HOME 下 .doc_cache（snap 版 libreoffice 的 /tmp 是私有沙箱，产物写 /tmp 会消失）。"""
    import subprocess
    cache = os.path.join(HERE, ".doc_cache")
    os.makedirs(cache, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    conv = os.path.join(cache, stem + ".docx")
    if not (os.path.exists(conv) and os.path.getsize(conv) > 1000):
        for cmd in (["libreoffice", "--headless"], ["soffice", "--headless"]):
            try:
                subprocess.run(cmd + ["--convert-to", "docx", "--outdir", cache, path],
                               capture_output=True, timeout=300)
                if os.path.exists(conv) and os.path.getsize(conv) > 1000:
                    break
            except Exception:
                continue
    if os.path.exists(conv) and os.path.getsize(conv) > 1000:
        d = parse_docx(conv)
        d["kind"] = "doc(libreoffice)"
        return d
    try:
        txt = subprocess.run(["antiword", path], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
        if txt.strip():
            return {"kind": "doc(antiword)", "sheets": [],
                    "narrative": [l for l in txt.splitlines() if l.strip()]}
    except Exception:
        pass
    return {"kind": "doc", "sheets": [], "narrative": []}

def parse_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".doc":
        return parse_doc(path)
    if ext in (".xlsx", ".xls"):
        try:
            return parse_excel(path)
        except Exception:
            return parse_csv(path) if ext == ".csv" else {"kind": ext, "sheets": [], "narrative": [f"[解析失败 {path}]"]}
    if ext == ".csv":
        return parse_csv(path)
    if ext == ".docx":
        return parse_docx(path)
    if ext == ".pdf":
        return parse_pdf(path)
    if ext == ".pptx":
        return parse_pptx(path)
    return parse_textish(path)

t = time.time()
docs = []
for fp in args.files:
    assert os.path.exists(fp), f"文件不存在: {fp}"
    d = parse_any(fp)
    d["file"] = os.path.basename(fp)
    if not d["sheets"] and not d["narrative"]:
        print(f"[A 解析] ⚠️ {d['file']} 解析出 0 内容（可能是伪格式/纯图片/损坏文件）")
    docs.append(d)
    ntab = sum(len(s["rows"]) for s in d["sheets"])
    print(f"[A 解析] {d['file']}（{d['kind']}）：表格 {len(d['sheets'])} 张/{ntab} 数据行，叙述行 {len(d['narrative'])}")
TIME["A_parse"] = time.time() - t

# ═══════════════ B 研判（1 次小调用） ═══════════════
t = time.time()
doc_stats = [{"file": d["file"], "kind": d["kind"],
              "tables": [{"sheet": s["name"], "rows": len(s["rows"]), "cols": len(s["headers"])} for s in d["sheets"]],
              "narrative_lines": len(d["narrative"])} for d in docs]
r1 = ask("研判", "你是本体应用分析师。三问自检+排除法研判，只输出 JSON。",
         "文档清单：" + json.dumps(doc_stats, ensure_ascii=False)[:2000]
         + "\n输出 {three_questions:[{q,yes,reason}], verdict, scope}")
print(f"[B 研判] {r1.get('verdict')}（{TIME.get('B_scan',0):.0f}s）")
TIME["B_scan"] = time.time() - t

# ═══════════════ C 建模（每表 1 次声明调用，输入=表头+样本行） ═══════════════
SCHEMA_SYS = ("你是本体建模师。为表格声明【列→谓词映射工件】table_map，代码将据此逐行确定性展开。只输出 JSON：\n"
              "{subject_class:\"行主体类名2-6字\", key_column:\"键列列名(行的唯一主体，如场景按钮/业务场景)\",\n"
              " columns:[{column:\"列名\", kind:\"cell\"|\"relation\", predicate:\"谓词2-4字\", target_class:\"relation目标类\"}],\n"
              " matrix_target_class:\"列头是编码/产品码的矩阵表填目标类(如 产品)，否则 null\", matrix_predicate:\"矩阵列谓词\"}\n"
              "铁律：column 必须逐字用表头列名；接口/系统/人员等独立事物列用 kind=relation；"
              "纯描述/条件列用 cell；矩阵表（大量编码列头）声明 matrix_target_class 让代码逐格建边。")
def _sample_rows(headers, rows, n=6):
    """样本行：取键列候选值最丰富的 n 行，单元格截 60。"""
    picked, seen = [], set()
    for r in sorted(rows, key=lambda x: -sum(1 for c in x if c and c not in SENTINEL)):
        key = "|".join(str(c)[:12] for c in r[:3])
        if key in seen:
            continue
        seen.add(key)
        picked.append([str(c)[:60] for c in r])
        if len(picked) >= n:
            break
    return picked

def declare_map(sheet):
    user = (f"表名：{sheet['name']}\n表头：{json.dumps(sheet['headers'], ensure_ascii=False)}\n"
            f"样本行：{json.dumps(_sample_rows(sheet['headers'], sheet['rows']), ensure_ascii=False)}")
    m = ask("建模", SCHEMA_SYS, user)
    hdr = sheet["headers"]
    # 矩阵自检：表头里 ≥5 个编码形列但模型没声明 matrix_target_class → 带提示重声明一次
    code_like = [h for h in hdr if re.match(r"^[0-9A-Za-z]{3,8}$", str(h) or "")]
    if len(code_like) >= 5 and not str(m.get("matrix_target_class") or "").strip():
        m2 = ask("建模", SCHEMA_SYS, user[:4000]
                 + f"\n【矩阵表提示】该表有 {len(code_like)} 个编码形列头（如 {code_like[:5]}），"
                   "这是矩阵表：请为编码列声明 matrix_target_class（编码实体的类名）与 matrix_predicate，"
                   "键列选前几个无表头/文本列中的行主体列。重新声明。")
        if isinstance(m2, dict) and str(m2.get("matrix_target_class") or "").strip():
            m = m2
    for attempt in range(2):   # 干跑校验：列名对不上就带报错重声明（harness 自检回路的轻量版）
        cols = [c for c in (m.get("columns") or []) if isinstance(c, dict)
                and str(c.get("column") or "").strip() in hdr]
        if len(cols) >= 2 and str(m.get("subject_class") or "").strip():
            break
        m2 = ask("建模", SCHEMA_SYS, user[:4000]
                 + f"\n【上次声明被拒】columns 的 column 必须逐字等于表头：{json.dumps(hdr, ensure_ascii=False)}。重新声明。")
        if isinstance(m2, dict) and (m2.get("columns") or m2.get("subject_class")):
            m = m2
    return m if isinstance(m, dict) else {}

t = time.time()
GROUP_CAP = int(cfg.get("declare_group_cap") or 12)
# 同签名表共享声明：表头结构（规范化非空表头序列）相同的表只声明一次——
# "每接口一张详情表"式的工分组工作簿 154 张表不必逐张问模型（通用机制，不依赖业务词表）
def _sig(s):
    hs = tuple(str(h).strip() for h in s["headers"] if str(h).strip())[:20]
    return hs if hs else ("<空表头>",)
_groups = {}
for d in docs:
    d["maps"] = {}
    for s in d["sheets"]:
        if len(s["rows"]) >= 1 and len(s["headers"]) >= 2:
            _groups.setdefault(_sig(s), {"rep": s, "members": []})["members"].append((d, s))
_ordered = sorted(_groups.values(),
                  key=lambda g: -sum(len(ms["rows"]) for _dd, ms in g["members"]))
_declared_n = 0
for gi, g in enumerate(_ordered):
    if gi >= GROUP_CAP:   # 声明预算外的组：不映射（覆盖表里记"无有效映射"）
        for d, s in g["members"]:
            d["maps"][s["name"]] = {}
        continue
    m = declare_map(g["rep"])
    _declared_n += 1
    for d, s in g["members"]:
        d["maps"][s["name"]] = m
TIME["C_schema"] = time.time() - t
_cov_n = sum(len(g["members"]) for g in _ordered[:GROUP_CAP])
print(f"[C 建模] {len(_groups)} 个表结构签名 → 声明 {_declared_n} 组，覆盖 {_cov_n}/"
      f"{sum(len(g['members']) for g in _ordered)} 张表（{TIME['C_schema']:.0f}s）")

# ═══════════════ D 确定性展开（代码，0 LLM） ═══════════════
card = {k: [] for k in ("classes", "relations", "instances", "links", "rules", "pending", "terms")}
INST_CAP = int(cfg.get("instance_cap") or 1600)   # 全卡实例总量软上限（防大工作簿失控）
coverage = []
def _have_inst(label, cls):
    return any(str(x.get("label") or "").strip() == label and x.get("class") == cls
               for x in card["instances"])
def _file_of(evidence):
    # 证据串格式 "文件名[sheet]行n" / "文件名…：…"——取首个 '[' 前或 '：' 前的文件名
    m = re.split(r"[\[：]", str(evidence or ""), 1)
    return m[0].strip()[:60] if m and m[0].strip() else ""
def _add_inst(label, cls, props=None, evidence=""):
    label = str(label)[:80].strip()   # 与存储口径一致，防超长标签重复实例
    if not label or not cls:
        return
    src = _file_of(evidence)
    tgt = next((x for x in card["instances"] if isinstance(x, dict)
                and str(x.get("label") or "").strip() == label and x.get("class") == cls), None)
    if tgt is not None:
        if props:
            tgt["props"].update(props)
        if src:
            tgt.setdefault("_files", [])
            if src not in tgt["_files"]:
                tgt["_files"].append(src)
        return
    if len(card["instances"]) >= INST_CAP:
        return
    x = {"label": label, "class": cls, "props": props or {}, "evidence": evidence}
    if src:
        x["_files"] = [src]
    card["instances"].append(x)

def _ensure_class(zh, definition="", src_doc=""):
    if zh and not any(str(c.get("label") or "") == zh for c in card["classes"]):
        card["classes"].append({"label": zh, "definition": definition, "properties": [],
                                **({"source_document": os.path.basename(src_doc)} if src_doc else {})})

t = time.time()
# 跨表二级表头指纹（通用）：同构 sheet 组（154 张同签名表）每张的数据区都重复着同一行
# 真列名（合并大表头下方的第二级表头）——值域被它污染后布尔/数值/复用率排除全失效（331 实锤）。
# 判据：同一"行值指纹"在 ≥3 张表重复出现 → 判定为表头行，行循环里全组跳过。不认任何业务词。
from collections import Counter as _Cfp
_fp = _Cfp()
for d in docs:
    for s in d["sheets"]:
        if not s["rows"]:
            continue
        for r0 in s["rows"][:3]:   # 二级表头通常在数据区前几行
            fp0 = tuple(str(x).strip() for x in r0 if str(x).strip() and str(x).strip() not in SENTINEL)[:14]
            if len(fp0) >= 3:
                _fp[fp0] += 1
_second_header_fps = {fp for fp, c in _fp.items() if c >= 3}
if _second_header_fps:
    print(f"[D 展开] 跨表二级表头指纹 {len(_second_header_fps)} 组（重复 ≥3 张表的数据行，整组跳过）")
for d in docs:
    for s in d["sheets"]:
        m = d["maps"].get(s["name"]) or {}
        if len(card["instances"]) >= INST_CAP:
            coverage.append({"sheet": s["name"], "rows": len(s["rows"]), "expanded": 0,
                             "note": f"实例上限{INST_CAP}停"})
            continue
        subj_cls = str(m.get("subject_class") or "").strip()
        cols_in = [c for c in (m.get("columns") or []) if isinstance(c, dict)]
        if not subj_cls or len(cols_in) < 1:
            coverage.append({"sheet": s["name"], "rows": len(s["rows"]), "expanded": 0, "note": "无有效映射"})
            continue
        hdr = s["headers"]
        # 列名→列号（同名表头按第 n 次出现顺序匹配）
        used, colmap, key_idx = {}, [], None
        kname = str(m.get("key_column") or "").strip()
        for c in cols_in:
            name = str(c.get("column") or "").strip()
            pred = str(c.get("predicate") or "").strip() or name
            kind = c.get("kind") if c.get("kind") in ("cell", "relation") else "cell"
            tc = str(c.get("target_class") or "").strip()
            cands = [i for i, h in enumerate(hdr) if h == name]
            nth = used.get(name, 0)
            hit = cands[nth] if nth < len(cands) else None
            if hit is None:
                cands2 = [i for i, h in enumerate(hdr) if name and name in str(h)]
                hit = cands2[min(nth, len(cands2) - 1)] if cands2 else None
            if hit is not None:
                used[name] = nth + 1
            if hit is None:
                continue
            if name == kname:
                key_idx = hit
            colmap.append((hit, kind, pred, tc, name))
        if not colmap:
            coverage.append({"sheet": s["name"], "rows": len(s["rows"]), "expanded": 0, "note": "列全未命中"})
            continue
        if key_idx is None:
            k2 = next((i for i, h in enumerate(hdr)
                       if any(w in str(h) for w in ("场景", "操作", "业务", "功能", "名称", "项目"))), None)
            key_idx = k2 if k2 is not None else 0
        # 矩阵升级：≥5 个同谓词 cell 列且列名像编码 → 逐格建边到 matrix_target_class
        mtc = str(m.get("matrix_target_class") or "").strip()
        mpred = str(m.get("matrix_predicate") or "适用").strip()
        # 确定性兜底：编码形列表头 ≥5 列（模型漏声明也补全），目标类缺省"编码实体"
        code_hdr_cols = [i for i, h in enumerate(hdr)
                         if i != key_idx and re.match(r"^[0-9A-Za-z]{3,8}$", str(h) or "")]
        if len(code_hdr_cols) >= 5:
            mtc = mtc or "编码实体"
            have_idx = {c5[0] for c5 in colmap}
            for i4 in code_hdr_cols:
                if i4 not in have_idx:
                    colmap.append((i4, "cell", mpred, "", hdr[i4]))
        from collections import Counter as _Cc
        pc = _Cc(p4 for (_i, k4, p4, _t4, _n4) in colmap if k4 == "cell" and _i != key_idx)
        matrix_cols = []
        if mtc:
            code_re = re.compile(r"^[0-9A-Za-z]{3,8}$")
            for (i4, k4, p4, _t4, n4) in colmap:
                if k4 == "cell" and i4 != key_idx and code_re.match(n4 or ""):
                    matrix_cols.append((i4, n4))
            if len(matrix_cols) < 3:
                matrix_cols = []
        # ── 形态判定（统计特征驱动，与业务词表/具体文档无关） ──
        # ①低基数枚举列：distinct 2-12 且值短 → 取值实体化；②长文本/规则列 → 规则原文实例+标识符抽取
        matrix_idx = {i4 for i4, _n in matrix_cols}
        enum_cols, rule_cols = {}, {}
        # 列形态档案（通用判据，认字符形态不认业务词）：
        #   bool_col  值域 ⊆ 逻辑常量（是/否/Y/N/0/1…）→ 布尔属性，永不实体化（"是"不是业务对象）
        #   num_col   值 100% 纯数字/小数/Excel 序列 → 数据属性，永不实体化（长度/日期序列/序号）
        #   reuse     distinct/非空行数 复用率 → 实体化的"类别"语义来自值复用；近似每行唯一=属性值
        _BOOL_SET = {"是", "否", "有", "无", "y", "n", "yes", "no", "true", "false",
                     "0", "1", "√", "×", "true", "false"}
        _NUM_RE = re.compile(r"^\d{1,6}(\.\d+)?$|^20\d{2}[-/]\d{1,2}[-/]\d{1,2}$")
        col_profile = {}
        for (i, kind, pred, tc, name) in colmap:
            # 档案覆盖 cell+relation 两类列：C 声明把属性列声明成实体列（tc 路径）
            # 与声明成枚举列同源，形态校验必须两边都查（328 轮实锤：relation 列漏档→残留伪类）
            if kind not in ("cell", "relation") or i == key_idx or i in matrix_idx:
                continue
            vals = [str(r2[i]).strip() for r2 in s["rows"]
                    if i < len(r2) and str(r2[i]).strip() and str(r2[i]).strip() not in SENTINEL]
            if not vals:
                continue
            distinct = set(vals)
            low = {v.lower() for v in distinct}
            prof = {"bool": low <= _BOOL_SET,
                    "num": all(_NUM_RE.match(v) for v in vals),
                    "reuse": len(distinct) / len(vals)}
            col_profile[i] = prof
            # distinct==1 的常量列也是分组维度（sheet 级常量=业务域/工作表语义），一并实体化；
            # 封闭性：至少一个值复用两次（reuse ≤ 0.6）——"上线日"4行4值/"字段名"143行143值这类
            # 每行唯一的属性值列在此被拦下，值回到数据属性位
            if 1 <= len(distinct) <= 12 and max(len(v) for v in distinct) <= 12 \
                    and not prof["bool"] and not prof["num"] \
                    and (len(distinct) == 1 or prof["reuse"] <= 0.6):
                enum_cols[i] = name or pred or "枚举值"
            elif len(distinct) > 1 and (re.search(r"规则|条件|校验", pred)
                                        or sum(len(v) for v in vals) / len(vals) >= 40):
                rule_cols[i] = (pred, name)
        unit_seen = set()   # 矩阵单元标签查重（同主体×列头多行时加行号后缀）
        fill = {}
        expanded = 0
        for ri, row in enumerate(s["rows"], 1):
            need = set([key_idx]) | {c5[0] for c5 in colmap}
            rowvals = {}
            for i in need:
                v = str(row[i]).strip() if i < len(row) else ""
                if v and v not in SENTINEL:
                    fill[i] = v
                rowvals[i] = fill.get(i, "")
            subj = rowvals.get(key_idx)
            if not subj:
                continue
            # 伪表头行过滤：分节表（输入项||返回）的真表头混在数据区——
            # 行的非空值与表头重合 ≥3 个即判定为表头行，整行跳过（防"字段名/备注/长度"列名变实例）
            _nz = [str(v) for v in rowvals.values() if v and v not in SENTINEL]
            if _nz and sum(1 for v in _nz if v in set(map(str, s["headers"]))) >= 3:
                continue
            if tuple(_nz)[:14] in _second_header_fps:
                continue
            expanded += 1
            ev = f"{d['file']}[{s['name']}]行{ri}"
            _ensure_class(subj_cls, f"{s['name']} 数据行主体")
            _add_inst(subj, subj_cls, evidence=ev)
            for i, kind, pred, tc, name in colmap:
                if i == key_idx or not rowvals.get(i):
                    continue
                if kind == "relation" and tc:
                    # C 声明的实体列降级校验：布尔/数值形态列（是否必输/长度/上线日…）的值
                    # 是数据属性，不实体化——只保留边到字面值（模型声明方差的结构防线）
                    _prof = col_profile.get(i)
                    _ensure_class(tc)
                    card["links"].append({"subject": subj, "predicate": pred, "object": rowvals[i], "evidence": ev})
                    if not (_prof and (_prof["bool"] or _prof["num"])):
                        _add_inst(rowvals[i], tc, evidence=ev)
                    if not any(str(r.get("label") or "") == pred for r in card["relations"]):
                        card["relations"].append({"label": pred, "domain": subj_cls, "range": tc})
                elif i in dict(matrix_cols):
                    # 交叉表形态：格 → 「矩阵单元」实例（对应主体/对应列头/单元取值 三向），
                    # 格子原值进 props；替代旧的 subject→列头直连边（四向查询可答）
                    _ensure_class(mtc, "矩阵列头编码实体")
                    _add_inst(name, mtc, evidence=ev)
                    if len(card["instances"]) < _SHAPE_CAP:
                        _ensure_class("矩阵单元", "交叉表单元格实例（行主体×列头×取值）")
                        ulab = f"{name}×{subj}"[:80]
                        if ulab in unit_seen:
                            ulab = f"{ulab}#{ri}"[:80]
                        unit_seen.add(ulab)
                        _add_inst(ulab, "矩阵单元", {"原值": rowvals[i][:120]}, ev)
                        card["links"].append({"subject": ulab, "predicate": "对应主体", "object": subj, "evidence": ev})
                        card["links"].append({"subject": ulab, "predicate": "对应列头", "object": name, "evidence": ev})
                        for pl, dc_, rc_ in (("对应主体", "矩阵单元", subj_cls), ("对应列头", "矩阵单元", mtc)):
                            if not any(str(r.get("label") or "") == pl for r in card["relations"]):
                                card["relations"].append({"label": pl, "domain": dc_, "range": rc_})
                        if _codeish(rowvals[i]):
                            _ensure_class("矩阵取值", "矩阵单元格中出现的编码取值（校验接口/参数码）")
                            _add_inst(rowvals[i], "矩阵取值", evidence=ev)
                            card["links"].append({"subject": ulab, "predicate": "单元取值",
                                                  "object": rowvals[i], "evidence": ev})
                            if not any(str(r.get("label") or "") == "单元取值" for r in card["relations"]):
                                card["relations"].append({"label": "单元取值", "domain": "矩阵单元", "range": "矩阵取值"})
                elif i in enum_cols:
                    # 低基数枚举形态：取值实体化 + 边（业务域/角色/状态类语义在所有文档通用）
                    if len(card["instances"]) < _SHAPE_CAP:
                        ec = enum_cols[i]
                        _ensure_class(ec, f"枚举列『{ec}』的取值类")
                        _add_inst(rowvals[i], ec, evidence=ev)
                        card["links"].append({"subject": subj, "predicate": pred, "object": rowvals[i], "evidence": ev})
                        if not any(str(r.get("label") or "") == pred for r in card["relations"]):
                            card["relations"].append({"label": pred, "domain": subj_cls, "range": ec})
                    else:
                        _add_inst(subj, subj_cls, {pred if pc.get(pred, 0) <= 1 else name: rowvals[i]}, ev)
                elif i in rule_cols:
                    # 长文本/规则形态：原文实例（不摘要）+ 受规则约束边 + 标识符→业务字段
                    if len(card["instances"]) < _SHAPE_CAP:
                        rpred, rname = rule_cols[i]
                        _ensure_class(rname, f"规则列『{rname}』的原文实例类")
                        ulab = f"{rname}·{s['name']}·{ri}"[:80]
                        _add_inst(ulab, rname, {"原文": rowvals[i][:300], "所属主体": subj}, ev)
                        card["links"].append({"subject": subj, "predicate": "受规则约束", "object": ulab, "evidence": ev})
                        if not any(str(r.get("label") or "") == "受规则约束" for r in card["relations"]):
                            card["relations"].append({"label": "受规则约束", "domain": subj_cls, "range": rname})
                        for tok in _uniq(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", rowvals[i]), lambda x: x)[:6]:
                            # 通用命名惯例判定：字段名=小写/下划线开头（camelCase/snake_case）；
                            # 大写开头是系统编码（C2504050/CTEB003），不抽业务字段
                            if tok in ("True", "False", "null", "None", "STR", "http", "https", "HTML") \
                                    or not re.match(r"^[a-z_]", tok):
                                continue
                            _ensure_class("业务字段", "规则/接口中出现的字段名或开关名")
                            _add_inst(tok, "业务字段", evidence=ev)
                            card["links"].append({"subject": ulab, "predicate": "引用字段",
                                                  "object": tok, "evidence": ev})
                        if not any(str(r.get("label") or "") == "引用字段" for r in card["relations"]):
                            card["relations"].append({"label": "引用字段", "domain": rname, "range": "业务字段"})
                    # 事理层：规则原文同步进 rules（subject=行主体，供规则形式化定位 if_class）
                    if re.search(r"规则|条件|校验", rule_cols[i][0]):
                        card["rules"].append({"condition": rowvals[i][:120],
                                              "conclusion": f"{subj} 按{rule_cols[i][0]}处理", "evidence": ev,
                                              "subject": subj})
                else:
                    _add_inst(subj, subj_cls, {pred if pc.get(pred, 0) <= 1 else name: rowvals[i]}, ev)
        expanded_now = expanded
        coverage.append({"sheet": s["name"], "rows": len(s["rows"]), "expanded": expanded_now,
                         "note": f"映射{len(colmap)}列" + (f"+矩阵{len(matrix_cols)}列" if matrix_cols else "")})
card["links"] = _uniq(card["links"], lambda l: (l.get("subject"), l.get("predicate"), l.get("object")))
card["relations"] = _uniq(card["relations"], lambda r: r.get("label"))
card["rules"] = _uniq(card["rules"], lambda r: (r.get("condition"), r.get("conclusion")))
TIME["D_expand"] = time.time() - t
tot_rows = sum(c["rows"] for c in coverage)
print(f"[D 展开] {tot_rows} 数据行 → {len(card['instances'])}实例 {len(card['links'])}边 "
      f"{len(card['rules'])}规则（{TIME['D_expand']:.2f}s，0 LLM）")
for c in coverage:
    print(f"    {c['sheet']}: {c['rows']}行 {c.get('note','')}")

# ═══════════════ E 叙事通道（分批 map-reduce，预算内） ═══════════════
NARR_SYS = ("你是本体采集工程师（29句话范式，事实-事理-行动）。从文本片段提取语义，只输出 JSON：\n"
            "{classes:[{label,definition,properties:[]}], instances:[{label,class,props:{},evidence}],\n"
            " attributes:[{subject,name,value,evidence}],"
            " links:[{subject,predicate,object,evidence}], rules:[{condition,conclusion,evidence}], pending:[]}\n"
            "铁律：绝不编造；实例逐字取自原文；类名2-6字、谓词2-4字；无据不输出。\n"
            "纪律：①规则/条款原文完整保留进 condition 或 props，不摘要不改写；"
            "②低基数分类值（状态/类型/角色/阶段等）实体化为实例并建边，不要只写在属性里；"
            "③源文未明示的归属/口径一律写进 pending，严禁脑补默认值。"
            "④类别性判定：仅能被多个不同个体实例化的概念（投资者/风险类型/资产类别）才声明为类；"
            "有具体费率/日期/编号/数值的具体安排（如某项费用、某个日期）是实例不是类——"
            "一个名字只对应一个具体对象时，声明为实例。"
            "⑤属性值分流：主体的具体取值（费率/上限/日期/金额/基准等，尤其【本条款数值要素】提示的）"
            "用 attributes 输出（subject=主体实例名，name=属性名，value=值），"
            "严禁把属性值抽成独立实例。")
def _chunks(lines, size=1000):
    buf, out = [], 0
    for l in lines:
        buf.append(l); out += len(l) + 1
        if out >= size:
            yield "\n".join(buf); buf, out = [], 0
    if buf:
        yield "\n".join(buf)

# ── 结构密度判定（通用，零 LLM）：编号密度 → 路由半结构 vs 纯散文 ──
_CLAUSE_RE = re.compile(r"^(第[一二三四五六七八九十百0-9]+[条章节]|[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[)）]|\d{1,3}[\.、](?!\d))")

def _struct_density(narr_lines):
    nz = [l.strip() for l in narr_lines if l.strip()]
    if not nz:
        return 0.0, 0
    num = [l for l in nz if _CLAUSE_RE.match(l)]
    if not num:
        return 0.0, 0
    density = min(1.0, len(num) / len(nz) * 2.5)
    return round(density, 2), len(num)

def _re_num(v):
    return bool(re.search(r"\d", str(v))) and len(str(v)) <= 24

def _clause_split(narr_lines):
    """编号条款边界切分：编号行开新条款，后续行续接；未编号块各自保留（未命中兜底不丢内容）"""
    out, cur_no, cur = [], "", []
    for l in narr_lines:
        l = l.rstrip()
        if not l:
            continue
        m = _CLAUSE_RE.match(l.strip())
        if m and len(l.strip()) > 2:
            if cur:
                out.append((cur_no, "\n".join(cur)))
            cur_no, cur = m.group(1), [l.strip()]
        else:
            cur.append(l.strip())
    if cur:
        out.append((cur_no, "\n".join(cur)))
    return out

t = time.time()
_narr_stat = []
groups = []   # 条款挂接：每片包含的条款 labels（散文通道为空——挂接自动跳过）
# 文档主体实例（通用）：该文档 evidence 出现最多的实例=叙事描述的核心主体（如理财产品）
_doc_subject = {}
for _i6 in card["instances"]:
    if isinstance(_i6, dict):
        for _f6 in (_i6.get("_files") or []):
            _doc_subject[_f6] = _i6   # 后写覆盖：最多 evidence 的文件取其主体

for d in docs:
    # 结构密度路由：半结构（条款通道——原文实例化由代码完成，LLM 只补跨条款语义）vs 纯散文（固定分片）
    density, n_clause = _struct_density(d["narrative"])
    if density >= 0.25 and n_clause >= 8:
        clauses = _clause_split(d["narrative"])
        _narr_stat.append({"file": os.path.basename(d["file"]), "mode": "clause", "density": density,
                           "clauses": len(clauses),
                           "chars": sum(len("".join(t.split())) for _, t in clauses)})
        # 条款确定性实例化（D 段哲学在文本侧复刻：原文即实例，零 LLM）；
        # 未编号块也保留（未命中兜底）——任何文本都进本体，路由不丢内容
        # 文档结构层（条款/章节）剥离出业务本体：条款原文进 card["doc_clauses"]（溯源层，
        # 随 spec_card 保存+证据指针引用），不进 classes/instances——文档骨架不是业务概念。
        # 数值属性槽（零 LLM）：条款里的「名词为/：【数值+单位】」「名词【值】」确定性抽出，
        # 拼进分片头部作为 LLM attributes 契约的值来源提示。
        if "doc_clauses" not in card:
            card["doc_clauses"] = []
        _slots_all = []    # [(no, {名:值})]——LLM attributes 的值来源
        clause_seq = []    # [(no, txt)]——分片用
        for no, txt in clauses:
            ev = f"{d['file']}[{no or '叙述'}]"
            card["doc_clauses"].append({"no": no or "", "doc": os.path.basename(d['file']), "text": txt})
            _ns = {}
            for _m in re.finditer(r"([\u4e00-\u9fa5A-Za-z]{2,12}?)(?:为|[:：是])\s*【?((?=[^】]{0,24}\d)[\d.,]+\s*(?:%|万亿|亿|万)?(?:元|天|个工作日|工作日|日|个月|月|年|笔|期|份)?[凡以上以内之内的]?)】?", txt):
                _k = _m.group(1)[-10:].strip("的其与本对该")
                _v = _m.group(2).strip()
                if _k and _v and _re_num(_v):
                    _ns[f"数值·{_k}"] = _v
            for _m in re.finditer(r"([\u4e00-\u9fa5]{2,10})【([^】]{1,24})】", txt):
                _k, _v = _m.group(1)[-10:].strip("的其与本对该"), _m.group(2).strip()
                if _k and _v and _re_num(_v) and _k not in _ns:
                    _ns[_k] = _v
            if _ns:
                _slots_all.append({"no": no or "", "slots": _ns})
            clause_seq.append((no, txt))
        # LLM 分片：条款边界组片（片内零截断），数值槽摘要拼片头（attributes 值来源）
        buf, size_now, chs2, groups = [], 0, [], []
        _slot_i = 0
        cur_labs = []
        for no, txt in clause_seq:
            _pre = ""
            while _slot_i < len(_slots_all) and _slots_all[_slot_i]["no"] == no:
                _pre = "【本条款数值要素】" + "；".join(f"{k}={v}" for k, v in _slots_all[_slot_i]["slots"].items()) + "\n"
                _slot_i += 1
                break
            buf.append(_pre + txt); size_now += len(txt) + 1
            if size_now >= 1000:
                chs2.append("\n".join(buf)); groups.append([])
                buf, size_now = [], 0
        if buf:
            chs2.append("\n".join(buf)); groups.append([])
        chs = chs2
        print(f"[E 叙事] {os.path.basename(d['file'])} 密度 {density} → 条款通道：{len(clauses)} 条款 → {len(chs)} 片")
    else:
        chs = list(_chunks(d["narrative"]))
        _narr_stat.append({"file": os.path.basename(d["file"]), "mode": "plain", "density": density,
                           "chunks": len(chs),
                           "chars": sum(len("".join(l.split())) for l in d["narrative"])})
        print(f"[E 叙事] {os.path.basename(d['file'])} 密度 {density} → 散文通道：{len(chs)} 片")
    _picked = chs[:40]   # 上限防失控（条款化片数更多仍受预算闸）
    half = MAX_IN // 2 - 1200
    def _ask_chunk(_ci, _ch):
        blocks = [_ch[i:i + half] for i in range(0, len(_ch), half)][:2]
        try:
            return _ci, ask("叙事", NARR_SYS, f"文件：{d['file']} 片段{_ci+1}/{len(_picked)}\n" + "\n".join(blocks)[:half * 2])
        except Exception as _e:
            print(f"[E 叙事] ⚠️ 片{_ci+1} 失败跳过：{str(_e)[:80]}")
            return _ci, {}
    with ThreadPoolExecutor(max_workers=_LLM_CONC) as _ex:
        _results = list(_ex.map(_ask_chunk, range(len(_picked)), _picked))
    for ci, r in _results:   # 并行计算、按片序合并=确定性
        for k, kf in (("classes", "label"), ("instances", None), ("attributes", None), ("links", None), ("rules", None)):
            for it in (r.get(k) or []):
                if not isinstance(it, dict):
                    continue
                if k == "classes" and it.get("label"):
                    _ensure_class(str(it["label"]).strip(), str(it.get("definition") or "")[:200],
                                  src_doc=os.path.basename(d["file"]))
                elif k == "attributes" and it.get("subject") and it.get("name") and it.get("value") is not None:
                    _subj = str(it["subject"]).strip()
                    _ti = next((x for x in card["instances"] if isinstance(x, dict)
                                and str(x.get("label") or "").strip() == _subj), None)
                    if _ti is not None:
                        _ti.setdefault("props", {})[str(it["name"]).strip()[:20]] = str(it["value"])[:200]
                    else:
                        card["pending"].append(f"属性 {str(it['name'])[:20]}={str(it['value'])[:60]} 的主体『{_subj}』未抽取，需确认主体来源")
                elif k == "instances" and it.get("label") and it.get("class"):
                    _ilab = str(it["label"]).strip()
                    # 属性值形态兜底（通用词尾表+纯数值）：费率/上限/日/金额…是主体的属性值，
                    # 不是实例——并入本文档主体实例的 props（主体=该文档 evidence 最多的实例）
                    if (_ilab.endswith(("率", "上限", "日", "金额", "期限", "费", "税", "基准", "净值", "规模"))
                            or _re_num(_ilab) and len(_ilab) <= 24):
                        _dsubj = _doc_subject.get(os.path.basename(d["file"]))
                        if _dsubj is not None:
                            _dsubj.setdefault("props", {})[_ilab[:20]] = str((it.get("props") or {}).get("原文")
                                                                            or (it.get("props") or {}).get("数值") or _ilab)[:200]
                            continue
                    _add_inst(_ilab, it["class"], it.get("props") or {}, str(it.get("evidence") or ""))
                    # 孤岛治理（通用，认出处）：实例挂到当前片包含的条款——"条款—涉及→实例"
                    for _cl in (groups[ci] if ci < len(groups) else [])[:6]:
                        if _cl != _ilab:
                            card["links"].append({"subject": _cl, "predicate": "涉及",
                                                  "object": _ilab, "evidence": f"{d['file']}片{ci+1}"})
                elif k == "links" and it.get("subject") and it.get("object") and it.get("predicate"):
                    card["links"].append({kk: str(it[kk])[:80] for kk in ("subject", "predicate", "object")}
                                         | {"evidence": str(it.get("evidence") or "")[:100]})
                elif k == "rules" and it.get("condition"):
                    card["rules"].append({"condition": str(it["condition"])[:120],
                                          "conclusion": str(it.get("conclusion") or "")[:80],
                                          "evidence": str(it.get("evidence") or "")[:100]})
        card["pending"] += [str(p)[:120] for p in (r.get("pending") or []) if isinstance(p, str)]
TIME["E_narrative"] = time.time() - t
# 守恒校验（通用）：路由前后总字符量核对——账对不上即丢内容，直接告警
for _st in _narr_stat:
    _in = sum(len("".join(l.split())) for l in (next((d for d in docs if os.path.basename(d["file"]) == _st["file"]), {}).get("narrative") or []))
    _out = _st.get("chars") or sum(len(c) for c in [])
    if _st["mode"] == "clause" and _out and abs(_out - _in) > _in * 0.02:
        print(f"[E 叙事] ⚠️ 守恒告警 {_st['file']}: 切分后 {_out} != 原文 {_in}")
try:
    _rep_narr = _narr_stat
except NameError:
    _rep_narr = []
card["links"] = _uniq(card["links"], lambda l: (l.get("subject"), l.get("predicate"), l.get("object")))
card["rules"] = _uniq(card["rules"], lambda r: (r.get("condition"), r.get("conclusion")))
print(f"[E 叙事] 累计 {len(card['classes'])}类 {len(card['instances'])}实例 {len(card['links'])}边 "
      f"{len(card['rules'])}规则（{TIME['E_narrative']:.0f}s）")

# ═══════════════ F 专家（追加补丁，不携带实例——预算减半） ═══════════════
EXPERT_SYS = ("你是资深业务专家+本体评审人（三标准：逻辑正确/描述清晰/内容完备）。对采集卡做【追加式】评审——"
              "严禁删除已有条目，只输出补丁 JSON：{add_classes,add_relations,add_links,add_rules,pending_additions,review}。"
              "1) 挖漏掉的条件规则；2) 引用闭合提示（被引用但缺失的类）；3) review 放三标准结论。\n"
              "建模纪律：①层次——新增类尽量收编到顶类『业务对象』之下（add_relations 里用 属于/属于业务域 表达），"
              "上位类必须是类别性概念（接口/系统/文档/流程类），严禁把操作按钮等实例性概念声明为其它类的子类；"
              "②原文即实例——规则/条款原文完整保留为实例属性或规则条件，不摘要不改写；"
              "③低基数分类值（状态/类型/角色/阶段）实体化为类与实例；"
              "④源文未明示的归属/口径一律进 pending_additions，严禁脑补默认值；"
              "⑤若文档含决策/行动语义，补『推理决策/行动指令/运行时业务事件』三个概念类"
              "（只定义形状不造实例）与 事件产生决策/决策触发指令 两条关系。")
t = time.time()
EXPERT_CAP = int(cfg.get("expert_input_cap") or 45000)   # 评审卡总字节上限（大工作簿防调用风暴）
for d in docs:
    cs = json.dumps({k: card[k] for k in ("classes", "relations", "links", "rules")},
                    ensure_ascii=False)[:EXPERT_CAP]
    PART = MAX_IN - 3500   # 单片 ≤ 预算减提示词余量
    for part_i in range(0, min(len(cs), PART), PART):
        r3 = ask("专家", EXPERT_SYS, f"（{d['file']}）评审卡 {part_i}起：\n" + cs[part_i:part_i + PART])
        def _safe(seq, kf):
            return [it for it in (seq if isinstance(seq, list) else []) if isinstance(it, dict) and kf(it)]
        card["classes"] += _safe(r3.get("add_classes"), lambda c: c.get("label"))
        card["relations"] += _safe(r3.get("add_relations"), lambda r: r.get("label"))
        card["links"] += _safe(r3.get("add_links"), lambda l: l.get("subject") and l.get("object"))
        card["rules"] += _safe(r3.get("add_rules"), lambda r: r.get("condition"))
        card["pending"] += [p for p in (r3.get("pending_additions") or []) if isinstance(p, str)]
    card["links"] = _uniq(card["links"], lambda l: (l.get("subject"), l.get("predicate"), l.get("object")))
    card["rules"] = _uniq(card["rules"], lambda r: (r.get("condition"), r.get("conclusion")))
TIME["F_expert"] = time.time() - t
print(f"[F 专家] {len(card['classes'])}类 {len(card['instances'])}实例 {len(card['links'])}边 "
      f"{len(card['rules'])}规则 {len(card['pending'])}待确认（{TIME['F_expert']:.0f}s）")

# ═══════════════ F.2 完备性对账（29 句式覆盖清单，1 次调用） ═══════════════
# 29 类句式=语义形态覆盖对账表（对象2+约束7+术语3+规则1+流程5+权限4+CRUD7），
# 与文档类型无关：表格型命中规则/流程句、制度文命中术语/约束句、操作手册命中权限/CRUD 句。
CHECKLIST = [
    ("O1", "对象", "核心业务对象/实体都有对应类"),
    ("O2", "对象", "每个对象的关键属性（字段）已建模"),
    ("C1", "约束", "必须满足的条件（必须/应当/需）"),
    ("C2", "约束", "禁止性约束（不能/不得/禁止）"),
    ("C3", "约束", "取值范围/枚举约束"),
    ("C4", "约束", "基数约束（至少/最多/唯一）"),
    ("C5", "约束", "依赖条件（若A则B/仅当）"),
    ("C6", "约束", "互斥约束"),
    ("C7", "约束", "时序/顺序约束"),
    ("T1", "术语", "同义词/等价术语"),
    ("T2", "术语", "缩写/编码对照"),
    ("T3", "术语", "别名/俗称"),
    ("R1", "规则", "业务规则（如果…则…）"),
    ("P1", "流程", "业务阶段/环节划分"),
    ("P2", "流程", "步骤/操作序列"),
    ("P3", "流程", "触发条件（何时执行）"),
    ("P4", "流程", "输入/输出物"),
    ("P5", "流程", "异常/分支处理"),
    ("A1", "权限", "角色/岗位"),
    ("A2", "权限", "操作权限（谁能做什么）"),
    ("A3", "权限", "审批/授权链"),
    ("A4", "权限", "数据可见范围"),
    ("D1", "CRUD", "新增/创建"),
    ("D2", "CRUD", "删除/作废"),
    ("D3", "CRUD", "修改/更新"),
    ("D4", "CRUD", "查询/检索"),
    ("D5", "CRUD", "批量操作"),
    ("D6", "CRUD", "导入/导出"),
    ("D7", "CRUD", "状态流转"),
]
ACCOUNT_SYS = ("你是本体完备性审计员。给定采集卡摘要与 29 句式清单，逐句判断当前本体是否覆盖该语义形态，"
               "只输出 JSON：{accounting:[{id:\"句式ID\", status:\"有据\"|\"无据\"|\"待确认\", evidence:\"≤40字依据\"}],\n"
               " add_rules:[{condition,conclusion,evidence}], add_classes:[{label,definition,properties:[]}],\n"
               " add_links:[{subject,predicate,object,evidence}],\n"
               " add_terms:[{preferred:\"主标签\", alternative:\"替代标签\", kind:\"synonym|alias|code_map\", evidence}],\n"
               " pending_additions:[\"…\"]}\n"
               "铁律：status 只依据评审卡内容判断，不得臆断；『无据』是正常结论（文档没写≠本体缺陷）；"
               "T1同义词/T2编码对照/T3别名 判『有据』时必须给对应 add_terms 补丁（preferred 用已有类或实例名）；"
               "对账中发现卡片漏采的规则/类/边，放 add_* 补丁（原文即实例，不摘要）；歧义进 pending_additions。")
t = time.time()
_inst_by_cls = {}
for x in card["instances"]:
    if isinstance(x, dict) and x.get("class"):
        _inst_by_cls.setdefault(str(x["class"]), []).append(str(x.get("label") or "")[:24])
digest = ("类：" + json.dumps([str(c.get('label') or '') for c in card['classes']], ensure_ascii=False)[:1200]
          + "\n关系：" + json.dumps([str(r.get('label') or '') for r in card['relations']], ensure_ascii=False)[:600]
          + f"\n规则数 {len(card['rules'])}，样例：" + json.dumps([r.get('condition', '')[:50] for r in card['rules'][:8]], ensure_ascii=False)[:800]
          + "\n实例分布：" + json.dumps({k: f"{len(v)}条 如{'/'.join(v[:4])}" for k, v in list(_inst_by_cls.items())[:20]}, ensure_ascii=False)[:1200]
          + "\n待确认：" + json.dumps([str(p)[:40] for p in card["pending"][:8]], ensure_ascii=False)[:400])
r5 = ask("对账", ACCOUNT_SYS,
         "句式清单：" + json.dumps([{"id": i, "cat": c, "shape": sh} for i, c, sh in CHECKLIST], ensure_ascii=False)
         + "\n\n采集卡摘要：\n" + digest[:MAX_IN - 3000])
acct = [a for a in (r5.get("accounting") if isinstance(r5, dict) else None) or []
        if isinstance(a, dict) and a.get("id") and a.get("status") in ("有据", "无据", "待确认")]
_seen_ids = {a["id"] for a in acct}
acct += [{"id": cid, "status": "未盘点", "evidence": ""} for cid, _, _ in CHECKLIST if cid not in _seen_ids]
card["checklist"] = acct
def _safe5(seq, kf):
    return [it for it in (seq if isinstance(seq, list) else []) if isinstance(it, dict) and kf(it)]
card["classes"] += _safe5(r5.get("add_classes"), lambda c: c.get("label"))
card["relations"] += _safe5(r5.get("add_relations"), lambda r: r.get("label"))
card["links"] += _safe5(r5.get("add_links"), lambda l: l.get("subject") and l.get("object") and l.get("predicate"))
card["rules"] += _safe5(r5.get("add_rules"), lambda r: r.get("condition"))
card["terms"] += _safe5(r5.get("add_terms"), lambda tm: tm.get("preferred") and tm.get("alternative"))
card["pending"] += [p for p in (r5.get("pending_additions") or []) if isinstance(p, str)]
card["links"] = _uniq(card["links"], lambda l: (l.get("subject"), l.get("predicate"), l.get("object")))
card["rules"] = _uniq(card["rules"], lambda r: (r.get("condition"), r.get("conclusion")))
TIME["F2_check"] = time.time() - t
from collections import Counter as _C3
_cov = _C3(a["status"] for a in acct)
print(f"[F.2 对账] 29 句式覆盖：有据 {_cov.get('有据', 0)} / 无据 {_cov.get('无据', 0)} / "
      f"待确认 {_cov.get('待确认', 0)} / 未盘点 {_cov.get('未盘点', 0)}"
      f"（+规则 {len(card['rules'])} 累计，{TIME['F2_check']:.0f}s）")

# ═══════════════ F.5 实体消解（全来源通用：表格展开/叙事/专家一视同仁） ═══════════════
# 三层自动合并（按误并风险递进），拼音层不做（60 实例误并事故），跨类语义近似只进待确认。
TIME["F5_resolve"] = 0.0
def _code_like(s):
    """编码形标签（C250405B/CTEB003/0122）：只允许归一化同名合并，嵌入层不可靠（短编码串余弦虚高）。"""
    n = re.sub(r"\s+", "", str(s or "").strip()).lower()
    return bool(re.match(r"^[a-z0-9]{4,12}$", n)) and re.search(r"\d", str(s or ""))

def resolve_entities(card):
    def norm(s):
        s = str(s or "").strip()[:80]
        # 全角→半角 + 压空白 + 拉丁小写（中文名保持原样）
        tbl = {ord(f): ord(t) for f, t in zip("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９",
                                              "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")}
        return re.sub(r"\s+", "", s.translate(tbl)).lower()
    def code_like(s):
        return bool(re.match(r"^[A-Za-z0-9]{4,12}$", norm(s))) and re.search(r"\d", s or "")
    insts = [x for x in card["instances"] if isinstance(x, dict) and x.get("label")]
    for x in insts:
        x["_nl"] = norm(x.get("label"))
    # 分组：归一化同名
    groups = {}
    for x in insts:
        groups.setdefault(x["_nl"], []).append(x)
    # 类别名归一（"接口"/"BP接口" 合并判定用）
    merged_n = cand_n = 0
    keep_extra = set()   # 同名跨类"未自动合并"组的全部成员都要保留
    canon = {}   # (norm_label) -> (canon_label, canon_class, props_merge_target)
    for nl, grp in groups.items():
        if len(grp) == 1:
            x = grp[0]
            canon[nl] = (x["label"], x.get("class"), x)
            continue
        # 同名同类：直接合并（首条为主，props 冲突进 alt）
        base = grp[0]
        for other in grp[1:]:
            for k, v in (other.get("props") or {}).items():
                if k not in base["props"]:
                    base["props"][k] = v
                elif base["props"][k] != v:
                    ai = 2
                    while f"{k}_alt{ai}" in base["props"]:
                        ai += 1
                    if f"{k}_alt{ai}" not in base["props"]:
                        base["props"][f"{k}_alt{ai}"] = v   # CQ11 场景：同编码多描述保留为 alt
            # 术语层喂料：异形标签（大小写/全半角差异）= SKOS altLabel 素材；来源文件并集
            if str(other.get("label")) != str(base.get("label")):
                card["terms"].append({"preferred": base["label"], "alternative": other["label"],
                                      "kind": "同名归一", "evidence": "消解合并"})
            for f in (other.get("_files") or []):
                base.setdefault("_files", [])
                if f not in base["_files"]:
                    base["_files"].append(f)
        if all(x.get("class") == base.get("class") for x in grp):
            merged_n += len(grp) - 1
            canon[nl] = (base["label"], base.get("class"), base)
            continue
        # 同名跨类：编码形（C250405B/CTEB003/0122）→ 统一到多数类（CQ5 根因）；非编码 → 合并+待确认
        if code_like(base["label"]):
            cls_cnt = {}
            for x in grp:
                cls_cnt[x.get("class")] = cls_cnt.get(x.get("class"), 0) + 1
            major_cls = max(cls_cnt, key=cls_cnt.get)
            base["class"] = major_cls
            for other in grp[1:]:
                for k, v in (other.get("props") or {}).items():
                    if k not in base["props"]:
                        base["props"][k] = v
                for f in (other.get("_files") or []):
                    base.setdefault("_files", [])
                    if f not in base["_files"]:
                        base["_files"].append(f)
            merged_n += len(grp) - 1
            canon[nl] = (base["label"], major_cls, base)
        else:
            cls_pair = "/".join(sorted({str(x.get('class')) for x in grp}))
            card["pending"].append(f"同名跨类待人工确认：『{base['label'][:40]}』存在于 {cls_pair}（未自动合并）")
            cand_n += len(grp) - 1
            canon[nl] = (base["label"], base.get("class"), base)
            keep_extra.update(id(x) for x in grp[1:])
    # 重指 links（subject/object 按归一化名重指到规范实例）
    n_rep = 0
    for lk in card["links"]:
        for side in ("subject", "object"):
            nl = norm(lk.get(side))
            if nl in canon:
                cl, cc, _ = canon[nl]
                if lk.get(side) != cl:
                    lk[side] = cl; n_rep += 1
        lk["subject"] = str(lk.get("subject") or "")[:80]
        lk["object"] = str(lk.get("object") or "")[:80]
    # 载荷边去重 + 实例表重建
    card["links"] = _uniq(card["links"], lambda l: (l.get("subject"), l.get("predicate"), l.get("object")))
    seen, new_insts = set(), []
    for x in insts:
        key = (x["_nl"], x.get("class"))
        if (x is canon.get(x["_nl"], (None, None, None))[2] or id(x) in keep_extra) and key not in seen:
            seen.add(key); x.pop("_nl", None); new_insts.append(x)
        elif "_nl" in x:
            x.pop("_nl", None)
    card["instances"] = new_insts
    return merged_n, cand_n, n_rep

# 嵌入层（可选，bge-m3）：同类高相似合并 / 跨类只进待确认
def embed_sim_merge(card, thresh=0.96):
    import requests as _rq
    ecfg = cfg.get("embedding") or {"base_url": "http://localhost:8101/v1", "model": "bge-m3"}
    insts = [x for x in card["instances"] if isinstance(x, dict) and x.get("label")]
    if len(insts) < 2:
        return 0, 0
    try:
        texts = [f"{x.get('class')}：{x['label']}" for x in insts]
        vecs = []
        for i in range(0, len(texts), 64):
            r = _rq.post(f"{ecfg['base_url']}/embeddings", json={"model": ecfg["model"], "input": texts[i:i+64]},
                         timeout=30)
            vecs += [d["embedding"] for d in r.json()["data"]]
        import math
        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb) if na and nb else 0.0
        merged = cand = 0
        removed = set()
        for i in range(len(insts)):
            if i in removed or _code_like(insts[i]["label"]):
                continue
            for j in range(i + 1, len(insts)):
                if j in removed or _code_like(insts[j]["label"]):
                    continue
                sim = cos(vecs[i], vecs[j])
                if sim >= thresh:
                    same = insts[i].get("class") == insts[j].get("class")
                    if same:
                        for k, v in (insts[j].get("props") or {}).items():
                            insts[i]["props"].setdefault(k, v)
                        removed.add(j); merged += 1
                    elif sim >= thresh + 0.02 and i not in removed:
                        card["pending"].append(f"语义近似跨类待确认：『{insts[i]['label'][:30]}』({insts[i].get('class')}) ≈ "
                                               f"『{insts[j]['label'][:30]}』({insts[j].get('class')}) sim={sim:.3f}")
                        cand += 1
        if removed:
            card["instances"] = [x for k, x in enumerate(insts) if k not in removed]
        return merged, cand
    except Exception as e:
        print(f"[F.5 嵌入层] 跳过（{str(e)[:60]}）")
        return 0, 0

t = time.time()
m1, c1, nr = resolve_entities(card)
m2, c2 = (0, 0)
if cfg.get("use_embedding", True):
    m2, c2 = embed_sim_merge(card)
TIME["F5_resolve"] = time.time() - t
card["links"] = _uniq(card["links"], lambda l: (l.get("subject"), l.get("predicate"), l.get("object")))
card["rules"] = _uniq(card["rules"], lambda r: (r.get("condition"), r.get("conclusion")))
# 叙事通道的 links 没有显式 relations 声明：按端点实例的类自动补注册（否则谓词不入 TBox、边全掉）
_known = {str(x.get("label") or "").strip(): x.get("class") for x in card["instances"] if isinstance(x, dict)}
_have_rel = {str(r.get("label") or "") for r in card["relations"] if isinstance(r, dict)}
_n_newrel = 0
for lk in card["links"]:
    pred = str(lk.get("predicate") or "").strip()
    dc = _known.get(str(lk.get("subject") or "").strip())
    rc = _known.get(str(lk.get("object") or "").strip())
    if pred and dc and rc and pred not in _have_rel:
        card["relations"].append({"label": pred[:40], "domain": dc, "range": rc})
        _have_rel.add(pred); _n_newrel += 1
if _n_newrel:
    print(f"[F.5 关系补注册] 从 links 端点类补注册 {_n_newrel} 个谓词")

# ── 类-实例误置降级（通用：认名字形态重叠，不认业务词）──
# 误置模式：类 C 与实例 I 指称同一事物（"托管费"类 + "托管费"实例——托管费是一项
# 具体费用安排（有费率/日期），是"费用项目"的实例，绝不该是类）。
# 判据：类名归一化 == 实例名归一化，或实例名 ⊆ 类名且长度差 ≤4（强包含）。
def _norm_zh(t):
    return re.sub(r"\s+", "", str(t or "").strip())
_HUB = "业务对象"
_mis, _demoted = [], 0
_cls_norm = {str(c.get("label") or "").strip(): _norm_zh(c.get("label")) for c in card["classes"] if isinstance(c, dict)}
_inst_by_norm = {}
for _i in card["instances"]:
    if isinstance(_i, dict) and _i.get("class"):
        _inst_by_norm.setdefault(_norm_zh(_i.get("label")), []).append(_i)
for _c in list(card["classes"]):
    if not isinstance(_c, dict):
        continue
    _cl = str(_c.get("label") or "").strip()
    _cn = _cls_norm.get(_cl, "")
    if not _cn or _cn == _norm_zh(_HUB):
        continue
    _hits = _inst_by_norm.get(_cn, [])
    _strong = [h for h in _hits if _cn and (_norm_zh(h.get("label")) == _cn
               or (len(_norm_zh(h.get("label"))) >= len(_cn) - 4 and _cn in _norm_zh(h.get("label"))))]
    if not _strong:
        continue
    # 过度降级保护：类下若还有其它不同名实例（该类被多个个体实例化）→ 类是真的，
    # 误置的只是同名实例本身（并入类定义说明后删除实例），类保留
    _others = [i2 for i2 in _hits if i2 not in _strong]
    if len(_others) >= 1:
        for h in _strong:
            if h in card["instances"]:
                card["instances"].remove(h)
        card["pending"].append(f"实例『{_cl}』与类同名冲突已并入类（具体个体名待源方确认）")
        continue
    # 类误置确认：父类（parent_classes 声明或"属于"收编边）为降级实例的新家；缺省顶类
    _new_cls = ""
    for _r in card["relations"]:
        if str(_r.get("label")) in ("属于", "属于业务域") and str(_r.get("domain")) == _cl:
            _new_cls = str(_r.get("range") or "")
    if not _new_cls and _c.get("parent_classes"):
        _new_cls = str(_c["parent_classes"][0])
    _new_cls = _new_cls or _HUB
    for h in _strong:
        h["class"] = _new_cls          # 误置实例归入父类/顶类
    card["pending"].append(f"类『{_cl}』与同名实例冲突已降级为实例（其类别性待源方确认）")
    card["classes"].remove(_c)          # 类壳删除
    card["relations"] = [r for r in card["relations"]
                         if isinstance(r, dict) and str(r.get("domain")) != _cl and str(r.get("range")) != _cl]
    # 该类名下其它实例（空 label/变体）并入保留实例 props 后移除
    for i2 in list(card["instances"]):
        if isinstance(i2, dict) and i2.get("class") == _cl and i2 not in _strong:
            card["instances"].remove(i2)
    _mis.append(_cl); _demoted += len(_strong)
if _mis:
    print(f"[F.5 类误置降级] {_mis} → 实例归入父类/顶类（{_demoted} 个实例保留）")

# ── 类名后缀聚类收编（通用）：≥3 类共享 ≥2 字尾词 → 尾词父类，子类挂"属于"；
# 泛尾停用（类型/事项/内容等通用语言词不建父类，防噪声聚合）
_tail_stop = {"类型", "事项", "内容", "信息", "情况", "方式", "原因", "要求", "说明"}
_tail_groups = {}
for _c2 in card["classes"]:
    if not isinstance(_c2, dict):
        continue
    _l2 = str(_c2.get("label") or "").strip()
    if len(_l2) >= 3 and _l2[-2:] not in _tail_stop:
        _tail_groups.setdefault(_l2[-2:], []).append(_l2)
_n_tail = 0
for _tw, _members in _tail_groups.items():
    _members = [m for m in _members if m != _tw]
    if len(_members) < 3 or _tw in {str(c.get("label") or "").strip() for c in card["classes"] if isinstance(c, dict)}:
        continue
    card["classes"].append({"label": _tw, "definition": f"尾词聚类父类：{'、'.join(_members[:6])}等的上位类别（后缀聚类自动生成）"})
    for _m in _members:
        card["relations"].append({"label": "属于", "domain": _m, "range": _tw})
    _n_tail += 1
if _n_tail:
    print(f"[F.5 尾词聚类] {_n_tail} 个尾词父类生成（机构/资产/风险等类族收编）")

# ── 章节目录/正文去重（通用）：同编号章节保留原文长者，短者（目录版）移除
_sec_by_no = {}
_rm_sec = 0
for _i3 in list(card["instances"]):
    if isinstance(_i3, dict) and _i3.get("class") == "章节":
        _no3 = str(_i3.get("label") or "").split("·")[0].strip()
        _prev = _sec_by_no.get(_no3)
        _cur_len = len(str((_i3.get("props") or {}).get("原文") or ""))
        if _prev is None:
            _sec_by_no[_no3] = _i3
        elif _cur_len > len(str((_prev.get("props") or {}).get("原文") or "")):
            if _prev in card["instances"]:
                card["instances"].remove(_prev)
            _sec_by_no[_no3] = _i3
        else:
            if _i3 in card["instances"]:
                card["instances"].remove(_i3)
            _rm_sec += 1
if _rm_sec:
    print(f"[F.5 章节去重] 目录/正文重复章节移除 {_rm_sec} 个")
print(f"[F.5 消解] 同名/编码合并 {m1} 实例 + 嵌入合并 {m2}（重指边 {nr}）| "
      f"跨类候选→待确认 {c1 + c2} | 现 {len(card['classes'])}类 {len(card['instances'])}实例 "
      f"{len(card['links'])}边（{TIME['F5_resolve']:.1f}s）")
json.dump(card, open(f"{OUT}/spec_card.json", "w"), ensure_ascii=False, indent=1)

# ═══════════════ G 公理 + TTL + owlrl ═══════════════
# 待确认项实例化（外部 CQ4 口径：待确认内容作为本体查询对象，进 TTL）
if card["pending"]:
    _ensure_class("待确认项", "构建过程中识别的待业务确认事项")
    for i, p in enumerate(card["pending"][:24]):
        _add_inst(f"待确认{i+1}", "待确认项", {"待确认内容": str(p)[:200]}, "构建流程")
from build_ontology import OntologyBuilder, EX, SWRL, SWRLB
from rdflib import RDF, RDFS, OWL, XSD, Literal, BNode, Namespace
t = time.time()
cls_list = [str(c.get("label") or "").strip() for c in card["classes"] if c.get("label")]
rel_list = _uniq(card["relations"], lambda r: r.get("label"))
r4 = ask("公理", "你是本体逻辑师。基于类/关系清单补形式公理，只输出 JSON："
         "{subclass_of:[[子,父]…],equivalent_classes:[[A,B]…],disjoint:[[A,B]…]}，无据不输出。",
         "类：" + json.dumps(cls_list, ensure_ascii=False)[:3000]
         + "\n关系：" + json.dumps([{r.get("label"): [r.get("domain"), r.get("range")]} for r in rel_list],
                                   ensure_ascii=False)[:2500])
axioms = []
for r in rel_list:
    if r.get("domain") in cls_list and r.get("range") in cls_list:
        axioms += [{"kind": "domain", "rel": r["label"], "class": r["domain"]},
                   {"kind": "range", "rel": r["label"], "class": r["range"]}]
def _pairlist(seq):
    return [a for a in (seq if isinstance(seq, list) else []) if isinstance(a, list) and len(a) == 2
            and all(isinstance(x, str) for x in a)]
# subclass 公理合法性闸（通用，非业务词表）：
# ①子=父拒收；②子类是某关系的 domain 且父类是其 range → 边闭环式怪层次（实例性概念⊑其关系目标）拒收；
# ③继承链成环拒收。拦截项进报表（可疑公理数）。
rejected_ax = []
_rel_dom_rng = {(r.get("domain"), r.get("range")) for r in rel_list}
parents = {}
_ok_sub, _ok_eq, _ok_dj = [], [], []
for a in _pairlist(r4.get("subclass_of")):
    if a[0] not in cls_list or a[1] not in cls_list:
        continue
    if a[0] == a[1] or (a[0], a[1]) in _rel_dom_rng:
        rejected_ax.append({"pair": a, "reason": "边闭环（子类是关系的domain且父类是其range）"})
        continue
    _ok_sub.append(a)
for child, par in _ok_sub:   # 环检测：沿父链走，回到自己即拒
    seen_, cur, cyc = set(), par, False
    while cur in parents and cur not in seen_:
        seen_.add(cur); cur = parents[cur]
        if cur == child:
            cyc = True; break
    if cyc:
        rejected_ax.append({"pair": [child, par], "reason": "继承链成环"})
    else:
        axioms.append({"kind": "subclass_of", "classes": [child, par]}); parents[child] = par
for a in _pairlist(r4.get("equivalent_classes")):
    if a[0] in cls_list and a[1] in cls_list and a[0] != a[1]:
        axioms.append({"kind": "equivalent_classes", "classes": a}); _ok_eq.append(a)
for a in _pairlist(r4.get("disjoint")):
    if a[0] in cls_list and a[1] in cls_list and a[0] != a[1]:
        axioms.append({"kind": "disjoint_with", "classes": a}); _ok_dj.append(a)
from collections import Counter as _C2
declared = {r.get("label") for r in rel_list}
obj_cnt = _C2((l["subject"], l["predicate"]) for l in card["links"] if l.get("predicate") in declared)
contra = [(s_, p_) for (s_, p_), n_ in obj_cnt.items() if n_ > 1]
axioms += [{"kind": "contradiction_watch", "subject": s_, "rel": p_} for s_, p_ in contra[:20]]

b = OntologyBuilder(EX, title=f"MVP模块构建 {'+'.join(d['file'] for d in docs)[:40]}",
                    comment="声明式建模+确定性展开 MVP")
CID, RID = {}, {}
_dp_names = set()   # 已注册的数据属性谓词（真 SWRL 数据条件落形用）
def _ensure_dprop(cls_zh, k):
    """实例属性谓词注册：属性三元组挂上之前必须先声明 dprop（否则谓词游离在 TBox 外）。"""
    if cls_zh not in CID or not str(k).strip():
        return ""
    dn = f"{CID[cls_zh]}_dp_{k}"
    if dn not in _dp_names:
        _dp_names.add(dn)
        b.dprop(dn, CID[cls_zh], XSD.string, label=str(k)[:40])
    return dn
def ensure_class(zh, definition=""):
    if zh and zh not in CID and len(CID) < 150:
        i = len(CID)
        b.cls(f"C{i}", label=zh, comment=definition[:200])
        CID[zh] = f"C{i}"
    return CID.get(zh)
for c in card["classes"]:
    zh = str(c.get("label") or "").strip()
    if zh:
        ensure_class(zh, c.get("definition") or "")
        cid_zh = CID.get(zh)
        props_raw = c.get("properties") or {}
        prop_names = list(props_raw.keys()) if isinstance(props_raw, dict) else [str(p) for p in props_raw]
        for p in prop_names[:20]:
            if cid_zh:
                b.dprop(f"{cid_zh}_dp_{p}", cid_zh, XSD.string, label=str(p)[:40])
                _dp_names.add(f"{cid_zh}_dp_{p}")
for i, r in enumerate(rel_list):
    if r.get("label") and ensure_class(r.get("domain")) and ensure_class(r.get("range")):
        b.oprop(f"R{i}", CID[r["domain"]], CID[r["range"]], label=r["label"][:40])
        RID[r["label"]] = f"R{i}"
# 公理落图：过闸的 subclass/equivalent/disjoint 写进 TTL（此前只进报表——原始 TTL subClassOf=0，
# owlrl 物化推不出任何实例继承，推理形同虚设；落图后实例类型沿层次传播+disjoint 冲突才有判据）
n_ax_land = 0
for child, par in _ok_sub:
    if child in CID and par in CID and child != par:
        b.g.add((EX[CID[child]], RDFS.subClassOf, EX[CID[par]])); n_ax_land += 1
for a, bb in _ok_eq:
    if a in CID and bb in CID and a != bb:
        b.g.add((EX[CID[a]], OWL.equivalentClass, EX[CID[bb]])); n_ax_land += 1
for a, bb in _ok_dj:
    if a in CID and bb in CID and a != bb:
        b.g.add((EX[CID[a]], OWL.disjointWith, EX[CID[bb]])); n_ax_land += 1
inst_id = {}
for ins in card["instances"]:
    lab, cls = str(ins.get("label") or "").strip(), ins.get("class")
    if cls not in CID or not lab or (lab, cls) in inst_id:
        if (lab, cls) in inst_id and cls in CID:
            iid = inst_id[(lab, cls)]
            for k, v in (ins.get("props") or {}).items():
                if str(k).strip() and isinstance(v, (str, int, float)) and str(v).strip():
                    dn = _ensure_dprop(cls, k)
                    if dn:
                        b.g.add((EX[iid], EX[dn], Literal(str(v)[:300])))
        continue
    iid = "inst_" + hashlib.md5(f"{lab}|{cls}".encode()).hexdigest()[:16]
    inst_id[(lab, cls)] = iid
    b.individual(iid, CID[cls], label=lab[:80])
    for k, v in (ins.get("props") or {}).items():
        if str(k).strip() and isinstance(v, (str, int, float)) and str(v).strip():
            dn = _ensure_dprop(cls, k)
            if dn:
                b.g.add((EX[iid], EX[dn], Literal(str(v)[:300])))
n_lk = n_closed = 0
rel_dom = {r.get("label"): (r.get("domain"), r.get("range")) for r in rel_list}
for lk in card["links"]:
    s_ = inst_id.get((lk.get("subject"), next((c for (l, c) in inst_id if l == lk.get("subject")), None)))
    o_ = inst_id.get((lk.get("object"), next((c for (l, c) in inst_id if l == lk.get("object")), None)))
    p_ = RID.get(lk.get("predicate"))
    dom, rng = rel_dom.get(lk.get("predicate"), (None, None))
    if s_ is None and dom in CID and lk.get("subject"):
        iid = "inst_" + hashlib.md5(f"{lk['subject']}|{dom}".encode()).hexdigest()[:16]
        inst_id[(lk["subject"], dom)] = iid
        b.individual(iid, CID[dom], label=lk["subject"][:80]); s_ = iid; n_closed += 1
    if o_ is None and rng in CID and lk.get("object"):
        iid = "inst_" + hashlib.md5(f"{lk['object']}|{rng}".encode()).hexdigest()[:16]
        inst_id[(lk["object"], rng)] = iid
        b.individual(iid, CID[rng], label=lk["object"][:80]); o_ = iid; n_closed += 1
    if s_ and o_ and p_:
        b.g.add((EX[s_], EX[p_], EX[o_])); n_lk += 1
# ── 属性特征白名单：函数性公理仅在数据零反证时上（≥3 主体且无一对多 → 函数性；有违规=反证不上，
# 防 owlrl 静默 sameAs 强并事故；与平台闸⑥同一防线）──
_fp_sub = {}
for lk in card["links"]:
    if lk.get("predicate") in declared and lk.get("subject") and lk.get("object"):
        _fp_sub.setdefault(lk["predicate"], {}).setdefault(lk["subject"], set()).add(lk["object"])
func_props = []
for p_, sm in _fp_sub.items():
    viol = sum(1 for objs in sm.values() if len({o for o in objs if o}) > 1)
    if len(sm) >= 3 and viol == 0 and p_ in RID:
        b.g.add((EX[RID[p_]], RDF.type, OWL.FunctionalProperty))
        func_props.append(p_)
axioms += [{"kind": "functional", "rel": p_} for p_ in func_props]

# ── 术语层落图（SKOS）：对账 T1-T3 + 消解异形标签 → skos:altLabel；code_map → skos:exactMatch ──
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
b.g.bind("skos", SKOS)
lab2iid = {}
for (l_, c_), i_ in inst_id.items():
    lab2iid.setdefault(l_, i_)
n_term = 0
for tm in card["terms"][:60]:
    pref, alt = str(tm.get("preferred") or "").strip()[:80], str(tm.get("alternative") or "").strip()[:80]
    if not pref or not alt or alt == pref:
        continue
    pi_, ai_ = lab2iid.get(pref), lab2iid.get(alt)
    if pi_:
        b.g.add((EX[pi_], SKOS.altLabel, Literal(alt, lang="zh"))); n_term += 1
    elif pref in CID:
        b.g.add((EX[CID[pref]], SKOS.altLabel, Literal(alt, lang="zh"))); n_term += 1
    if pi_ and ai_ and str(tm.get("kind")) == "code_map":
        b.g.add((EX[pi_], SKOS.exactMatch, EX[ai_])); n_term += 1

# ── 规则形式化：1 次调用转可执行分类规则（前提=类断言+属性比较 → 结论=x∈新类），可执行=真 SWRL；
# 落不了形的回落伪规则（文档性保留）。SWRL 变量安全：head 只用 body 已绑定的 x ──
def _indiv_prop_atom(prop_id, v1, v2):
    a = BNode()
    b.g.add((a, RDF.type, SWRL.IndividualPropertyAtom))
    b.g.add((a, SWRL.propertyPredicate, EX[prop_id]))
    b.g.add((a, SWRL.argument1, b.var(v1)))
    b.g.add((a, SWRL.argument2, b.var(v2)))
    return a
def _data_prop_atom(prop_id, v1, v2):
    a = BNode()
    b.g.add((a, RDF.type, SWRL.DatavaluedPropertyAtom))
    b.g.add((a, SWRL.propertyPredicate, EX[prop_id]))
    b.g.add((a, SWRL.argument1, b.var(v1)))
    b.g.add((a, SWRL.argument2, b.var(v2)))
    return a
RSYS = ("你是规则形式化师。把业务规则转成【可执行分类规则】（满足前提的个体 x 归入结论类），只输出 JSON：\n"
        '{rules:[{"if_class":"前提类名","when":[{"prop":"条件字段/属性名","op":"equal|contains","value":"字面值"}],'
        '"derive":"结论类名(2-6字)","because":"原规则condition原样"}]}\n'
        "if_class 优先用规则的主体类（subject 字段对应的类，如场景按钮/产品/接口）；when.prop 用规则原文里出现的"
        "字段名或开关名（opnStat/isRelPrtn 等原文逐字）；每条 1-3 个 when；只形式化能落到『类+属性比较』的规则，"
        "落不了形的跳过不要硬凑；derive 结论类若无现成类可按规则语义新建。")
r6 = ask("规则形式化", RSYS,
         "类清单：" + json.dumps(cls_list[:40], ensure_ascii=False)[:1500]
         + "\n关系清单：" + json.dumps([str(r.get("label")) for r in rel_list][:30], ensure_ascii=False)[:600]
         + "\n规则：" + json.dumps([{"condition": str(x.get("condition"))[:100],
                                     "subject": str(x.get("subject") or "")[:24],
                                     "conclusion": str(x.get("conclusion") or "")[:60]}
                                    for x in card["rules"][:20]], ensure_ascii=False)[:2600])
_OP2SWRLB = {"equal": "equal", "contains": "contains", "not_equal": "notEqual",
             "greater": "greaterThan", "less": "lessThan"}
n_sr = 0
_structured_src = set()
for rr in (r6.get("rules") if isinstance(r6, dict) else None) or []:
    if n_sr >= 24:
        break
    ic, dv = str(rr.get("if_class") or "").strip(), str(rr.get("derive") or "").strip()
    when = rr.get("when")
    if not dv or not isinstance(when, list) or not when:
        continue
    if ic not in CID:
        # 兜底：if_class 填了主体实例名时按 实例→类 反查（认出处=实例属于哪个类是本体事实；
        # 同名跨类有歧义时宁拒不放——和消解纪律一致）
        class_of = _C2(str(i.get("class")) for i in card["instances"] if isinstance(i, dict)
                           and str(i.get("label") or "").strip() == ic and str(i.get("class") or "")
                           in CID)
        if len(class_of) == 1:
            ic = class_of.most_common(1)[0][0]
        else:
            continue
    ensure_class(dv, f"由规则推导：{str(rr.get('because') or '')[:80]}")
    if dv not in CID or dv == ic:
        continue
    body = [b.class_atom(CID[ic], "x")]
    ok = True
    for c_ in when[:3]:
        if not isinstance(c_, dict):
            ok = False; break
        p_ = str(c_.get("prop") or "").strip()[:40]
        val_ = str(c_.get("value") or "").strip()[:80]
        swb = _OP2SWRLB.get(str(c_.get("op") or "equal").strip().lower(), "equal")
        if not p_ or not val_:
            ok = False; break
        if p_ in RID:
            body.append(_indiv_prop_atom(RID[p_], "x", "y"))
            body.append(b.builtin_atom(swb, "y", val_))
        else:
            # 数据属性路径：规则引用的字段名自动注册为 if_class 的 dprop（谓词全部声明=形式合法；
            # 值未灌数时规则形式就绪、待数据到位即可被 SWRL 引擎执行）
            dn = _ensure_dprop(ic, p_)
            if not dn:
                ok = False; break
            body.append(_data_prop_atom(dn, "x", "y"))
            body.append(b.builtin_atom(swb, "y", val_))
    if not ok:
        continue
    b.rule(f"SR{n_sr+1}", body_atoms=body, head_atoms=[b.class_atom(CID[dv], "x")],
           comment=f"{str(rr.get('because') or '')[:60]} → 属于{dv}")
    _structured_src.add(str(rr.get("because") or "")[:60])
    n_sr += 1
for i, r_ in enumerate(card["rules"][:24]):   # 伪规则回落（文档性保留）
    if str(r_.get("condition") or "")[:60] in _structured_src:
        continue
    base = list(CID.values())[0]
    b.rule(f"AR{i+1}", body_atoms=[b.class_atom(base, "x"),
           b.builtin_atom("equal", "x", str(r_["condition"])[:60])],
           head_atoms=[b.builtin_atom("equal", "x", str(r_.get("conclusion", ""))[:40])],
           comment=f"如果{str(r_['condition'])[:50]}…")
TTL = f"{OUT}/mvp_ontology.ttl"
b.serialize(TTL)
vstat = OntologyBuilder.verify(TTL)
try:
    import owlrl
    gx = __import__("rdflib").Graph(); gx.parse(TTL, format="turtle")
    before = len(gx)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(gx)
    nothing = sum(1 for _ in gx.subjects(RDF.type, OWL.Nothing))
    inferred = len(gx) - before
    print(f"[G 推理] OWL RL 物化 +{inferred} | owl:Nothing 冲突 {nothing} 个"
          + (" ⚠️" if nothing else " ✅ 无形式矛盾"))
except Exception as e:
    inferred = nothing = 0
    print(f"[G 推理] 跳过: {e}")
TIME["G_axiom"] = time.time() - t
print(f"[G 公理+TTL] {vstat} | 实例边 {n_lk}（闭合补端点 {n_closed}）| 公理落图 {n_ax_land} "
      f"| 真SWRL {n_sr} | 函数性公理 {len(func_props)} | SKOS术语 {n_term} "
      f"| 公理 {dict(_C2(a['kind'] for a in axioms))}"
      f"（{TIME['G_axiom']:.0f}s）")

# ═══════════════ H 落平台（graph_data + Neo4j + 检索冒烟） ═══════════════
import requests
t = time.time()
BASE = "http://localhost:3001"
s = requests.Session()
JWT = s.post(f"{BASE}/api/auth/login", data={"username": "admin", "password": "cbil123456"},
             timeout=15).json()["access_token"]
H = {"Authorization": f"Bearer {JWT}"}
r = s.post(f"{BASE}/api/projects", json={"name": f"MVP模块构建-{model_name}-{int(time.time())%10000}"},
           headers=H, timeout=15)
pid = r.json()["id"]
s.close(); s = requests.Session(); s.trust_env = False   # A-F 阶段数分钟空闲后旧 TCP 连接已被服务端 keep-alive 掐死（uvicorn 默认 5s），H 段重建会话防 reset
for fp in args.files:
    files = [("files", (os.path.basename(fp), open(fp, "rb")))]
    s.post(f"{BASE}/api/projects/{pid}/documents/upload", headers=H, files=files, timeout=300)
t0 = time.time()
while time.time() - t0 < 300:
    docs_ = s.get(f"{BASE}/api/projects/{pid}/documents", headers=H, timeout=15).json()
    items = docs_.get("items") or docs_.get("documents") or []
    if items and all(x.get("parse_status") not in ("uploaded", "parsing") for x in items):
        break
    time.sleep(5)
print(f"[H 落地] 项目 {pid}，{len(args.files)} 个文件已上传解析（{time.time()-t0:.0f}s）")

nodes, edges = [], []
# 类的属性模板回填（零 LLM）：类该持有"实例应有哪些属性"的模板（schema 层），
# 实例是填了值的模板（data 层）——从采集卡实例聚合每类实际用到的属性键，
# 补上光杆类（此前 36/36 类 properties 全空，是"实例含类属性"错位的另一半）
_cls_props_tmpl = {}
for _i in card["instances"]:
    if isinstance(_i, dict) and _i.get("class"):
        for _k in (_i.get("props") or {}):
            if str(_k).strip():
                _cls_props_tmpl.setdefault(str(_i["class"]), set()).add(str(_k).strip())
_seen_cls_node = set()   # card["classes"] 可能含同名条目；重复 id 会让行表双写撞唯一键整体回滚（327 实锤）
for i, c in enumerate(card["classes"]):
    zh = str(c.get("label") or "").strip()
    if zh and zh in CID and CID[zh] not in _seen_cls_node:
        _seen_cls_node.add(CID[zh])
        props_raw = c.get("properties") or {}
        prop_names = list(props_raw.keys()) if isinstance(props_raw, dict) else [str(p) for p in props_raw]
        # SKOS 术语层接入：altLabel 进类节点 aliases → TBox 索引嵌入文本吃到别名，
        # 类名漂移（同文档不同轮抽成不同类名）时 QA 向量锚定仍能命中
        aliases = [str(a_) for a_ in b.g.objects(EX[CID[zh]], SKOS.altLabel)][:10]
        _csrc = str(c.get("source_document") or "")
        nodes.append({"id": CID[zh], "type": "smoothstep",
                      "data": {"type": "Class", "label": zh, "aliases": aliases,
                               "definition": str(c.get("definition") or "")[:200],
                               "properties": {p: "string" for p in (list(prop_names) + sorted(_cls_props_tmpl.get(zh, set())))[:20] if str(p).strip()},
                               **({"source_document": _csrc, "source_quote": f"类『{zh}』首次声明于 {_csrc}"} if _csrc else {})}})
_inst_meta = {}
for ins in card["instances"]:
    if not isinstance(ins, dict):
        continue
    k = (str(ins.get("label") or "").strip(), ins.get("class"))
    if k[0] and k[1] in CID and k not in _inst_meta:
        _inst_meta[k] = (ins.get("props") or {}, str(ins.get("evidence") or ""))
# 遍历 inst_id 而非 card["instances"]：G 段链接闭合补建的端点个体只在 inst_id 里，
# 漏掉它们会让平台图比 TTL 少一截（接口字段 114 vs 39 的口径差根因）
for (lab, cls), iid in inst_id.items():
    if cls not in CID or not lab:
        continue
    props, evi = _inst_meta.get((lab, cls), ({}, "闭合补建：关系端点无原文实例，按关系定义域/值域兜底创建"))
    _srcdoc = evi.split("[")[0].strip() if "[" in evi else ""   # 证据前缀=来源文件名（平台行表溯源映射键）
    nodes.append({"id": iid, "type": "smoothstep",
                  "data": {"type": "owl:NamedIndividual", "label": lab[:80], "confidence": 0.9,
                           "class_label": cls,
                           "properties": {k: str(v)[:200] for k, v in props.items() if str(k).strip() and v not in (None, "")},
                           "source_quote": evi[:200], "source_document": _srcdoc}})
# rdf:type 边（实例→类节点）：前端画布"展开实例"/实例挂树/详情页实例数全部依赖它。
# 必须进 edges：此前误 append 到 nodes，画布多出上百个 rdf:type 假节点、
# 实例挂树/类型边全丢，行表也会把假节点双写成实体。
for (_il, _ic), _iid in list(inst_id.items()):
    if _ic in CID:
        edges.append({"id": f"t_{_iid}", "type": "smoothstep", "label": "rdf:type",
                      "source": _iid, "target": CID[_ic],
                      "data": {"type": "ObjectProperty", "relation": "rdf:type", "confidence": 1.0}})
# 收编兜底（F 专家"收编到业务对象"纪律的代码防线）：无任何类间边连接的类
# 自动挂顶类「业务对象」（"属于"边）——折叠态不再出现孤岛类
def _ensure_hub_edges(_edges, _cls_nodes, _cid):
    _hub = str(cfg.get("hub_class") or "业务对象")
    _linked = {e.get("source") for e in _edges} | {e.get("target") for e in _edges}
    _hub_id = None
    for _n in _cls_nodes:
        if (_n.get("data") or {}).get("label") == _hub:
            _hub_id = _n["id"]; break
    _iso = []
    for _n in _cls_nodes:
        _nid = _n["id"]
        if _nid == _hub_id or _nid in _linked:
            continue
        if _hub_id is None:
            _hub_id = f"C_hub_{_hub}"
            _edges.append({"id": _hub_id, "type": "smoothstep",
                           "data": {"type": "Class", "label": _hub, "aliases": [],
                                    "definition": "顶类：一切业务概念的收编上位（收编兜底自动生成）",
                                    "properties": {}}})
        _iso.append((_nid, str((_n.get("data") or {}).get("label"))))
    for i, (_nid, _lab) in enumerate(_iso):
        _edges.append({"id": f"hub_{i}_{_lab}"[:40], "type": "smoothstep", "label": "属于",
                       "source": _nid, "target": _hub_id,
                       "data": {"type": "ObjectProperty", "relation": "属于", "kind": "hub", "confidence": 0.8}})
    if _iso:
        print(f"[H 落地] 收编兜底：{_len_fix(len(_iso))} 个孤立类挂入顶类「{_hub}」")

def _len_fix(n):
    return n

# 类间模式边（TBox 层）：折叠态下画布显示类的组织结构（domain→range），数据源=采集卡关系声明
_seen_sch = set()
for _r in card["relations"]:
    _d, _g, _rl = str(_r.get("domain") or "").strip(), str(_r.get("range") or "").strip(), str(_r.get("label") or "").strip()
    if _d in CID and _g in CID and _d != _g and _rl:
        _key = (_rl, _d, _g)
        if _key in _seen_sch:
            continue
        _seen_sch.add(_key)
        edges.append({"id": f"s_{len(_seen_sch)}", "type": "smoothstep", "label": _rl,
                      "source": CID[_d], "target": CID[_g],
                      "data": {"type": "ObjectProperty", "relation": _rl, "kind": "schema", "confidence": 0.9}})
_cls_nodes_canvas = [n for n in nodes if (n.get("data") or {}).get("type") == "Class"]
_ensure_hub_edges(edges, _cls_nodes_canvas, CID)
for lk in card["links"]:
    s_ = next((v for (l, c), v in inst_id.items() if l == lk.get("subject")), None)
    o_ = next((v for (l, c), v in inst_id.items() if l == lk.get("object")), None)
    if s_ and o_ and RID.get(lk.get("predicate")):
        _ev = str(lk.get("evidence") or "")
        edges.append({"id": f"edge_{len(edges)}", "type": "smoothstep", "label": lk["predicate"],
                      "source": s_, "target": o_,
                      "data": {"type": "ObjectProperty", "relation": lk["predicate"], "confidence": 0.9,
                               "source_quote": _ev[:160],
                               "source_document": (_ev.split("[")[0].strip() if "[" in _ev else "")}})
# 公理落位翻译（平台约定，axiom_store.list_axioms 读的是
# classes[].disjoint_with/parent_classes 与 object_properties[].functional——
# schema.axioms 列表平台不读，公理显示 0 的根因）。domain/range 随 op 自身展示，无需翻译。
_cls_by = {str(c.get("label") or "").strip(): c for c in card["classes"] if isinstance(c, dict)}
_op_by = {str(op.get("label") or "").strip(): op for op in rel_list if isinstance(op, dict)}
for _a in axioms:
    _k = str(_a.get("kind") or "")
    if _k in ("subclass_of", "disjoint_with") and isinstance(_a.get("classes"), list) and len(_a["classes"]) == 2:
        _a1, _a2 = str(_a["classes"][0] or "").strip(), str(_a["classes"][1] or "").strip()
        if _k == "subclass_of":
            if _a1 in _cls_by and _a2 in _cls_by:
                _pc = _cls_by[_a1].setdefault("parent_classes", [])
                if _a2 not in _pc:
                    _pc.append(_a2)
        else:
            if _a1 in _cls_by and _a2 in _cls_by:
                for _who, _oth in ((_a1, _a2), (_a2, _a1)):
                    _dw = _cls_by[_who].setdefault("disjoint_with", [])
                    if _oth not in _dw:
                        _dw.append(_oth)
    elif _k == "functional":
        _pl = str(_a.get("rel") or "").strip()
        if _pl in _op_by:
            _op_by[_pl]["functional"] = True
# 平台详情页的统计口径：数据属性读 schema.datatype_properties、业务规则读
# schema.business_rules；只塞进 classes.properties 会让页面显示 0。
_dt_props: list[dict] = []
_seen_dp: set[tuple[str, str]] = set()
for _c in card["classes"]:
    _cl = str(_c.get("label") or "").strip()
    _pr = _c.get("properties") or {}
    _names = list(_pr.keys()) if isinstance(_pr, dict) else list(_pr)
    for _pn in _names:
        _n = str(_pn or "").strip()
        if _cl and _n and (_cl, _n) not in _seen_dp:
            _seen_dp.add((_cl, _n))
            _dt_props.append({"name": _n, "domain": _cl, "data_type": "string"})
for _i in card["instances"]:
    if not isinstance(_i, dict):
        continue
    _cl = str(_i.get("class") or "").strip()
    for _pn in (_i.get("props") or {}):
        _n = str(_pn or "").strip()
        if _cl and _n and (_cl, _n) not in _seen_dp:
            _seen_dp.add((_cl, _n))
            _dt_props.append({"name": _n, "domain": _cl, "data_type": "string"})

gd = {"nodes": nodes, "edges": edges,
      "schema": {"classes": card["classes"], "object_properties": rel_list,
                 "datatype_properties": _dt_props,
                 "business_rules": card["rules"],
                 "axioms": axioms, "spec_card_pending": card["pending"]}}
os.chdir(BK)
from sqlalchemy.orm.attributes import flag_modified
from app.infrastructure.database import SessionLocal, Project
from app.infrastructure.neo4j_client import neo4j_client
db = SessionLocal()
proj = db.query(Project).filter(Project.id == pid).first()
proj.graph_data = gd; flag_modified(proj, "graph_data"); db.commit()
# 平台页面依赖的收尾数据（此前脚本完全没写）：构建报告、能力问题、审核待办
try:
    from app.services.build_report import generate_build_report
    generate_build_report(db, pid, depth_label="MVP", model_name=model_name)
except Exception as _e:  # noqa: BLE001
    print(f"[H 落地] 构建报告生成跳过：{_e}")
try:
    from app.infrastructure.database import CompetencyQuestion, ReviewItem
    _qs: list[str] = []
    for _i in card["instances"]:
        if not isinstance(_i, dict):
            continue
        _cl = str(_i.get("class") or "")
        if "能力问题" in _cl:
            _q = str(_i.get("label") or "").strip()
            if _q and _q not in _qs:
                _qs.append(_q)
    for _i, _q in enumerate(_qs):
        db.add(CompetencyQuestion(project_id=pid, question=_q[:500], source="auto",
                                  sort_order=_i, created_by=1))
    _n_rev = 0
    for _msg in (card.get("pending") or []):
        _t = str(_msg or "").strip()
        if not _t:
            continue
        db.add(ReviewItem(project_id=pid, item_type="missing_evidence",
                          payload={"message": _t[:1000], "source": "mvp"},
                          reason=_t[:250], priority="low", status="pending"))
        _n_rev += 1
    if _qs or _n_rev:
        db.commit()
    if _qs:
        print(f"[H 落地] 能力问题导入 {len(_qs)} 条")
    if _n_rev:
        print(f"[H 落地] 待确认项写入审核队列 {_n_rev} 条")
except Exception as _e:  # noqa: BLE001
    db.rollback()
    print(f"[H 落地] 能力问题/审核项写入跳过：{_e}")
t5 = time.time()
ok = neo4j_client.sync_graph(pid, gd)
print(f"[H 落地] DB+Neo4j {time.time()-t5:.1f}s (ok={ok})，{len(nodes)} 节点 {len(edges)} 边")
# Neo4j 关系丢失自愈（332 实锤：sync ok=True 但关系 0——静默丢失）：
# 落地后数关系，与 graph_data 边数偏差 >10% 即整图重放一次（幂等：先 DELETE 后写）
try:
    with neo4j_client.driver.session() as _s:
        _nr = _s.run("MATCH (a)-[r]->(b) WHERE a.project_id=$p RETURN count(r) AS c",
                     p=pid).single()["c"]
    _expect = len(gd.get("edges") or [])
    if _nr < _expect * 0.9:
        print(f"[H 落地] ⚠️ Neo4j 关系 {_nr} < 预期 {_expect}，自动重放整图同步")
        neo4j_client.sync_graph(pid, gd)
        with neo4j_client.driver.session() as _s:
            _nr2 = _s.run("MATCH (a)-[r]->(b) WHERE a.project_id=$p RETURN count(r) AS c",
                          p=pid).single()["c"]
        print(f"[H 落地] 重放后关系 {_nr2}")
except Exception as _e:
    print(f"[H 落地] 关系校验跳过：{_e}")

# 版本与能力问题评测（平台页面依赖；失败不阻断落地）
try:
    from app.tasks.extract_tasks import _auto_version
    _vn = _auto_version(db, pid, "full", "MVP 脚本构建")
    if _vn:
        print(f"[H 落地] 自动版本 v{_vn}")
except Exception as _e:  # noqa: BLE001
    print(f"[H 落地] 自动版本跳过：{_e}")
try:
    from app.services.cq_service import ensure_rdf_graphs, evaluate_cqs
    ensure_rdf_graphs(pid, rebuild=True)
    _ev = evaluate_cqs(db, pid, trigger="auto_post_extract")
    db.commit()  # 评测记录落库（平台管线同样在 CQ 段提交）
    if _ev is not None:
        print(f"[H 落地] 能力问题评测 {_ev.answerable_count}/{_ev.total_cq}")
except Exception as _e:  # noqa: BLE001
    print(f"[H 落地] 能力问题评测跳过：{_e}")

for q in ["贷款申请流程使用了哪些接口，分别调用了哪个后端系统", "还款账号校验矩阵中产品0122的校验配置是什么"]:
    r6 = s.post(f"{BASE}/api/projects/{pid}/retrieval", headers=H, timeout=60,
                json={"question": q, "top_k": 8, "mode": "retrieve"}).json()
    rs = r6.get("results") or []
    kinds = {}
    for x in rs:
        kinds[x.get("ref_type")] = kinds.get(x.get("ref_type"), 0) + 1
    print(f"[H 检索] {q[:26]}… total={r6.get('total')} {kinds} {r6.get('latency_ms')}ms")
TIME["H_land"] = time.time() - t

# ═══════════════ 报表 ═══════════════
total_s = time.time() - T0
from collections import Counter as _CR
rep = {"model": model_name, "files": [d["file"] for d in docs],
       "time_phase": {k: round(v, 2) for k, v in TIME.items()}, "total_s": round(total_s, 1),
       "llm": {"calls": IO["calls"], "secs": round(IO["secs"], 1), "in_bytes": IO["in"],
               "stage_max_input": {k: v["in_max"] for k, v in IO["stages"].items()}},
       "result": {"classes": len(card["classes"]), "instances": len(card["instances"]),
                  "links": len(card["links"]), "rules": len(card["rules"]),
                  "axioms": len(axioms), "ttl": vstat, "project_id": pid},
       "coverage": coverage,
       "narrative_routing": _narr_stat,
       "quality": {
           "checklist": card.get("checklist") or [],
           "checklist_covered": sum(1 for a in (card.get("checklist") or []) if a.get("status") == "有据"),
           "checklist_total": 29,
           "pending_n": len(card.get("pending") or []),
           "rejected_axioms": rejected_ax,
           "swrl_structured": n_sr,
           "functional_props": func_props,
           "skos_terms": n_term,
           "instance_dist": dict(_CR(str(i.get("class")) for i in card["instances"] if isinstance(i, dict))),
           "edge_dist": dict(_CR(str(l.get("predicate")) for l in card["links"] if isinstance(l, dict))),
       }}
_multi = [x for x in card["instances"] if isinstance(x, dict) and len(x.get("_files") or []) >= 2]
rep["quality"]["cross_doc"] = {
    "shared_instances": len(_multi),
    "samples": [{"label": x["label"], "class": x["class"], "files": x["_files"]} for x in _multi[:12]]}
json.dump(rep, open(f"{OUT}/build_report.json", "w"), ensure_ascii=False, indent=1)
print(f"\n═══ 分阶段耗时 ═══")
for k, v in TIME.items():
    print(f"  {k}: {v:.1f}s")
print(f"LLM {IO['calls']} 次/{IO['secs']:.0f}s | 各阶段单次最大输入 "
      f"{ {k: str(v['in_max']) + 'B' for k, v in IO['stages'].items()} }")
q = rep["quality"]
print(f"质量：句式覆盖 {q['checklist_covered']}/29 有据 | pending {q['pending_n']} | "
      f"可疑公理拦截 {len(q['rejected_axioms'])} | 真SWRL {q['swrl_structured']} | "
      f"SKOS术语 {q['skos_terms']} | 函数性 {len(q['functional_props'])} | 类分布 {q['instance_dist']}")
print(f"跨文档：{q['cross_doc']['shared_instances']} 个实例被 ≥2 个文件共同支撑，样例 "
      + json.dumps([f"{x['label']}({x['class']})←{len(x['files'])}文件" for x in q['cross_doc']['samples'][:6]],
                   ensure_ascii=False))
print(f"总计 {total_s:.0f}s | 项目 {pid} | 产物 {OUT}/")
