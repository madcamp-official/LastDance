import json

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.analysis import (
    PatternWindowRow,
    PauseEventRow,
    PivotEventRow,
    SessionSummary,
)
from app.model.ast_tree import AstDiffEventRow, AstSnapshotRow, AstTreeEvolutionRow
from app.model.ingest import IngestSessionState
from app.model.session import Submission
from app.model.user import User
from app.schema.analysis import (
    AnalysisResult,
    AnalyzeResponse,
    AstEvolutionResponse,
    AstSnapshot,
    AstTreeEvolution,
    DeleteBurst,
    DiffEvent,
    PatternWindowResult,
    PausePoint,
    SubtreeShape,
)
from app.util.security import get_current_user

# 조회 전용: 분석 실행은 Ingest Gateway(app/api/ingest.py) + Replay Worker
# (app/worker/consumer.py)가 session.end 수신 시 비동기로 수행한다(§3, §4).
router = APIRouter(prefix="/sessions", tags=["analysis"])


def _load_owned_session(session_id: str, user_id: str, db: Session) -> Submission:
    sub = db.query(Submission).filter(Submission.session_id == session_id).first()
    if not sub:
        raise APIError(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", "세션을 찾을 수 없습니다.")
    if sub.user_id != user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")
    return sub


@router.get("/{session_id}/analysis")
async def get_analysis(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _load_owned_session(session_id, current_user.user_id, db)

    summary = db.query(SessionSummary).filter(SessionSummary.sid == session_id).first()
    if not summary:
        # Ingest Gateway가 세션을 받아 처리 중이면 202, 아예 시작된 적 없으면 404
        state = db.query(IngestSessionState).filter(IngestSessionState.sid == session_id).first()
        if state is not None and not state.ended:
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "processing"})
        raise APIError(status.HTTP_404_NOT_FOUND, "ANALYSIS_NOT_FOUND", "분석 결과가 없습니다.")

    pauses = db.query(PauseEventRow).filter(PauseEventRow.sid == session_id).all()
    # llm_candidate/llm 행은 검수 전 후보(addendum §7) — 세션 분석 응답에는 rule 행만.
    pivots = (
        db.query(PivotEventRow)
        .filter(PivotEventRow.sid == session_id, PivotEventRow.source == "rule")
        .all()
    )
    windows = (
        db.query(PatternWindowRow)
        .filter(PatternWindowRow.sid == session_id, PatternWindowRow.source == "rule")
        .all()
    )

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


@router.get("/{session_id}/ast-evolution")
async def get_ast_evolution(
    session_id: str,
    include_diffs: bool = Query(True, description="스냅샷별 diff 이벤트 포함 여부"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """세션 종료 후 저장된 AST 트리 변화 이력 (app/model/ast_tree.py).

    스냅샷은 매처 실행(디바운스) 시점 기준이라 시간 간격이 균일하지 않다 —
    t_ms를 x축으로 써야 한다. include_diffs=false면 곡선용 요약만 내려간다.
    """
    _load_owned_session(session_id, current_user.user_id, db)

    row = (
        db.query(AstTreeEvolutionRow)
        .filter(AstTreeEvolutionRow.sid == session_id)
        .first()
    )
    if row is None:
        state = db.query(IngestSessionState).filter(IngestSessionState.sid == session_id).first()
        if state is not None and not state.ended:
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "processing"})
        # 분석은 끝났지만 파서가 없는 언어라 트리를 뜰 수 없었던 세션도 여기로 온다
        raise APIError(
            status.HTTP_404_NOT_FOUND, "AST_EVOLUTION_NOT_FOUND", "트리 변화 이력이 없습니다."
        )

    snap_rows = (
        db.query(AstSnapshotRow)
        .filter(AstSnapshotRow.sid == session_id)
        .order_by(AstSnapshotRow.seq)
        .all()
    )
    diffs_by_seq: dict = {}
    if include_diffs:
        for d in (
            db.query(AstDiffEventRow)
            .filter(AstDiffEventRow.sid == session_id)
            .order_by(AstDiffEventRow.snapshot_seq, AstDiffEventRow.id)
            .all()
        ):
            diffs_by_seq.setdefault(d.snapshot_seq, []).append(
                DiffEvent(
                    t_ms=d.t_ms, op=d.op, node_type=d.node_type,
                    parent_type=d.parent_type, depth=d.depth,
                    subtree_hash=d.subtree_hash, size_nodes=d.size_nodes,
                    callee_is_self=d.callee_is_self,
                    from_parent=d.from_parent, to_parent=d.to_parent,
                )
            )

    evolution = AstTreeEvolution(
        snapshots=[
            AstSnapshot(
                seq=s.seq, t_ms=s.t_ms,
                struct_node_count=s.struct_node_count,
                max_depth=s.max_depth,
                sketch_hash=s.sketch_hash,
                node_type_counts=json.loads(s.node_type_counts_json or "{}"),
                insert_count=s.insert_count,
                delete_count=s.delete_count,
                move_count=s.move_count,
                diff_events=diffs_by_seq.get(s.seq, []),
            )
            for s in snap_rows
        ],
        insert_count=row.insert_count,
        delete_count=row.delete_count,
        move_count=row.move_count,
        diff_event_count=row.diff_event_count,
        first_t_ms=row.first_t_ms,
        last_t_ms=row.last_t_ms,
        initial_node_count=row.initial_node_count,
        final_node_count=row.final_node_count,
        peak_node_count=row.peak_node_count,
        final_max_depth=row.final_max_depth,
        final_sketch_hash=row.final_sketch_hash,
        final_shape=(
            SubtreeShape(**json.loads(row.final_shape_json))
            if row.final_shape_json
            else None
        ),
    )
    return AstEvolutionResponse(session_id=session_id, evolution=evolution)
