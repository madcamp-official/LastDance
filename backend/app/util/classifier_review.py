"""LLM 구조 분류기 검수·메트릭 (llm-structural-classifier-addendum.md §7~§8).

- aggregate_candidates: 주 1회 사람 검수용 — llm_candidate 행을 (pattern, proposed_label)
  단위로 묶어 빈도·confidence를 집계한다. 승인된 proposed_label은 사람이
  app/worker/patterns.py 시그니처 표에 규칙으로 추가하고, 그 시점부터 규칙 매처가
  처리하므로 LLM 호출 대상에서 자연히 빠진다 (패턴 목록이 점진적으로 일반화되는 폐루프).
- classifier_metrics: M3의 "UNMATCHED 비율", M3.5의 "grounding 실패율 < 5%" 검증 기준.
"""

from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.model.analysis import PatternWindowRow, SessionSummary, UnmatchedSegmentRow


def aggregate_candidates(
    db: Session,
    min_count: int = 1,
    min_avg_confidence: float = 0.0,
) -> List[Dict]:
    """(pattern, proposed_label)별 후보 집계. 빈도 내림차순 → confidence 내림차순."""
    rows = (
        db.query(
            PatternWindowRow.pattern,
            PatternWindowRow.proposed_label,
            func.count(PatternWindowRow.id).label("n"),
            func.avg(PatternWindowRow.confidence).label("avg_conf"),
            func.count(func.distinct(PatternWindowRow.sid)).label("n_sessions"),
            func.count(func.distinct(PatternWindowRow.classifier_version)).label("n_versions"),
        )
        .filter(PatternWindowRow.source == "llm_candidate")
        .group_by(PatternWindowRow.pattern, PatternWindowRow.proposed_label)
        .all()
    )
    out = [
        {
            "pattern": r.pattern,
            "proposed_label": r.proposed_label,
            "count": r.n,
            "avg_confidence": round(float(r.avg_conf or 0.0), 3),
            "n_sessions": r.n_sessions,
            "n_classifier_versions": r.n_versions,
        }
        for r in rows
        if r.n >= min_count and float(r.avg_conf or 0.0) >= min_avg_confidence
    ]
    out.sort(key=lambda d: (-d["count"], -d["avg_confidence"], d["pattern"] or "", d["proposed_label"] or ""))
    return out


def classifier_metrics(db: Session, current_version: Optional[str] = None) -> Dict:
    """분류기 운영 메트릭.

    - unmatched_session_ratio (M3): full 분석 세션 중 UNMATCHED 세그먼트가 나온 비율
    - grounding_discard_rate (M3.5): 분류 시도된 세그먼트 중 최종 폐기 비율 (< 0.05 목표)
    - stale_segments: current_version과 다른 버전으로 분류됐거나 pending인 세그먼트 수
      (addendum §7: 프롬프트/모델 버전 변경 시 재처리 백필 대상)
    """
    n_full_sessions = (
        db.query(func.count(SessionSummary.sid))
        .filter(SessionSummary.analysis_level == "full")
        .scalar()
        or 0
    )
    n_unmatched_sessions = (
        db.query(func.count(func.distinct(UnmatchedSegmentRow.sid))).scalar() or 0
    )

    status_counts = dict(
        db.query(UnmatchedSegmentRow.status, func.count(UnmatchedSegmentRow.id))
        .group_by(UnmatchedSegmentRow.status)
        .all()
    )
    n_pending = status_counts.get("pending", 0)
    n_classified = status_counts.get("classified", 0)
    n_discarded = status_counts.get("discarded", 0)
    n_attempted = n_classified + n_discarded

    stale = 0
    if current_version is not None:
        stale = (
            db.query(func.count(UnmatchedSegmentRow.id))
            .filter(
                (UnmatchedSegmentRow.status == "pending")
                | (UnmatchedSegmentRow.classifier_version.is_(None))
                | (UnmatchedSegmentRow.classifier_version != current_version)
            )
            .scalar()
            or 0
        )

    return {
        "n_full_sessions": n_full_sessions,
        "n_sessions_with_unmatched": n_unmatched_sessions,
        "unmatched_session_ratio": round(n_unmatched_sessions / n_full_sessions, 4) if n_full_sessions else 0.0,
        "segments_total": n_pending + n_classified + n_discarded,
        "segments_pending": n_pending,
        "segments_classified": n_classified,
        "segments_discarded": n_discarded,
        "grounding_discard_rate": round(n_discarded / n_attempted, 4) if n_attempted else 0.0,
        "stale_segments": stale,
    }
