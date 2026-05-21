"""
文本分块工具
将长文本切分为带重叠的chunks
"""
import logging
from typing import List, Dict, Any


def chunk_text(text: str, chunk_size: int = 1000, overlap_percentage: int = 10) -> List[Dict[str, Any]]:
    """
    将文本切分为带重叠的块

    Args:
        text: 原始文本内容
        chunk_size: 每个块的字符长度
        overlap_percentage: 相邻块之间的重叠百分比(0-50)

    Returns:
        包含chunk_id、text、start_char、end_char、size的字典列表
    """
    if not text or not text.strip():
        return []

    # 限制overlap_percentage在合理范围内
    if overlap_percentage < 0:
        overlap_percentage = 0
    elif overlap_percentage > 50:
        overlap_percentage = 50

    overlap_size = int(chunk_size * overlap_percentage / 100)
    chunks = []
    start = 0
    chunk_id = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk_content = text[start:end]

        chunks.append({
            "chunk_id": f"chunk_{chunk_id}",
            "text": chunk_content,
            "start_char": start,
            "end_char": end,
            "size": len(chunk_content),
            "index": chunk_id,
        })

        if end >= len(text):
            break

        next_start = end - overlap_size

        # 确保每次前进至少1个字符，避免无限循环
        if next_start <= start:
            next_start = start + max(1, chunk_size - overlap_size)

        start = next_start
        chunk_id += 1

    logger = logging.getLogger("graph_injector.chunker")
    logger.info(f"文本分块完成: 总长度={len(text)}, 块数={len(chunks)}, "
                f"chunk_size={chunk_size}, overlap={overlap_percentage}%")

    return chunks
