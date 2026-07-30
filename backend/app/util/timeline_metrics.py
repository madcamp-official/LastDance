"""타임라인 파이프라인 운영 메트릭 (git-timeline-feedback-spec.md §8).

기존 M 시리즈(app/util/classifier_review.py의 classifier_metrics)를 대체한다.

- M1': 재생 검증 — 최종 커밋 스냅샷과 클라이언트가 실제 제출한 코드의 바이트 일치율.
       클라이언트 최종 코드는 파생 테이블에 없으므로, 그 세션의 마지막 채점 제출
       (judge_submissions.code)을 대조 기준으로 쓴다. 제출이 없는 세션은 분모에서 제외.
- M2': 커밋 분포 — 세션당 커밋 수(평균/중앙값), 세그먼트 라벨 비율.
- M3': 인사이트 discard율 — 프롬프트 품질 지표 (검증 실패 / 시도 전체).
- M4': 피드백 rating(up/down)과 인사이트 category별 상관.
"""

import hashlib
from collections import Counter, defaultdict
from typing import Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.model.analysis import SessionSummary
from app.model.feedback import Feedback
from app.model.insight import ProblemFeedbackInsight
from app.model.submission import JudgeSubmission
from app.model.timeline import CodeCommitRow, SessionSegmentRow
from app.util.stats import percentile


def _sha16(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def replay_fidelity(db: Session) -> Dict:
    """M1': 최종 스냅샷 == 클라이언트 최종 코드 바이트 일치율."""
    finals: Dict[str, str] = {}
    for row in (
        db.query(CodeCommitRow.sid, CodeCommitRow.seq, CodeCommitRow.snapshot_hash)
        .filter(CodeCommitRow.snapshot_hash != "")
        .order_by(CodeCommitRow.sid, CodeCommitRow.seq)
        .all()
    ):
        finals[row.sid] = row.snapshot_hash  # 같은 sid의 마지막 seq가 최종 스냅샷

    matched = compared = 0
    for sid, snap_hash in finals.items():
        sub = (
            db.query(JudgeSubmission)
            .filter(JudgeSubmission.session_id == sid)
            .order_by(JudgeSubmission.submitted_at.desc())
            .first()
        )
        if sub is None:
            continue
        compared += 1
        if _sha16(sub.code) == snap_hash:
            matched += 1
    return {
        "sessions_compared": compared,
        "sessions_matched": matched,
        "byte_match_rate": round(matched / compared, 4) if compared else 0.0,
    }


def commit_distribution(db: Session) -> Dict:
    """M2': 세션당 커밋 수 + 세그먼트 라벨 비율."""
    counts = [
        float(n)
        for _, n in db.query(CodeCommitRow.sid, func.count(CodeCommitRow.id))
        .group_by(CodeCommitRow.sid)
        .all()
    ]
    label_counts = Counter(
        label for (label,) in db.query(SessionSegmentRow.label).all()
    )
    total_segments = sum(label_counts.values())
    return {
        "n_sessions": len(counts),
        "commits_per_session_mean": round(sum(counts) / len(counts), 2) if counts else 0.0,
        "commits_per_session_p50": percentile(counts, 0.5),
        "segments_total": total_segments,
        "segment_label_ratio": {
            label: round(n / total_segments, 4) for label, n in sorted(label_counts.items())
        }
        if total_segments
        else {},
    }


def insight_discard_rate(db: Session, current_version: Optional[str] = None) -> Dict:
    """M3': 인사이트 discard율 (enum/커밋범위/시간/R1 검증 실패 비율)."""
    status_counts = dict(
        db.query(ProblemFeedbackInsight.status, func.count(ProblemFeedbackInsight.id))
        .group_by(ProblemFeedbackInsight.status)
        .all()
    )
    n_valid = status_counts.get("valid", 0)
    n_discarded = status_counts.get("discarded", 0)
    total = n_valid + n_discarded

    stale = 0
    if current_version is not None:
        stale = (
            db.query(func.count(ProblemFeedbackInsight.id))
            .filter(ProblemFeedbackInsight.analyzer_version != current_version)
            .scalar()
            or 0
        )
    return {
        "insights_total": total,
        "insights_valid": n_valid,
        "insights_discarded": n_discarded,
        "discard_rate": round(n_discarded / total, 4) if total else 0.0,
        "stale_insights": stale,   # analyzer_version 변경 시 재분류 백필 대상 (§7)
    }


def rating_by_category(db: Session) -> Dict:
    """M4': 피드백 rating과 인사이트 category별 상관.

    한 세션의 피드백은 그 세션의 모든 valid 인사이트 category에 함께 귀속된다
    (한 피드백이 여러 category를 언급하므로 category별 배타 분할이 불가).
    """
    ratings = (
        db.query(Feedback.session_id, Feedback.rating)
        .filter(Feedback.rating.isnot(None))
        .all()
    )
    if not ratings:
        return {"n_rated_feedbacks": 0, "by_category": {}}

    cats_by_sid: Dict[str, set] = defaultdict(set)
    for sid, category in (
        db.query(ProblemFeedbackInsight.sid, ProblemFeedbackInsight.category)
        .filter(ProblemFeedbackInsight.status == "valid")
        .all()
    ):
        cats_by_sid[sid].add(category)

    tally: Dict[str, Counter] = defaultdict(Counter)
    for sid, rating in ratings:
        for category in cats_by_sid.get(sid, {"(no_insight)"}):
            tally[category][rating] += 1

    return {
        "n_rated_feedbacks": len(ratings),
        "by_category": {
            category: {
                "up": c.get("up", 0),
                "down": c.get("down", 0),
                "up_rate": round(c.get("up", 0) / (c.get("up", 0) + c.get("down", 0)), 4)
                if (c.get("up", 0) + c.get("down", 0))
                else 0.0,
            }
            for category, c in sorted(tally.items())
        },
    }


def timeline_metrics(db: Session, analyzer_version: Optional[str] = None) -> Dict:
    """§8 M1'~M4' 한 번에."""
    return {
        "n_full_sessions": (
            db.query(func.count(SessionSummary.sid))
            .filter(SessionSummary.analysis_level == "full")
            .scalar()
            or 0
        ),
        "m1_replay_fidelity": replay_fidelity(db),
        "m2_commit_distribution": commit_distribution(db),
        "m3_insight_discard": insight_discard_rate(db, analyzer_version),
        "m4_rating_by_category": rating_by_category(db),
    }
