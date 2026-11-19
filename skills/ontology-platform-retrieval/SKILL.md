---
name: ontology-platform-retrieval
description: 查询 Ontology 平台项目图谱中的实体、关系、接口清单、路径与原文证据，并做可校验的计数/分组。当用户问“有多少个/有哪些/怎么关联/依据是什么”，或需要从接口索引、字段表、图谱中取稳定检索结果时使用。
---

# 本体图谱检索 Skill

这是一个**带脚本**的检索包：模型负责选路径，脚本负责分页取全量、计数闭合和输出证据，避免只数当前页、漏项或口算分组。

## 先决条件

智能体运行环境已注入：

```text
MCP_ENDPOINT  例如 http://127.0.0.1:3001/mcp
MCP_TOKEN     sk-mcp-*（只读即可）
```

没配置时先向用户要，**不要把令牌写进回答或代码**。

## 先做连通性检查

```bash
python scripts/check.py
```

它会检查：握手、工具列表、图谱 Schema，以及 `query_facts` 是否可用。失败时按错误提示处理，不要猜数据。

## 用脚本做稳定查询

脚本都是只读的，JSON 输出到 stdout，错误到 stderr。

### 1. 看结构

```bash
python scripts/retrieve.py schema
python scripts/retrieve.py types
```

### 2. 计数 / 按属性分组

```bash
python scripts/retrieve.py count  --class 前端接口
python scripts/retrieve.py group  --class 前端接口 --by 登记人
```

`group` 会分页取全量，并强制校验：

```text
各组之和 + 未标注 == total
```

不闭合时脚本以退出码 2 结束，并输出 `closure.ok=false`，此时回答里必须说明缺口，不能硬凑。

### 3. 精确过滤 / 语义召回

```bash
python scripts/retrieve.py find --class 前端接口 --filter "登记人='张茂莲'"
python scripts/retrieve.py semantic --query "合同查询接口" --top-k 10
```

### 4. 关系与路径

```bash
python scripts/retrieve.py neighbors --id C2504051 --direction BOTH
python scripts/retrieve.py relations --from C2504051 --to C2504052
python scripts/retrieve.py subgraph --id C2504051 --hops 2
python scripts/retrieve.py path --from C2504051 --to C2504052 --mode shortest
```

`find` 返回 `dataId/label/class/props`；`semantic` 返回原文切片和分数，需要再用 `find` / `entity_get` 落到具体实体。

## 回答规则

1. 先给结论和总数；
2. 再给分组/清单；
3. 最后给证据：`dataId`、label、原文切片或文档名；
4. 图谱没有直接关系就明确说“未建立该关系”，不要用语义相似猜成关系；
5. 计数/分组必须引用脚本输出的 `total` 和 `closure`，不要口算。

## 何时不用脚本

如果 harness 直接调用 MCP 工具更顺手，也可以按 `references/tool-routing.md` 的映射调用；但计数和分组仍必须使用 `query_facts` 的 `total`，并自行校验各组之和。

## 写入

只读令牌不能写入。需要新增实体/关系时使用 `can_write` 令牌调用 `add_entity` / `add_relationship`，并在回答中说明写入结果。
