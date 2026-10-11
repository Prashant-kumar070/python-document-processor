import re
from typing import Any


class ChunkingService:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 150):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be between zero and chunk_size - 1")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_text(self, text: str) -> list[str]:
        return [
            chunk["content"]
            for chunk in self.chunk_pages([{"page_number": None, "text": text}])
        ]

    def chunk_pages(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for page in pages:
            page_number = page.get("page_number")
            page_text = page.get("text", "")
            page_chunks = self._chunk_page(page_text)
            chunks.extend(
                {
                    "chunk_index": len(chunks),
                    "content": content,
                    "page_number": page_number,
                }
                for content in page_chunks
            )
        return chunks

    def _chunk_page(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []

        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        chunks: list[str] = []
        current = ""
        current_is_overlap = False

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > self.chunk_size:
                if current and not current_is_overlap:
                    chunks.append(current)
                long_chunks = self._split_long_text(sentence)
                chunks.extend(long_chunks)
                current = self._overlap_suffix(long_chunks[-1], self.chunk_overlap)
                current_is_overlap = bool(current)
                continue

            candidate = f"{current} {sentence}".strip()
            if len(candidate) <= self.chunk_size:
                current = candidate
                current_is_overlap = False
                continue

            if current and not current_is_overlap:
                chunks.append(current)
            previous_text = chunks[-1] if current_is_overlap else current
            overlap_limit = min(
                self.chunk_overlap,
                self.chunk_size - len(sentence) - 1,
            )
            overlap = self._overlap_suffix(previous_text, overlap_limit)
            current = f"{overlap} {sentence}".strip()
            current_is_overlap = False

        if current and not current_is_overlap:
            chunks.append(current)
        return chunks

    def _split_long_text(self, text: str) -> list[str]:
        chunks: list[str] = []
        start = 0

        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start, end + 1)
                if boundary > start:
                    end = boundary
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            next_start = end - self.chunk_overlap
            if next_start <= start:
                next_start = end
            start = next_start
            while start < len(text) and text[start].isspace():
                start += 1

        return chunks

    @staticmethod
    def _overlap_suffix(text: str, max_chars: int) -> str:
        if max_chars <= 0 or not text:
            return ""

        start = max(0, len(text) - max_chars)
        if start > 0 and not text[start - 1].isspace():
            boundary = text.find(" ", start)
            if boundary == -1:
                return ""
            start = boundary + 1
        return text[start:].strip()
