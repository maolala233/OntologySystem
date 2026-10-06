# scripts/backfill_vectors.py - 存量文档切片向量回填（一次性运维脚本）
# 背景：解析管道此前未实装 Milvus 入库，QA 向量召回无数据（07 §4 缺失的入库环节）。
# 用法：cd src/backend && python scripts/backfill_vectors.py [project_id]
#   不带参数 = 回填全部项目已解析文档；带参数 = 只回填指定项目。

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infrastructure.database import DocumentChunk, SessionLocal, UploadedDocument
from app.infrastructure.vector_client import VectorStoreManager
from app.services.document_pipeline import _sync_chunks_to_vector


class _ChunkAdapter:
    """_sync_chunks_to_vector 期望 chunk 带 .text/.start_index/.end_index，DB 行字段名不同。"""

    def __init__(self, row: DocumentChunk):
        self.text = row.text
        self.start_index = row.char_start or 0
        self.end_index = row.char_end or 0


def main() -> None:
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    vm = VectorStoreManager()
    if not vm.is_enabled:
        print("Milvus 未启用，无法回填")
        return
    db = SessionLocal()
    q = db.query(UploadedDocument).filter(
        UploadedDocument.parse_status == "parsed",
        UploadedDocument.deleted_at.is_(None),
    )
    if pid is not None:
        q = q.filter(UploadedDocument.project_id == pid)
    docs = q.all()
    print(f"待回填文档 {len(docs)} 个" + (f"（项目 {pid}）" if pid else ""))
    ok = skip = fail = 0
    for doc in docs:
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        if not chunks:
            skip += 1
            print(f"  - 文档 {doc.id} {doc.filename[:40]} 无切片，跳过")
            continue
        n = _sync_chunks_to_vector(doc, [_ChunkAdapter(c) for c in chunks])
        if n > 0:
            ok += 1
            print(f"  ✓ 文档 {doc.id} {doc.filename[:40]} 入库 {n} 条")
        else:
            fail += 1
            print(f"  ✗ 文档 {doc.id} {doc.filename[:40]} 入库失败（详见后端日志）")
    print(f"完成：成功 {ok}，跳过 {skip}，失败 {fail}")
    print("Milvus 当前行数:", vm.collection.num_entities)


if __name__ == "__main__":
    main()
