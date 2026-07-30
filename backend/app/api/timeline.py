"""타임라인·인사이트 조회 API (git-timeline-feedback-spec.md §6).

조회 전용: 타임라인 생성은 Replay Worker(app/worker/consumer.py)가 session.end
수신 시 비동기로 수행한다. 분석이 아직 안 끝났으면 202, 시작된 적 없으면 404 —
기존 GET /sessions/{sid}/analysis와 동일한 규약.
"""

import json

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.analysis import SessionSummary
from app.model.ingest import IngestSessionState
from app.model.insight import ProblemFeedbackInsight
from app.model.session import Submission
from app.model.timeline import CodeCommitRow, SessionSegmentRow
from app.model.user import User
from app.schema.insight import STAGE_KO, InsightItem, InsightsResponse
from app.schema.timeline import Commit, Hunk, Segment, TimelineResponse
from app.util.security import get_current_user

router = APIRouter(prefix="/sessions", tags=["timeline"])


def _load_owned_session(session_id: str, user_id: str, db: Session) -> Submission:
    sub = db.query(Submission).filter(Submission.session_id == session_id).first()
    if not sub:
        raise APIError(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", "세션을 찾을 수 없습니다.")
    if sub.user_id != user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")
    return sub


def _processing_or_404(db: Session, session_id: str, code: str, message: str):
    state = db.query(IngestSessionState).filter(IngestSessionState.sid == session_id).first()
    if state is not None and not state.ended:
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "processing"})
    raise APIError(status.HTTP_404_NOT_FOUND, code, message)


@router.get("/{session_id}/timeline")
async def get_timeline(
    session_id: str,
    include_hunks: bool = Query(True, description="커밋별 라인 diff 포함 여부"),
    include_snapshots: bool = Query(False, description="제출·종료 시점 전체 코드 포함 여부"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """code_commits + session_segments (프론트 타임라인 UI용)."""
    _load_owned_session(session_id, current_user.user_id, db)

    rows = (
        db.query(CodeCommitRow)
        .filter(CodeCommitRow.sid == session_id)
        .order_by(CodeCommitRow.seq.asc())
        .all()
    )
    if not rows:
        return _processing_or_404(
            db, session_id, "TIMELINE_NOT_FOUND", "타임라인 기록이 없습니다."
        )

    seg_rows = (
        db.query(SessionSegmentRow)
        .filter(SessionSegmentRow.sid == session_id)
        .order_by(SessionSegmentRow.commit_start_seq.asc())
        .all()
    )
    segments = [
        Segment(
            seg_id=s.seg_id, label=s.label,
            commit_start_seq=s.commit_start_seq, commit_end_seq=s.commit_end_seq,
            t_start_ms=s.t_start_ms, t_end_ms=s.t_end_ms,
            pause_ms=s.pause_ms, lines_touched=s.lines_touched, net_lines=s.net_lines,
        )
        for s in seg_rows
    ]
    label_of = {}
    for s in seg_rows:
        for seq in range(s.commit_start_seq, s.commit_end_seq + 1):
            label_of[seq] = s.label

    commits = [
        Commit(
            seq=r.seq, kind=r.kind, t_ms=r.t_ms,
            pause_before_ms=r.pause_before_ms, duration_ms=r.duration_ms,
            hunks=(
                [Hunk(**h) for h in json.loads(r.hunks_json or "[]")]
                if include_hunks
                else []
            ),
            verdict=r.verdict,
            lines_added=r.lines_added, lines_deleted=r.lines_deleted,
            lines_modified=r.lines_modified, net_lines=r.net_lines,
            churn_lines=r.churn_lines,
            snapshot_hash=r.snapshot_hash,
            snapshot_text=r.snapshot_text if include_snapshots else None,
            segment_label=label_of.get(r.seq, ""),
        )
        for r in rows
    ]

    summary = db.query(SessionSummary).filter(SessionSummary.sid == session_id).first()
    return TimelineResponse(
        session_id=session_id,
        timeline_version=rows[0].timeline_version,
        analysis_level=summary.analysis_level if summary else "full",
        total_ms=summary.total_ms if summary else 0,
        keystroke_count=summary.keystroke_count if summary else 0,
        verdict_seq=[r.verdict or "PENDING" for r in rows if r.kind == "submit"],
        commits=commits,
        segments=segments,
    )


@router.get("/{session_id}/insights", response_model=InsightsResponse)
async def get_insights(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인 세션의 valid 인사이트 (Stage B 산출). discarded는 노출하지 않는다."""
    _load_owned_session(session_id, current_user.user_id, db)

    rows = (
        db.query(ProblemFeedbackInsight)
        .filter(
            ProblemFeedbackInsight.sid == session_id,
            ProblemFeedbackInsight.status == "valid",
        )
        .order_by(ProblemFeedbackInsight.t_start_ms.asc())
        .all()
    )
    return InsightsResponse(
        session_id=session_id,
        insights=[
            InsightItem(
                stage=r.stage,
                stage_ko=STAGE_KO.get(r.stage, r.stage),
                category=r.category,
                logic_label=r.logic_label,
                description=r.description,
                severity=r.severity,
                commit_start_seq=r.commit_start_seq,
                commit_end_seq=r.commit_end_seq,
                t_start_ms=r.t_start_ms,
                t_end_ms=r.t_end_ms,
                duration_ms=r.duration_ms,
                evidence=json.loads(r.evidence_json or "[]"),
                advice=r.advice,
                analyzer_version=r.analyzer_version,
            )
            for r in rows
        ],
    )
