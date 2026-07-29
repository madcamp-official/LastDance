"""분석 결과 저장 (dev-plan §8 멱등성: sid 기준 upsert)."""

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.model.analysis import (
    PatternWindowRow,
    PauseEventRow,
    PivotEventRow,
    SessionSummary,
)
from app.schema.analysis import AnalysisResult


def save_analysis(
    db: Session,
    sid: str,
    user_id: str,
    problem_id: int,
    lang: Optional[str],
    result: AnalysisResult,
    tier: Optional[str] = None,
) -> None:
    # 같은 sid 재처리 시 동일 결과 보장: 기존 파생 행 전부 삭제 후 재삽입
    db.query(SessionSummary).filter(SessionSummary.sid == sid).delete()
    db.query(PauseEventRow).filter(PauseEventRow.sid == sid).delete()
    db.query(PivotEventRow).filter(PivotEventRow.sid == sid).delete()
    db.query(PatternWindowRow).filter(PatternWindowRow.sid == sid).delete()

    db.add(
        SessionSummary(
            sid=sid,
            user_id=user_id,
            problem_id=problem_id,
            tier=tier,
            lang=lang,
            analysis_level=result.analysis_level,
            matcher_version=result.matcher_version,
            total_ms=result.total_ms,
            setup_ms=result.setup_ms,
            formation_ms=result.formation_ms,
            debug_ms=result.debug_ms,
            refine_ms=result.refine_ms,
            keystroke_count=result.keystroke_count,
            pause_total_ms=result.pause_total_ms,
            pause_count=result.pause_count,
            pivot_count=result.pivot_count,
            local_rewrite_count=result.local_rewrite_count,
            code_bytes=result.code_bytes,
            created_at=datetime.now(tz=UTC),
        )
    )
    for p in result.pauses:
        db.add(
            PauseEventRow(
                sid=sid, user_id=user_id,
                t_ms=p.t_ms, duration_ms=p.duration_ms,
                ast_label=p.ast_label, pattern=p.pattern, phase=p.phase,
            )
        )
    for b in result.pivots:
        db.add(
            PivotEventRow(
                sid=sid, user_id=user_id,
                t_ms=b.t_ms, deleted_chars=b.deleted_chars,
                pivot_type=b.pivot_type, pattern=b.pattern,
            )
        )
    for w in result.pattern_windows:
        db.add(
            PatternWindowRow(
                sid=sid, user_id=user_id, problem_id=problem_id,
                pattern=w.pattern,
                t_start_ms=w.t_start_ms, t_complete_ms=w.t_complete_ms,
                formation_ms=w.formation_ms,
                pause_ms_in_window=w.pause_ms_in_window,
                pivot_count_in_window=w.pivot_count_in_window,
            )
        )
    db.commit()
