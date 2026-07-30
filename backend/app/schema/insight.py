"""Stage B(LLM 세션 분석기) 구조화 출력 + 비교군 집계 스키마
(git-timeline-feedback-spec.md §4, §5.1).
"""

from typing import List, Optional, Tuple

from pydantic import BaseModel

# §4.2 [stage 정준 값 — 이 목록 밖의 값 금지]
STAGE_ENUM = (
    "PROBLEM_UNDERSTANDING",
    "APPROACH_DESIGN",
    "CORE_LOGIC_DESIGN",
    "SCAFFOLD_IMPLEMENTATION",
    "CORE_IMPLEMENTATION",
    "EDGE_CASE_HANDLING",
    "DEBUG_LOGIC",
    "DEBUG_TRIVIAL",
    "OPTIMIZATION",
)

STAGE_KO = {
    "PROBLEM_UNDERSTANDING": "문제 이해·관찰",
    "APPROACH_DESIGN": "접근 설계",
    "CORE_LOGIC_DESIGN": "핵심 논리 설계",
    "SCAFFOLD_IMPLEMENTATION": "뼈대 구현",
    "CORE_IMPLEMENTATION": "핵심 논리 코드화",
    "EDGE_CASE_HANDLING": "경계·예외 처리",
    "DEBUG_LOGIC": "논리 오류 디버깅",
    "DEBUG_TRIVIAL": "사소한 디버깅",
    "OPTIMIZATION": "시간/공간 최적화",
}

CATEGORY_ENUM = ("stall", "churn", "debug_loop", "smooth")
SEVERITY_ENUM = ("high", "medium", "low")


class SessionInsight(BaseModel):
    """LLM이 낸 인사이트 1건. 검증 통과분만 status='valid'로 저장된다."""

    stage: str
    category: str
    logic_label: str
    description: str
    severity: str = "medium"
    commit_range: Tuple[int, int]
    t_range_ms: Tuple[int, int]
    evidence: List[str] = []
    advice: Optional[str] = None


class AnalyzerOutput(BaseModel):
    insights: List[SessionInsight] = []
    overall: str = ""


class ValidatedInsight(BaseModel):
    """검증 결과가 붙은 인사이트 (discarded도 M3' 집계를 위해 저장)."""

    insight: SessionInsight
    status: str                     # "valid" | "discarded"
    reason: str = ""                # discarded 사유 (로그·메트릭용, DB 컬럼은 아님)


# ---- GET /sessions/{session_id}/insights ----
class InsightItem(BaseModel):
    stage: str
    stage_ko: str = ""
    category: str
    logic_label: str
    description: str
    severity: str
    commit_start_seq: int
    commit_end_seq: int
    t_start_ms: int
    t_end_ms: int
    duration_ms: int
    evidence: List[str] = []
    advice: Optional[str] = None
    analyzer_version: str = ""


class InsightsResponse(BaseModel):
    session_id: str
    insights: List[InsightItem] = []


# ---- §5.1 비교군 집계 ----
class CohortStage(BaseModel):
    stage: str
    category: str
    top_logic_label: str = ""
    n: int = 0                      # 표본 수 (본인 제외)
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    occurrence_rate: float = 0.0    # 그 문제를 푼 세션 중 이 stage 인사이트가 있는 비율
    user_duration_ms: Optional[int] = None   # 이번 세션의 같은 stage 소요
    small_sample: bool = False      # n < 30 → "[표본 적음]"


class CohortSummary(BaseModel):
    problem_id: int
    session_count: int = 0          # 비교 대상 세션 수 (본인 제외)
    stages: List[CohortStage] = []
    total_ms_p50: Optional[float] = None
    attempt_count_p50: Optional[float] = None
    user_total_ms: Optional[int] = None
    user_attempt_count: Optional[int] = None

    @property
    def exposable(self) -> bool:
        """노출 가능한 stage 비교가 하나라도 있는가 (§5.1: n<5는 노출 금지)."""
        return bool(self.stages)
