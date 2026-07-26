from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.analysis import (
    PatternWindowRow,
    PauseEventRow,
    PivotEventRow,
    SessionSummary,
)
from app.model.session import Submission
from app.model.user import User
from app.schema.analysis import (
    AnalysisResult,
    AnalyzeRequest,
    AnalyzeResponse,
    DeleteBurst,
    PatternWindowResult,
    PausePoint,
)
from app.util.security import get_current_user
from app.worker.pipeline import analyze_session
from app.worker.store import save_analysis

# Ingest Gateway(§3)가 붙기 전까지의 임시 입구:
# 프런트가 세션 종료 시 이벤트 배열을 통째로 올리면 동기적으로 분석·저장한다.
# 게이트웨이/큐 도입 시 이 라우터는 조회 전용으로 축소된다.
router = APIRouter(prefix="/sessions", tags=["analysis"])


def _load_owned_session(session_id: str, user_id: str, db: Session) -> Submission:
    sub = db.query(Submission).filter(Submission.session_id == session_id).first()
    if not sub:
        raise APIError(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", "세션을 찾을 수 없습니다.")
    if sub.user_id != user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")
    return sub


@router.post("/{session_id}/analysis", response_model=AnalyzeResponse)
async def run_analysis(
    session_id: str,
    body: AnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = _load_owned_session(session_id, current_user.user_id, db)

    result = analyze_session(
        events=body.events,
        lang=body.lang,
        submission_ts_ms=body.submission_ts_ms,
    )
    save_analysis(
        db,
        sid=session_id,
        user_id=sub.user_id,
        problem_id=sub.problem_id,
        lang=body.lang,
        result=result,
    )
    # 수집 로그(코드 전문)는 개인정보와 분리 보관 원칙 → DB에는 파생 피처만 저장.
    # 응답의 final_code는 프런트가 M1 바이트 일치 검증에 쓰도록 1회성으로만 반환.
    return AnalyzeResponse(session_id=session_id, result=result)


@router.get("/{session_id}/analysis", response_model=AnalyzeResponse)
async def get_analysis(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _load_owned_session(session_id, current_user.user_id, db)

    summary = db.query(SessionSummary).filter(SessionSummary.sid == session_id).first()
    if not summary:
        raise APIError(status.HTTP_404_NOT_FOUND, "ANALYSIS_NOT_FOUND", "분석 결과가 없습니다.")

    pauses = db.query(PauseEventRow).filter(PauseEventRow.sid == session_id).all()
    pivots = db.query(PivotEventRow).filter(PivotEventRow.sid == session_id).all()
    windows = db.query(PatternWindowRow).filter(PatternWindowRow.sid == session_id).all()

    result = AnalysisResult(
        analysis_level=summary.analysis_level,
        matcher_version=summary.matcher_version,
        total_ms=summary.total_ms,
        setup_ms=summary.setup_ms,
        formation_ms=summary.formation_ms,
        debug_ms=summary.debug_ms,
        refine_ms=summary.refine_ms,
        keystroke_count=summary.keystroke_count,
        pause_total_ms=summary.pause_total_ms,
        pause_count=summary.pause_count,
        pivot_count=summary.pivot_count,
        code_bytes=summary.code_bytes,
        final_code="",  # 코드 전문은 DB에 저장하지 않음
        pauses=[
            PausePoint(
                event_index=-1,  # 원본 이벤트는 보관하지 않으므로 인덱스는 무의미
                t_ms=p.t_ms, duration_ms=p.duration_ms,
                ast_label=p.ast_label, pattern=p.pattern, phase=p.phase,
            )
            for p in pauses
        ],
        pivots=[
            DeleteBurst(
                start_index=-1, end_index=-1, rewrite_horizon=-1,
                t_ms=b.t_ms, deleted_chars=b.deleted_chars,
                pivot_type=b.pivot_type, pattern=b.pattern,
            )
            for b in pivots
        ],
        pattern_windows=[
            PatternWindowResult(
                pattern=w.pattern,
                t_start_ms=w.t_start_ms, t_complete_ms=w.t_complete_ms,
                formation_ms=w.formation_ms,
                pause_ms_in_window=w.pause_ms_in_window,
                pivot_count_in_window=w.pivot_count_in_window,
            )
            for w in windows
        ],
        patterns_detected=sorted({w.pattern for w in windows}),
    )
    return AnalyzeResponse(session_id=session_id, result=result)
