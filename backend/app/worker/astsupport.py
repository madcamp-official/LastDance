import hashlib
import logging
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)

try:
    from tree_sitter import Language, Parser, Tree, Node
    import tree_sitter_python
    import tree_sitter_cpp

    # tree_sitter_language_pack lazily downloads compiled grammars from GitHub
    # Releases on first use — fails hard (DownloadError) with no network egress,
    # which the deployed backend doesn't have. Build Language objects directly
    # from the precompiled grammar wheels instead: fully offline, no runtime fetch.
    _LANGUAGES = {
        "python": Language(tree_sitter_python.language()),
        "cpp": Language(tree_sitter_cpp.language()),
    }
    _TS_AVAILABLE = True
except ImportError:  # tree-sitter 미설치 환경 → timing_only 분석만 수행
    _TS_AVAILABLE = False
    _LANGUAGES = {}
    Parser = Tree = Node = None  # type: ignore


# 프런트가 보내는 lang 문자열 → tree-sitter 파서 이름 (dev-plan §4.1 Step 1)
_LANG_ALIASES = {
    "cpp": "cpp", "c++": "cpp", "cpp17": "cpp", "cpp20": "cpp", "cpp23": "cpp",
    "python": "python", "python3": "python", "py": "python", "pypy3": "python",
    "c": "c",
    "java": "java",
    "rust": "rust",
    "go": "go",
    "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript",
}


def normalize_lang(lang: str) -> Optional[str]:
    return _LANG_ALIASES.get((lang or "").strip().lower())


def load_parser(lang: str):
    """파서 로드. 미지원 언어/미설치면 None → 호출부가 timing_only로 강등."""
    if not _TS_AVAILABLE:
        return None
    name = normalize_lang(lang)
    if name is None or name not in _LANGUAGES:
        return None
    try:
        return Parser(_LANGUAGES[name])
    except Exception:
        logger.exception("tree-sitter parser init failed for lang=%s", name)
        return None


def walk_nodes(root) -> Iterator["Node"]:
    """서브트리 전체를 전위 순회 (재귀 없이)."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def collect_by_type(root, types: set) -> List["Node"]:
    return [n for n in walk_nodes(root) if n.type in types]


def node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


# 구조 스케치에서 무시할 말단(식별자/리터럴) — TYPO 판정(§4.1 Step 4)에 필요:
# "삭제 후 동일 구조로 재작성"은 이름이 바뀌어도 구조가 같으면 TYPO다.
_SKETCH_IGNORE = {
    "identifier", "field_identifier", "type_identifier", "namespace_identifier",
    "number_literal", "string_literal", "char_literal", "string", "integer",
    "float", "comment", "string_content",
}


def structure_sketch(root) -> str:
    """식별자·리터럴 텍스트를 무시한 트리 구조 해시 (서브트리 해시 diff 기반)."""
    parts: List[str] = []
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if node.type in _SKETCH_IGNORE:
            parts.append(f"{depth}:_")
            continue
        parts.append(f"{depth}:{node.type}")
        for child in reversed(node.named_children):
            stack.append((child, depth + 1))
    return hashlib.md5("|".join(parts).encode()).hexdigest()
