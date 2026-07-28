"""비교군 기준선 조회 서비스 (app-db-ingestion-spec.md §4 연동 지점).

- 앱 problems(정수 id) → baseline problem('pXXXXX')은 problems.testcase_dir로 연결
- 셀 조회 순서: (tier, cluster_id) → (tier, -1 fallback) — spec §3.5
- n_real < 30인 metric은 data_source='estimated' (spec §4)
"""

import json
from typing import List, Optional

from sqlalchemy.orm import Session

from app.model.baseline import BaselineCell, BaselineProblem
from app.model.problem import Problem
from app.schema.baseline import BaselineMetric, BaselinePercentiles, ProblemBaselineResponse

ESTIMATED_THRESHOLD = 30  # spec §4: n_real < 30 → "추정치 기반"

# 피드백/리포트에 쓰는 핵심 metric 순서
CORE_METRICS = ("total_duration", "attempt_count", "pivot_count")


def resolve_baseline_problem(db: Session, problem: Problem) -> Optional[BaselineProblem]:
    if not problem or not problem.testcase_dir:
        return None
    return (
        db.query(BaselineProblem)
        .filter(BaselineProblem.problem_id == problem.testcase_dir)
        .first()
    )


def _load_cells(db: Session, tier: str, cluster_id: Optional[int]) -> List[BaselineCell]:
    if cluster_id is not None:
        cells = (
            db.query(BaselineCell)
            .filter(BaselineCell.tier == tier, BaselineCell.cluster_id == cluster_id)
            .all()
        )
        if cells:
            return cells
    return (
        db.query(BaselineCell)
        .filter(BaselineCell.tier == tier, BaselineCell.cluster_id == -1)
        .all()
    )


def user_band(pct: BaselinePercentiles, value: float) -> str:
    """사용자 값이 기준선 percentile 어느 구간인지 (낮을수록 빠름/적음)."""
    bounds = [("p10", pct.p10), ("p25", pct.p25), ("p50", pct.p50), ("p75", pct.p75), ("p90", pct.p90)]
    if value < bounds[0][1]:
        return "<p10"
    for (lo_name, _), (hi_name, hi) in zip(bounds, bounds[1:]):
        if value < hi:
            return f"{lo_name}~{hi_name}"
    return ">p90"


def build_baseline(
    db: Session,
    problem: Problem,
    user_values: Optional[dict] = None,
) -> ProblemBaselineResponse:
    """문제의 비교군 기준선 + (있으면) 사용자 값 위치를 계산.

    user_values: {"total_duration": 초, "attempt_count": 회, "pivot_count": 회, ...}
    """
    resp = ProblemBaselineResponse(problem_id=problem.problem_id if problem else -1)
    bp = resolve_baseline_problem(db, problem)
    if bp is None or bp.tier is None:
        return resp  # 비교군 없음 — metrics 빈 리스트

    resp.source_problem_id = bp.problem_id
    resp.tier = bp.tier
    cells = _load_cells(db, bp.tier, bp.cluster_id)
    if cells:
        resp.cluster_id = cells[0].cluster_id

    user_values = user_values or {}
    metrics: List[BaselineMetric] = []
    # 핵심 metric 먼저, pause_ms@* 는 뒤에
    ordered = sorted(
        cells,
        key=lambda c: (CORE_METRICS.index(c.metric) if c.metric in CORE_METRICS else len(CORE_METRICS), c.metric),
    )
    for cell in ordered:
        try:
            pct_raw = json.loads(cell.percentiles_json)
            pct = BaselinePercentiles(**pct_raw)
        except (ValueError, TypeError):
            continue
        uv = user_values.get(cell.metric)
        metrics.append(
            BaselineMetric(
                metric=cell.metric,
                percentiles=pct,
                n_real=cell.n_real,
                n_synthetic=cell.n_synthetic,
                data_source="observed" if cell.n_real >= ESTIMATED_THRESHOLD else "estimated",
                user_value=uv,
                user_band=user_band(pct, uv) if uv is not None else None,
            )
        )
    resp.metrics = metrics
    return resp
