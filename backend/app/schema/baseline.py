from typing import List, Literal, Optional

from pydantic import BaseModel


class BaselinePercentiles(BaseModel):
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float


class BaselineMetric(BaseModel):
    """비교군 기준선 1개 metric. data_source는 spec §4 규칙:
    n_real < 30 → 'estimated' (리포트/프롬프트에 '추정치 기반' 명시)."""

    metric: str                                   # total_duration | attempt_count | pivot_count | pause_ms@LABEL
    percentiles: BaselinePercentiles
    n_real: int
    n_synthetic: int
    data_source: Literal["observed", "estimated"]
    user_value: Optional[float] = None            # 세션에서 계산된 사용자 값 (없으면 None)
    user_band: Optional[str] = None               # 사용자 값의 위치, 예: "<p10", "p50~p75", ">p90"


class ProblemBaselineResponse(BaseModel):
    problem_id: int                               # 앱 problems 정수 id
    source_problem_id: Optional[str] = None       # AtCoder 원본 id (예: "p02537"), 비교군 없으면 None
    tier: Optional[str] = None
    cluster_id: Optional[int] = None              # -1 = tier 단독 fallback 셀
    metrics: List[BaselineMetric] = []
