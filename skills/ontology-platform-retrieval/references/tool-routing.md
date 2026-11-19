# 工具路由与稳定口径

## 兼容别名

旧版本工具名仍可调用，但不再出现在 `tools/list`，避免和文档契约重复：

| 旧名 | 等价新名 |
|---|---|
| `get_entity` | `entity_get` |
| `get_neighbors` | `neighbors_get` |
| `get_ontology_schema` | `graph_schema_get` |

`retrieve` 与 `semantic_search` 不是重复：前者走问答检索链返回 sources，后者按语义返回图谱/原文候选；`query_facts` 与 `entity_find` 也不是重复：前者适合全量计数/分页，后者适合带过滤条件的实体筛选。

## 按任务选工具

| 任务 | 首选 | 说明 |
|---|---|---|
| 看项目有哪些类 | `graph_schema_get` / `entity_type_list` | 先确认 label，再查实例 |
| 看关系类型 | `relation_type_list` | 确认 domain/range，避免把相似当关系 |
| 精确清单/过滤 | `entity_find` | filter 支持 `=`、`in`、`and`、`or`、括号 |
| 全量计数 | `query_facts(kind=entities)` | 用 `total`，`items` 可能被分页截断 |
| 按属性分组 | `query_facts` 分页取全量后本地聚合 | 各组之和必须等于 `total` |
| 语义召回 | `semantic_search` | 返回原文切片，不等于实体关系 |
| 单实体详情 | `entity_get` / `entities_get` | 用 `dataId` / `uri` |
| 邻居 | `neighbors_get` | direction=OUT/IN/BOTH |
| 两点关系 | `relations_between` | 明确方向；不要靠语义猜 |
| 子图 | `subgraph_expand` | 控制 hops/maxNodes，避免爆量 |
| 路径 | `shortest_path` / `paths_find` / `path_exists` | 最多 4 跳 |
| 原文问答 | `ask` | 仍需保留 sources 做溯源 |

## 计数闭合

`query_facts` 返回：

```json
{"total": 152, "returned": 50, "has_more": true, "next_offset": 50, "items": []}
```

- `total` 才是全集；
- 必须按 `next_offset` 翻页到 `has_more=false` 再分组；
- 分组时空值/缺失值记为 `未标注`；
- 校验 `sum(groups) == total`，不成立时输出 `closure.ok=false`，不得硬答。

## 数据隔离与错误

- 令牌绑定单项目，跨项目查询会被拒绝；
- 令牌无效/过期：HTTP 401 + JSON-RPC 认证错误；
- 限流：读 60/分、写 20/分/令牌；
- 关系不存在时明确说“未建立”，不要编造路径。

## 输出约定

```json
{
  "ok": true,
  "total": 152,
  "groups": [{"value": "孙博涵", "count": 56}],
  "closure": {"ok": true, "sum": 152, "diff": 0}
}
```

回答必须带 `total`、分组和证据；脚本的 `ok`/`closure` 优先于模型口算。
