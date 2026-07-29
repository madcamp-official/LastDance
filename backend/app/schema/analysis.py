from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel


# ---- 입력 이벤트 (dev-plan §2.1 EditOp) ----
class EditOp(BaseModel):
    t: int                      # 세션 시작 기준 상대 ms
    op: Literal[0, 1]           # 0 = insert, 1 = delete
    pos: int                    # 코드포인트 기준 절대 오프셋
    len: int = 0                # delete 길이
    txt: str = ""               # insert 문자열
    src: Literal["user", "autoindent", "autocomplete"] = "user"


# ---- pause (§4.1 Step 2~3) ----
class PausePoint(BaseModel):
    event_index: int            # pause 직후 이벤트 인덱스 (간격 = ev[i-1] ~ ev[i])
    t_ms: int                   # pause 시작 시각 (ev[i-1].t)
    duration_ms: int
    ast_label: str = ""         # INTERFACE_DESIGN 등, 재생 패스에서 채움
    pattern: str = ""           # 귀속된 구조 패턴 (formation 역산 후 채움)
    phase: str = ""


# ---- 삭제 버스트 / pivot (§4.1 Step 4) ----
class DeleteBurst(BaseModel):
    start_index: int            # 버스트 첫 delete 이벤트 인덱스
    end_index: int              # 버스트 마지막 delete 이벤트 인덱스
    t_ms: int
    deleted_chars: int
    rewrite_horizon: int        # "재작성 완료"로 간주하는 이벤트 인덱스 (before/after 비교 지점)
    pivot_type: str = ""        # APPROACH_SWITCH | COMPLEXITY_FIX | EDGE_CASE_FIX | TYPO
    pattern: str = ""


# ---- 구조 패턴 매칭 (§4.1 Step 5~6) ----
class PatternMatch(BaseModel):
    pattern: str                            # BFS, DP 등
    ranges: List[Tuple[int, int]]           # 패턴 구성 노드들의 (start_byte, end_byte)


class PatternWindowResult(BaseModel):
    pattern: str
    t_start_ms: int
    t_complete_ms: int
    formation_ms: int
    pause_ms_in_window: int = 0
    pivot_count_in_window: int = 0


# ---- UNMATCHED 구간의 구조 diff (llm-structural-classifier-addendum §3) ----
# LLM 입력용이지만 생성은 전 구간 결정론적. 원본 코드/식별자 텍스트는 넣지 않는다.
class DiffEvent(BaseModel):
    t_ms: int
    # GumTree/ChangeDistiller류 표준 편집 연산 (addendum §3). update는 노드 라벨
    # (식별자 텍스트) 변경인데 이 파이프라인은 이름-무관이라 라벨을 아예 안 보므로 없음.
    op: Literal["insert", "delete", "move"]
    node_type: str
    parent_type: str = ""           # 가장 가까운 '구조' 조상 타입 (compound 등 중간 노드 제외)
    depth: int = 0                  # 구조 조상 기준 깊이 (addendum §3 예시와 동일 눈금)
    subtree_hash: str = ""
    size_nodes: int = 0
    callee_is_self: bool = False    # 재귀 판정용: 이름 대신 불리언만 전달
    from_parent: str = ""           # move 전용
    to_parent: str = ""             # move 전용


class SubtreeShape(BaseModel):
    root_type: str = ""
    child_types_multiset: List[str] = []
    max_depth: int = 0
    has_self_call: bool = False
    has_visited_array_pattern: bool = False  # Step 5 규칙 불리언 피처 재사용 (addendum §3)


class UnmatchedSegment(BaseModel):
    segment_id: str
    t_start_ms: int
    t_end_ms: int
    diff_events: List[DiffEvent] = []
    final_subtree_shape: Optional[SubtreeShape] = None


# ---- 세션 전체 AST 트리 변화 이력 (app/model/ast_tree.py에 영속화) ----
# UNMATCHED 여부와 무관하게 매처 실행(디바운스) 시점마다 전부 기록한다.
class AstSnapshot(BaseModel):
    seq: int                        # 세션 내 매처 실행 순서 (0부터)
    t_ms: int
    struct_node_count: int = 0
    max_depth: int = 0
    sketch_hash: str = ""           # 트리 전체 구조 해시 (되돌아간 구간 탐지용)
    node_type_counts: Dict[str, int] = {}
    insert_count: int = 0           # 직전 스냅샷 대비
    delete_count: int = 0
    move_count: int = 0
    diff_events: List[DiffEvent] = []


class AstTreeEvolution(BaseModel):
    snapshots: List[AstSnapshot] = []
    insert_count: int = 0
    delete_count: int = 0
    move_count: int = 0
    diff_event_count: int = 0
    first_t_ms: int = 0
    last_t_ms: int = 0
    initial_node_count: int = 0
    final_node_count: int = 0
    peak_node_count: int = 0
    final_max_depth: int = 0
    final_sketch_hash: str = ""
    final_shape: Optional[SubtreeShape] = None


# ---- 워커 최종 산출 (§4.2) ----
class AnalysisResult(BaseModel):
    analysis_level: str                     # "full" | "timing_only" | "degraded"
    matcher_version: int
    total_ms: int = 0
    setup_ms: int = 0
    formation_ms: int = 0
    debug_ms: int = 0
    refine_ms: int = 0
    keystroke_count: int = 0
    pause_total_ms: int = 0
    pause_count: int = 0
    pivot_count: int = 0
    local_rewrite_count: int = 0
    code_bytes: int = 0
    final_code: str = ""                    # M1 바이트 일치 검증용
    pauses: List[PausePoint] = []
    pivots: List[DeleteBurst] = []
    pattern_windows: List[PatternWindowResult] = []
    patterns_detected: List[str] = []
    unmatched_segments: List[UnmatchedSegment] = []  # 규칙 매처가 못 덮은 구간 (addendum §2)
    ast_evolution: Optional[AstTreeEvolution] = None  # 세션 전체 트리 변화 (full 분석에서만)


# ---- GET /sessions/{session_id}/analysis (분석 결과 조회) ----
class AnalyzeResponse(BaseModel):
    session_id: str
    result: AnalysisResult


# ---- GET /sessions/{session_id}/ast-evolution (트리 변화 이력 조회) ----
class AstEvolutionResponse(BaseModel):
    session_id: str
    evolution: AstTreeEvolution
