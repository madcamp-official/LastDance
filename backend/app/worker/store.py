"""분석 결과 저장 (dev-plan §8 멱등성: sid 기준 upsert)."""

from datetime import UTC, datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.model.analysis import (
    PatternWindowRow,
    PauseEventRow,
    PivotEventRow,
    SessionSummary,
)
from app.schema.analysis import AnalysisResult, UnmatchedSegment


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


def save_llm_candidates(
    db: Session,
    sid: str,
    user_id: str,
    problem_id: int,
    segments: List[UnmatchedSegment],
    results: list,          # List[app.llm.classifier.SegmentResult] (worker→llm 임포트 회피)
    classifier_version: str,
) -> None:
    """구조 분류기(addendum §7)의 후보 결과 저장.

    pattern_windows.source="llm_candidate", pivot_events.source="llm" —
    기준선 통계·피드백 프롬프트는 source="rule"만 읽으므로 재현성 보장(§5)이 유지된다.
    같은 sid 재처리 시 기존 후보 행을 지우고 다시 쓴다(멱등).
    """
    db.query(PatternWindowRow).filter(
        PatternWindowRow.sid == sid, PatternWindowRow.source == "llm_candidate"
    ).delete()
    db.query(PivotEventRow).filter(
        PivotEventRow.sid == sid, PivotEventRow.source == "llm"
    ).delete()

    seg_by_id = {s.segment_id: s for s in segments}
    for r in results:
        seg = seg_by_id.get(r.segment_id)
        if seg is None:
            continue
        db.add(
            PatternWindowRow(
                sid=sid, user_id=user_id, problem_id=problem_id,
                pattern=r.pattern,
                t_start_ms=seg.t_start_ms, t_complete_ms=seg.t_end_ms,
                formation_ms=max(seg.t_end_ms - seg.t_start_ms, 0),
                source="llm_candidate",
                classifier_version=classifier_version,
                confidence=r.pattern_confidence,
                proposed_label=r.proposed_label,
            )
        )
        if r.pivot_type:
            db.add(
                PivotEventRow(
                    sid=sid, user_id=user_id,
                    t_ms=seg.t_start_ms, deleted_chars=0,
                    pivot_type=r.pivot_type, pattern=r.pattern if r.pattern != "OTHER" else "",
                    source="llm",
                    classifier_version=classifier_version,
                    confidence=r.pivot_confidence,
                )
            )
    db.commit()
