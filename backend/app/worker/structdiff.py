"""Structural Diff Extractor (llm-structural-classifier-addendum.md §2~§3).

세션 종료 시점에 1회 실행되는 결정론적 단계. 디바운스된 매처 실행 시점마다
캡처한 구조 스냅샷들 사이의 GumTree식 diff(insert/delete)를 계산하고,
규칙 매처(Step 5)가 어떤 패턴 윈도우에도 귀속시키지 못한 시간 구간을
UNMATCHED 세그먼트로 묶는다.

LLM에는 여기서 만든 구조 이벤트만 전달된다 — 노드 타입, 트리 깊이,
서브트리 해시, 크기, 시각뿐이며 원본 코드/식별자 텍스트는 포함하지 않는다.
(재귀 판정용 callee_is_self는 이름 대신 불리언만 전달)
"""

from collections import Counter
from typing import List, NamedTuple, Optional, Tuple

from app.schema.analysis import DiffEvent, PatternWindowResult, SubtreeShape, UnmatchedSegment
from app.worker.astsupport import node_text, structure_sketch, walk_nodes

# diff 대상으로 삼는 구조 노드 타입 (cpp / python)
_STRUCT_TYPES = {
    "function_definition",
    "for_statement", "for_range_loop", "while_statement", "do_statement",
    "if_statement",
    "call_expression", "call",
    "subscript_expression", "subscript",
    "assignment_expression", "assignment", "augmented_assignment",
    "declaration", "return_statement",
}

_HASH_LEN = 6            # addendum §3 예시와 동일한 축약 해시 길이
SEGMENT_GAP_MS = 30_000  # 이보다 diff 이벤트 간격이 벌어지면 세그먼트 분리
MIN_SEGMENT_EVENTS = 3   # 이보다 diff 이벤트가 적은 세그먼트는 노이즈로 버림
MAX_SEGMENTS = 8         # 세션당 LLM에 넘길 세그먼트 상한 (비용 통제, §7.4 사상)
MAX_EVENTS_PER_SEGMENT = 40


class ShapeEntry(NamedTuple):
    """스냅샷 간 multiset 비교 단위 (전부 이름-무관 정보만)."""
    node_type: str
    parent_type: str
    depth: int
    subtree_hash: str
    size_nodes: int
    callee_is_self: bool


def _function_names(root, source: bytes) -> set:
    names = set()
    for n in walk_nodes(root):
        if n.type != "function_definition":
            continue
        name_node = n.child_by_field_name("name") or n.child_by_field_name("declarator")
        if name_node is None:
            continue
        for d in walk_nodes(name_node):
            if d.type == "identifier":
                names.add(node_text(d, source))
                break
    return names


class ShapeSnapshot:
    """매처 실행 시점의 구조 노드 multiset. diff 계산에만 쓰인다."""

    def __init__(self, tree, source: bytes) -> None:
        self.entries: List[ShapeEntry] = []
        if tree is None:
            return
        root = tree.root_node
        func_names = _function_names(root, source)
        for n in walk_nodes(root):
            if n.type not in _STRUCT_TYPES:
                continue
            depth = 0
            cursor = n.parent
            while cursor is not None:
                depth += 1
                cursor = cursor.parent
            callee_is_self = False
            if n.type in ("call_expression", "call"):
                fn = n.child_by_field_name("function")
                callee_is_self = fn is not None and node_text(fn, source) in func_names
            self.entries.append(
                ShapeEntry(
                    node_type=n.type,
                    parent_type=n.parent.type if n.parent is not None else "",
                    depth=depth,
                    subtree_hash=structure_sketch(n)[:_HASH_LEN],
                    size_nodes=sum(1 for _ in walk_nodes(n)),
                    callee_is_self=callee_is_self,
                )
            )


def diff_snapshots(before: Optional[ShapeSnapshot], after: ShapeSnapshot, t_ms: int) -> List[DiffEvent]:
    """두 스냅샷 사이 구조 변화를 insert/delete 이벤트로 (결정론적 순서)."""
    b = Counter(before.entries if before is not None else [])
    a = Counter(after.entries)
    events: List[DiffEvent] = []
    for op, delta in (("insert", a - b), ("delete", b - a)):
        for e in sorted(delta):
            for _ in range(delta[e]):
                events.append(
                    DiffEvent(
                        t_ms=t_ms,
                        op=op,
                        node_type=e.node_type,
                        parent_type=e.parent_type,
                        depth=e.depth,
                        subtree_hash=e.subtree_hash,
                        size_nodes=e.size_nodes,
                        callee_is_self=e.callee_is_self,
                    )
                )
    return events


def final_subtree_shape(tree, source: bytes) -> Optional[SubtreeShape]:
    """세션 종료 시점 트리의 이름-무관 요약 (addendum §3 final_subtree_shape)."""
    if tree is None:
        return None
    root = tree.root_node
    child_types: List[str] = []
    max_depth = 0
    has_self_call = False
    func_names = _function_names(root, source)
    for n in walk_nodes(root):
        if n.type not in _STRUCT_TYPES:
            continue
        child_types.append(n.type)
        depth = 0
        cursor = n.parent
        while cursor is not None:
            depth += 1
            cursor = cursor.parent
        max_depth = max(max_depth, depth)
        if n.type in ("call_expression", "call"):
            fn = n.child_by_field_name("function")
            if fn is not None and node_text(fn, source) in func_names:
                has_self_call = True
    return SubtreeShape(
        root_type=root.type,
        child_types_multiset=sorted(child_types)[:30],
        max_depth=max_depth,
        has_self_call=has_self_call,
    )


def extract_unmatched_segments(
    timeline: List[DiffEvent],
    windows: List[PatternWindowResult],
    tree,
    source: bytes,
) -> List[UnmatchedSegment]:
    """규칙 매처 윈도우에 덮이지 않은 diff 이벤트를 시간 인접 그룹으로 묶는다."""
    covered: List[Tuple[int, int]] = [(w.t_start_ms, w.t_complete_ms) for w in windows]
    uncovered = [
        e for e in timeline
        if not any(lo <= e.t_ms <= hi for lo, hi in covered)
    ]
    if not uncovered:
        return []

    groups: List[List[DiffEvent]] = [[uncovered[0]]]
    for e in uncovered[1:]:
        if e.t_ms - groups[-1][-1].t_ms <= SEGMENT_GAP_MS:
            groups[-1].append(e)
        else:
            groups.append([e])

    shape = final_subtree_shape(tree, source)
    segments: List[UnmatchedSegment] = []
    for g in groups:
        if len(g) < MIN_SEGMENT_EVENTS:
            continue
        segments.append(
            UnmatchedSegment(
                segment_id=f"seg_{len(segments)}",
                t_start_ms=g[0].t_ms,
                t_end_ms=g[-1].t_ms,
                diff_events=g[:MAX_EVENTS_PER_SEGMENT],
                final_subtree_shape=shape,
            )
        )
        if len(segments) >= MAX_SEGMENTS:
            break
    return segments
