"""Structural Diff Extractor (llm-structural-classifier-addendum.md §2~§3).

세션 종료 시점에 1회 실행되는 결정론적 단계. 디바운스된 매처 실행 시점마다
캡처한 구조 스냅샷들 사이의 GumTree식 diff(insert/delete/move)를 계산하고,
규칙 매처(Step 5)가 어떤 패턴 윈도우에도 귀속시키지 못한 시간 구간을
UNMATCHED 세그먼트로 묶는다.

LLM에는 여기서 만든 구조 이벤트만 전달된다 — 노드 타입, 구조 조상 경로,
서브트리 해시, 크기, 시각뿐이며 원본 코드/식별자 텍스트는 포함하지 않는다.
(재귀 판정용 callee_is_self는 이름 대신 불리언만 전달)

parent_type/depth는 addendum §3 예시와 같은 눈금을 쓴다: compound_statement 같은
중간 노드를 건너뛰고 가장 가까운 '구조' 조상(_STRUCT_TYPES)을 부모로 본다.
"""

from collections import Counter
from typing import List, NamedTuple, Optional, Tuple

from app.schema.analysis import (
    AstSnapshot,
    AstTreeEvolution,
    DiffEvent,
    PatternWindowResult,
    SubtreeShape,
    UnmatchedSegment,
)
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
_ASSIGN_TYPES = {"assignment_expression", "assignment", "augmented_assignment"}
_SUBSCRIPT_TYPES = {"subscript_expression", "subscript"}

_HASH_LEN = 6            # addendum §3 예시와 동일한 축약 해시 길이
_TREE_HASH_LEN = 12      # 트리 전체 해시는 세션 내 스냅샷 동일성 판정용 — 충돌 여유를 더 둔다
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


def _struct_ancestry(n) -> Tuple[str, int]:
    """(가장 가까운 구조 조상 타입, 구조 조상 개수 + 1 = depth).

    addendum §3 예시: function_definition 안의 while_statement가 depth=2,
    그 while 안의 call_expression이 depth=3.
    """
    parent_type = ""
    depth = 1
    cursor = n.parent
    while cursor is not None:
        if cursor.type in _STRUCT_TYPES:
            if not parent_type:
                parent_type = cursor.type
            depth += 1
        cursor = cursor.parent
    if not parent_type:
        # 구조 조상이 없으면 트리 루트(translation_unit/module)를 부모로
        root = n
        while root.parent is not None:
            root = root.parent
        parent_type = root.type if root is not n else ""
    return parent_type, depth


def _has_visited_array_pattern(root) -> bool:
    """Step 5 규칙(BFS 방문 배열/DFS 방문 마킹)에서 쓰는 불리언 피처:
    배열 원소 대입(subscript LHS assignment)이 존재하는가."""
    for n in walk_nodes(root):
        if n.type not in _ASSIGN_TYPES:
            continue
        left = n.child_by_field_name("left")
        if left is not None and left.type in _SUBSCRIPT_TYPES:
            return True
    return False


class ShapeSnapshot:
    """매처 실행 시점의 구조 노드 multiset. diff 계산 + 트리 변화 이력 기록에 쓰인다."""

    def __init__(self, tree, source: bytes) -> None:
        self.entries: List[ShapeEntry] = []
        self.sketch_hash: str = ""     # 트리 전체 구조 해시 (스냅샷 간 동일성 판정)
        if tree is None:
            return
        root = tree.root_node
        self.sketch_hash = structure_sketch(root)[:_TREE_HASH_LEN]
        func_names = _function_names(root, source)
        for n in walk_nodes(root):
            if n.type not in _STRUCT_TYPES:
                continue
            parent_type, depth = _struct_ancestry(n)
            callee_is_self = False
            if n.type in ("call_expression", "call"):
                fn = n.child_by_field_name("function")
                callee_is_self = fn is not None and node_text(fn, source) in func_names
            self.entries.append(
                ShapeEntry(
                    node_type=n.type,
                    parent_type=parent_type,
                    depth=depth,
                    subtree_hash=structure_sketch(n)[:_HASH_LEN],
                    size_nodes=sum(1 for _ in walk_nodes(n)),
                    callee_is_self=callee_is_self,
                )
            )


def _move_key(e: ShapeEntry) -> Tuple[str, str, int, bool]:
    """move 판정 키: 동일 구조 서브트리(타입+해시+크기)가 위치만 바뀐 경우."""
    return (e.node_type, e.subtree_hash, e.size_nodes, e.callee_is_self)


def diff_snapshots(before: Optional[ShapeSnapshot], after: ShapeSnapshot, t_ms: int) -> List[DiffEvent]:
    """두 스냅샷 사이 구조 변화를 insert/delete/move 이벤트로 (결정론적 순서).

    GumTree식 move: 동일 (node_type, subtree_hash, size) 서브트리가 삭제측과
    삽입측에 동시에 있으면 위치 이동으로 본다 (addendum §3 move 예시).
    """
    b = Counter(before.entries if before is not None else [])
    a = Counter(after.entries)
    inserted = a - b
    deleted = b - a

    # ---- move 짝짓기 ----
    ins_by_key: dict = {}
    for e in sorted(inserted):
        ins_by_key.setdefault(_move_key(e), []).extend([e] * inserted[e])
    del_by_key: dict = {}
    for e in sorted(deleted):
        del_by_key.setdefault(_move_key(e), []).extend([e] * deleted[e])

    moves: List[DiffEvent] = []
    for key in sorted(ins_by_key.keys() & del_by_key.keys()):
        ins_list, del_list = ins_by_key[key], del_by_key[key]
        for e_del, e_ins in zip(del_list, ins_list):
            moves.append(
                DiffEvent(
                    t_ms=t_ms,
                    op="move",
                    node_type=e_ins.node_type,
                    parent_type=e_ins.parent_type,
                    depth=e_ins.depth,
                    subtree_hash=e_ins.subtree_hash,
                    size_nodes=e_ins.size_nodes,
                    callee_is_self=e_ins.callee_is_self,
                    from_parent=e_del.parent_type,
                    to_parent=e_ins.parent_type,
                )
            )
            inserted[e_ins] -= 1
            deleted[e_del] -= 1

    events: List[DiffEvent] = []
    for op, delta in (("insert", +inserted), ("delete", +deleted)):  # +Counter: 0 이하 제거
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
    return events + moves


def final_subtree_shape(tree, source: bytes) -> Optional[SubtreeShape]:
    """세션 종료 시점 트리의 이름-무관 요약 (addendum §3 final_subtree_shape).

    addendum 예시처럼 함수가 있으면 가장 큰 function_definition을 shape 루트로 삼는다.
    """
    if tree is None:
        return None
    root = tree.root_node
    funcs = [n for n in walk_nodes(root) if n.type == "function_definition"]
    shape_root = max(funcs, key=lambda n: (n.end_byte - n.start_byte, -n.start_byte)) if funcs else root

    func_names = _function_names(root, source)
    child_types: List[str] = []
    max_depth = 0
    has_self_call = False
    for n in walk_nodes(shape_root):
        if n is shape_root or n.type not in _STRUCT_TYPES:
            continue
        child_types.append(n.type)
        _, depth = _struct_ancestry(n)
        max_depth = max(max_depth, depth)
        if n.type in ("call_expression", "call"):
            fn = n.child_by_field_name("function")
            if fn is not None and node_text(fn, source) in func_names:
                has_self_call = True
    return SubtreeShape(
        root_type=shape_root.type,
        child_types_multiset=sorted(child_types)[:30],
        max_depth=max_depth,
        has_self_call=has_self_call,
        has_visited_array_pattern=_has_visited_array_pattern(shape_root),
    )


def build_ast_snapshot(
    seq: int, t_ms: int, shape: ShapeSnapshot, events: List[DiffEvent]
) -> AstSnapshot:
    """매처 실행 시점 1회분의 트리 상태 + 직전 대비 변화량 (app/model/ast_tree.py에 영속화).

    diff 계산 결과를 그대로 재사용하므로 추가 순회 비용이 없다(트리 전체 해시 1회 제외).
    node_type_counts는 키 정렬해서 담아 직렬화 결과까지 결정론적으로 만든다.
    """
    counts = Counter(e.node_type for e in shape.entries)
    ops = Counter(e.op for e in events)
    return AstSnapshot(
        seq=seq,
        t_ms=t_ms,
        struct_node_count=len(shape.entries),
        max_depth=max((e.depth for e in shape.entries), default=0),
        sketch_hash=shape.sketch_hash,
        node_type_counts=dict(sorted(counts.items())),
        insert_count=ops.get("insert", 0),
        delete_count=ops.get("delete", 0),
        move_count=ops.get("move", 0),
        diff_events=events,
    )


def build_tree_evolution(
    snapshots: List[AstSnapshot], tree, source: bytes
) -> AstTreeEvolution:
    """세션 종료 시점에 스냅샷 열을 세션 단위 트리 변화 요약으로 접는다."""
    if not snapshots:
        return AstTreeEvolution(final_shape=final_subtree_shape(tree, source))
    node_counts = [s.struct_node_count for s in snapshots]
    return AstTreeEvolution(
        snapshots=snapshots,
        insert_count=sum(s.insert_count for s in snapshots),
        delete_count=sum(s.delete_count for s in snapshots),
        move_count=sum(s.move_count for s in snapshots),
        diff_event_count=sum(len(s.diff_events) for s in snapshots),
        first_t_ms=snapshots[0].t_ms,
        last_t_ms=snapshots[-1].t_ms,
        initial_node_count=node_counts[0],
        final_node_count=node_counts[-1],
        peak_node_count=max(node_counts),
        final_max_depth=snapshots[-1].max_depth,
        final_sketch_hash=snapshots[-1].sketch_hash,
        final_shape=final_subtree_shape(tree, source),
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
