# 输出契约

## 事实回答

```json
{
  "question": "...",
  "answer": "先给结论，再给分组/清单",
  "total": 152,
  "breakdown": [{"group": "登记人", "value": "孙博涵", "count": 56}],
  "evidence": [{"dataId": "280502", "label": "C2504051", "doc": "...", "quote": "..."}],
  "warnings": []
}
```

## 计数/分组

- 必须使用脚本或 `query_facts` 的 `total`；
- `breakdown` 各组合计必须等于 `total`；
- 缺失属性使用 `未标注`，不能省略；
- 任何 `closure.ok=false` 都必须写进 `warnings`，并说明是取数未完成还是源数据缺失。

## 关系/路径

- 有路径：给出节点序列、关系谓词、跳数；
- 无路径：`"图谱中未建立该关系"`，并说明查询了哪两个实体；
- 语义相似只能作为候选，不能当成已建立关系。

## 溯源

每条结论至少带一个证据：

```json
{"dataId": "...", "label": "...", "ref_type": "graph_node|doc_chunk|tool_query", "quote": "..."}
```

原文引用过长时只保留关键片段，但必须保留文档名和定位。
