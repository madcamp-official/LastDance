"""Stage C 비교군 집계 (git-timeline-feedback-spec.md §5.1).

같은 problem_id의 problem_feedback_insights(status="valid")를 (stage, category)로
그룹해 소요 시간 분포와 발생률을 낸다. 기존 AST 기준선(app/util/baseline.py의
build_baseline: 합성 데이터 기반 tier×cluster 셀)을 대체하며, 표본이 쌓이기 전에는
노출 자체를 막아 콜드스타트에 합성 데이터가 필요 없다.

노출 규칙 (§5.1):
- n < 5 → 그 stage 비교는 노출하지 않는다 (신뢰 불가 + 자기효능감 보호, 연구 5 계승)
- n < 30 → "[표본 적음]" 표시
"""

from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.model.analysis import SessionSummary
from app.model.insight import ProblemFeedbackInsight
from app.model.timeline import CodeCommitRow
from app.schema.baseline import BaselineMetric, BaselinePercentiles, ProblemBaselineResponse
from app.schema.insight import STAGE_KO, CohortStage, CohortSummary
from app.util.stats import percentile, percentiles

MIN_COHORT_N = 5        # 이 미만이면 비교 노출 금지
SMALL_SAMPLE_N = 30     # 이 미만이면 "[표본 적음]"


def _valid_insights(db: Session, problem_id: int, exclude_sid: Optional[str]):
    q = db.query(ProblemFeedbackInsight).filter(
        ProblemFeedbackInsight.problem_id == problem_id,
        ProblemFeedbackInsight.status == "valid",
    )
    if exclude_sid:
        q = q.filter(ProblemFeedbackInsight.sid != exclude_sid)
    return q.all()


def _session_count(db: Session, problem_id: int, exclude_sid: Optional[str]) -> int:
    """그 문제를 푼(분석이 끝난) 세션 수 — 발생률의 분모."""
    q = db.query(func.count(SessionSummary.sid)).filter(SessionSummary.problem_id == problem_id)
    if exclude_sid:
        q = q.filter(SessionSummary.sid != exclude_sid)
    return int(q.scalar() or 0)


def _attempt_counts(db: Session, problem_id: int, exclude_sid: Optional[str]) -> List[float]:
    """세션별 제출 횟수 (code_commits의 kind="submit" 행 수)."""
    q = (
        db.query(CodeCommitRow.sid, func.count(CodeCommitRow.id))
        .filter(CodeCommitRow.problem_id == problem_id, CodeCommitRow.kind == "submit")
        .group_by(CodeCommitRow.sid)
    )
    if exclude_sid:
        q = q.filter(CodeCommitRow.sid != exclude_sid)
    return [float(n) for _, n in q.all()]


def _total_ms_values(db: Session, problem_id: int, exclude_sid: Optional[str]) -> List[float]:
    q = db.query(SessionSummary.total_ms).filter(SessionSummary.problem_id == problem_id)
    if exclude_sid:
        q = q.filter(SessionSummary.sid != exclude_sid)
    return [float(v) for (v,) in q.all() if v is not None]


def build_cohort(
    db: Session,
    problem_id: int,
    exclude_sid: Optional[str] = None,
    user_durations: Optional[Dict[Tuple[str, str], int]] = None,
    user_total_ms: Optional[int] = None,
    user_attempt_count: Optional[int] = None,
) -> CohortSummary:
    """(stage, category)별 소요 시간 분포 + 발생률.

    user_durations: 본인 세션의 {(stage, category): duration_ms} — 같은 키가 여러 개면
    호출부가 미리 합산해서 넘긴다.
    """
    summary = CohortSummary(
        problem_id=problem_id,
        user_total_ms=user_total_ms,
        user_attempt_count=user_attempt_count,
    )
    rows = _valid_insights(db, problem_id, exclude_sid)
    total_sessions = _session_count(db, problem_id, exclude_sid)
    summary.session_count = total_sessions

    durations: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    labels: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    sids_by_stage: Dict[str, set] = defaultdict(set)
    for r in rows:
        key = (r.stage, r.category)
        durations[key].append(float(r.duration_ms))
        labels[key][r.logic_label] += 1
        sids_by_stage[r.stage].add(r.sid)

    user_durations = user_durations or {}
    stages: List[CohortStage] = []
    for key in sorted(durations):
        values = durations[key]
        n = len(values)
        if n < MIN_COHORT_N:
            continue  # §5.1: 표본 부족 — 노출 자체를 막는다
        stage, category = key
        pct = percentiles(values, (0.25, 0.5, 0.75))
        stages.append(
            CohortStage(
                stage=stage,
                category=category,
                top_logic_label=labels[key].most_common(1)[0][0],
                n=n,
                p25=pct["p25"], p50=pct["p50"], p75=pct["p75"],
                occurrence_rate=(
                    len(sids_by_stage[stage]) / total_sessions if total_sessions else 0.0
                ),
                user_duration_ms=user_durations.get(key),
                small_sample=n < SMALL_SAMPLE_N,
            )
        )
    summary.stages = stages

    totals = _total_ms_values(db, problem_id, exclude_sid)
    if len(totals) >= MIN_COHORT_N:
        summary.total_ms_p50 = percentile(totals, 0.5)
    attempts = _attempt_counts(db, problem_id, exclude_sid)
    if len(attempts) >= MIN_COHORT_N:
        summary.attempt_count_p50 = percentile(attempts, 0.5)
    return summary


# --------------------------------------------------------- GET /problems/{id}/stats
def build_cohort_baseline(
    db: Session, problem_id: int, exclude_sid: Optional[str] = None
) -> ProblemBaselineResponse:
    """비교 통계 API용 어댑터 (§6: baseline 엔드포인트 내부를 cohort 집계로 교체).

    응답 스키마(ProblemBaselineResponse)는 프론트 호환을 위해 그대로 두고,
    metric 이름만 타임라인 파이프라인 기준으로 바꾼다:
      total_duration(초) / attempt_count(회) / stage_ms@{STAGE}(초)
    n < 30이면 기존 규약대로 data_source="estimated".
    """
    resp = ProblemBaselineResponse(problem_id=problem_id)
    metrics: List[BaselineMetric] = []

    def add(metric: str, values: List[float], n: int) -> None:
        if n < MIN_COHORT_N:
            return
        pct = percentiles(values)
        metrics.append(
            BaselineMetric(
                metric=metric,
                percentiles=BaselinePercentiles(**pct),
                n_real=n,
                n_synthetic=0,      # 타임라인 파이프라인은 합성 표본을 쓰지 않는다 (§7 롤아웃)
                data_source="observed" if n >= SMALL_SAMPLE_N else "estimated",
            )
        )

    totals = _total_ms_values(db, problem_id, exclude_sid)
    add("total_duration", [v / 1000.0 for v in totals], len(totals))
    attempts = _attempt_counts(db, problem_id, exclude_sid)
    add("attempt_count", attempts, len(attempts))

    by_stage: Dict[str, List[float]] = defaultdict(list)
    for r in _valid_insights(db, problem_id, exclude_sid):
        by_stage[r.stage].append(r.duration_ms / 1000.0)
    for stage in sorted(by_stage):
        add(f"stage_ms@{stage}", by_stage[stage], len(by_stage[stage]))

    resp.metrics = metrics
    return resp


def stage_ko(stage: str) -> str:
    return STAGE_KO.get(stage, stage)
