"""삭제 버스트 탐지 + 구조적 diff 분류 (dev-plan §4.1 Step 4)."""

import re
from typing import Dict, List, Optional, Set

from app.schema.analysis import DeleteBurst, EditOp
from app.worker.astsupport import node_text, structure_sketch, walk_nodes

BURST_GAP_MS = 500          # 인접 delete 간격
BURST_MIN_CHARS = 40        # pivot 후보 절대 임계값
BURST_MIN_RATIO = 0.10      # 또는 버스트 시작 시점 버퍼의 10%
REWRITE_CAP_EVENTS = 200    # 재작성 완료 판정 상한 (버스트 후 이벤트 수)

PIVOT_APPROACH_SWITCH = "APPROACH_SWITCH"
PIVOT_COMPLEXITY_FIX = "COMPLEXITY_FIX"
PIVOT_EDGE_CASE_FIX = "EDGE_CASE_FIX"
PIVOT_TYPO = "TYPO"
PIVOT_OTHER = "OTHER"

# 연구 7: TYPO로 분류돼 지표에서 빠지는 버스트 중, 같은 자리 근처를 짧은 시간
# 안에 반복해서 고쳐 쓴 것(= 좌절/확신 부족 신호)만 별도로 센다.
LOCAL_REWRITE_GAP_MS = 5000       # 같은 클러스터로 볼 인접 버스트 간 최대 시간차
LOCAL_REWRITE_OFFSET_CHARS = 20   # 같은 자리로 볼 최대 오프셋 차이
LOCAL_REWRITE_MIN_BURST = 3       # 클러스터로 인정할 최소 버스트 수


def detect_bursts(events: List[EditOp]) -> List[DeleteBurst]:
    """1차 패스: AST 없이 버스트 경계와 재작성 지평(rewrite_horizon)만 계산."""
    bursts: List[DeleteBurst] = []
    buf_len = 0
    i = 0
    K = len(events)
    while i < K:
        ev = events[i]
        if ev.op != 1 or ev.src != "user":
            buf_len += len(ev.txt) if ev.op == 0 else -min(ev.len, buf_len)
            i += 1
            continue

        # 버스트 시작
        start = i
        start_buf_len = buf_len
        deleted = 0
        j = i
        while j < K:
            e = events[j]
            if e.op == 1 and e.src == "user" and (j == start or e.t - events[j - 1].t < BURST_GAP_MS):
                deleted += min(e.len, max(buf_len, 0))
                buf_len -= min(e.len, buf_len)
                j += 1
            else:
                break
        end = j - 1

        threshold = max(BURST_MIN_CHARS, int(start_buf_len * BURST_MIN_RATIO))
        if deleted >= threshold:
            # 재작성 완료 지점: 버스트 후 삽입 누적이 삭제량에 도달하는 이벤트
            horizon = min(end + REWRITE_CAP_EVENTS, K - 1)
            inserted = 0
            for k in range(end + 1, K):
                if events[k].op == 0:
                    inserted += len(events[k].txt)
                if inserted >= deleted:
                    horizon = k
                    break
            bursts.append(
                DeleteBurst(
                    start_index=start,
                    end_index=end,
                    t_ms=events[start].t,
                    deleted_chars=deleted,
                    rewrite_horizon=horizon,
                )
            )
        i = j
    return bursts


# ---- 구조 스냅샷 (2차 패스에서 버스트 직전/재작성 완료 시점에 캡처) ----

_LOOP_TYPES = {"for_statement", "for_range_loop", "while_statement", "do_statement"}
_COND_TYPES = {"condition_clause", "comparison_operator"}
_CMP_RE = re.compile(r"\s+")


class StructSnapshot:
    def __init__(self, tree, source: bytes, lang: str) -> None:
        self.sketch = ""
        self.control: Dict[str, int] = {}       # 제어구조 종류별 개수 + 재귀 유무
        self.decl_types: Set[str] = set()       # 선언 타입 텍스트 (자료구조 교체 탐지)
        self.conditions: Set[str] = set()       # if/while 조건식 텍스트
        if tree is None:
            return
        root = tree.root_node
        self.sketch = structure_sketch(root)

        func_names: Set[str] = set()
        calls: List = []
        for n in walk_nodes(root):
            if n.type in _LOOP_TYPES:
                self.control[n.type] = self.control.get(n.type, 0) + 1
            elif n.type == "function_definition":
                name_node = n.child_by_field_name("name") or n.child_by_field_name("declarator")
                if name_node is not None:
                    for d in walk_nodes(name_node):
                        if d.type == "identifier":
                            func_names.add(node_text(d, source))
                            break
            elif n.type in ("call_expression", "call"):
                calls.append(n)
            elif n.type == "declaration":
                t = n.child_by_field_name("type")
                if t is not None:
                    self.decl_types.add(_CMP_RE.sub("", node_text(t, source)))
            elif n.type == "if_statement" or n.type in _LOOP_TYPES:
                pass
            if n.type in ("if_statement", "while_statement"):
                cond = n.child_by_field_name("condition")
                if cond is not None:
                    self.conditions.add(_CMP_RE.sub("", node_text(cond, source)))

        # 재귀 호출 존재 여부 (loop ↔ recursion 전환 탐지용)
        rec = 0
        for c in calls:
            fn = c.child_by_field_name("function")
            if fn is not None and node_text(fn, source) in func_names:
                rec += 1
        if rec:
            self.control["recursive_call"] = rec


def classify_pivot(before: StructSnapshot, after: StructSnapshot) -> str:
    """버스트 직전/재작성 완료 스냅샷 비교 → pivot 유형 (§4.1 Step 4 표)."""
    if before.sketch and before.sketch == after.sketch:
        return PIVOT_TYPO

    before_kinds = {k for k, v in before.control.items() if v > 0}
    after_kinds = {k for k, v in after.control.items() if v > 0}
    if before_kinds != after_kinds:
        return PIVOT_APPROACH_SWITCH

    if before.decl_types != after.decl_types:
        return PIVOT_COMPLEXITY_FIX

    if before.conditions != after.conditions:
        return PIVOT_EDGE_CASE_FIX

    return PIVOT_OTHER


def count_local_rewrites(typo_bursts: List[DeleteBurst], events: List[EditOp]) -> int:
    """TYPO 버스트 중 같은 자리 근방(LOCAL_REWRITE_OFFSET_CHARS 이내)을 짧은 시간
    (LOCAL_REWRITE_GAP_MS 이내) 안에 LOCAL_REWRITE_MIN_BURST회 이상 반복해서 고쳐 쓴
    버스트 수를 센다. 시간순으로 훑으며 인접 버스트가 시간·위치 조건을 모두
    만족하면 같은 클러스터에 묶는다."""
    if not typo_bursts:
        return 0

    items = sorted(
        ((b, events[b.start_index].pos) for b in typo_bursts),
        key=lambda item: item[0].t_ms,
    )

    def flush(cluster: List[tuple]) -> int:
        return len(cluster) if len(cluster) >= LOCAL_REWRITE_MIN_BURST else 0

    count = 0
    cluster: List[tuple] = [items[0]]
    for b, off in items[1:]:
        ref_off = cluster[0][1]
        if b.t_ms - cluster[-1][0].t_ms <= LOCAL_REWRITE_GAP_MS and abs(off - ref_off) <= LOCAL_REWRITE_OFFSET_CHARS:
            cluster.append((b, off))
        else:
            count += flush(cluster)
            cluster = [(b, off)]
    count += flush(cluster)
    return count
