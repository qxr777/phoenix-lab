#!/usr/bin/env python3
"""
文档分块策略

提供三种分块策略:
  1. fixed     — 固定长度切片，按字符数(非 token)切分，支持 overlap
  2. semantic  — 按 Markdown 标题层级递归切分，保持语义完整性
  3. paragraph — 按段落边界切分，确保不跨越双换行符

每项策略返回 List[str]，其中每个元素是一个 chunk。
"""

import re
from typing import List


def fixed_size_chunk(text: str, chunk_size: int = 512, overlap: int = 50) -> List[str]:
    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            last_period = text.rfind("。", start, end)
            last_newline = text.rfind("\n", start, end)
            last_boundary = max(last_period, last_newline)
            if last_boundary > start + chunk_size // 2:
                end = last_boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if end < text_len else text_len

    return chunks


def _split_by_headings(text: str) -> List[tuple[int, str, str]]:
    heading_pattern = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(text))

    if not matches:
        return [(0, "", text)]

    sections = []
    for i, match in enumerate(matches):
        level = match.group(1)
        title = match.group(2).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        sections.append((len(level), title, content))

    return sections


def semantic_chunk(text: str, max_chunk_size: int = 1024, overlap: int = 0) -> List[str]:
    sections = _split_by_headings(text)
    chunks = []

    for level, title, content in sections:
        if len(content) <= max_chunk_size:
            chunks.append(content)
        else:
            paragraphs = content.split("\n\n")
            current_chunk = ""
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if len(current_chunk) + len(para) + 2 > max_chunk_size and current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = para
                else:
                    current_chunk += "\n\n" + para if current_chunk else para
            if current_chunk:
                chunks.append(current_chunk.strip())

    return chunks


def paragraph_aware_chunk(text: str, max_chunk_size: int = 512, overlap: int = 0) -> List[str]:
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_len = len(para)

        if current_len + para_len > max_chunk_size and current_chunk:
            chunks.append(current_chunk.strip())

            if overlap > 0:
                overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                current_chunk = overlap_text + "\n\n" + para
                current_len = len(overlap_text) + 2 + para_len
            else:
                current_chunk = para
                current_len = para_len
        else:
            current_chunk += "\n\n" + para if current_chunk else para
            current_len = current_len + 2 + para_len if current_chunk else para_len

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def chunk_document(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    strategy: str = "fixed",
) -> List[str]:
    if strategy == "fixed":
        return fixed_size_chunk(text, chunk_size, chunk_overlap)
    elif strategy == "semantic":
        return semantic_chunk(text, max_chunk_size=chunk_size, overlap=chunk_overlap)
    elif strategy == "paragraph":
        return paragraph_aware_chunk(text, max_chunk_size=chunk_size, overlap=chunk_overlap)
    else:
        raise ValueError(f"未知的分块策略: {strategy}")
