from typing import List, Literal, Tuple

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


# ---- GET /sessions/{session_id}/analysis (분석 결과 조회) ----
class AnalyzeResponse(BaseModel):
    session_id: str
    result: AnalysisResult
