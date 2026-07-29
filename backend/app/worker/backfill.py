"""분류기 재처리 백필 (llm-structural-classifier-addendum.md §7).

프롬프트/모델 버전(classifier_version)이 바뀌면 과거 llm_candidate 행과 비교가
불가능해지므로 재처리 대상이 된다. UNMATCHED 세그먼트의 diff 이벤트가
unmatched_segments 테이블에 영속화돼 있어(raw blob 재생 불필요) 여기서 바로
재분류한다. pending(LLM 미가용으로 분류 못 한) 세그먼트 재시도도 겸한다.
"""

import json
import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.llm.classifier import CLASSIFIER_VERSION, classify_unmatched
from app.llm.client import LLMUnavailable, VLLMClient
from app.model.analysis import PatternWindowRow, SessionSummary, UnmatchedSegmentRow
from app.schema.analysis import DiffEvent, SubtreeShape, UnmatchedSegment
from app.worker.store import save_llm_candidates

logger = logging.getLogger(__name__)


def _row_to_segment(row: UnmatchedSegmentRow) -> UnmatchedSegment:
    events = [DiffEvent(**e) for e in json.loads(row.diff_events_json or "[]")]
    shape = SubtreeShape(**json.loads(row.final_shape_json)) if row.final_shape_json else None
    return UnmatchedSegment(
        segment_id=row.segment_id,
        t_start_ms=row.t_start_ms,
        t_end_ms=row.t_end_ms,
        diff_events=events,
        final_subtree_shape=shape,
    )


def stale_session_ids(db: Session, current_version: str = CLASSIFIER_VERSION, limit: Optional[int] = None) -> List[str]:
    """재분류가 필요한 sid 목록: pending이거나 다른 버전으로 분류된 세그먼트 보유."""
    q = (
        db.query(UnmatchedSegmentRow.sid)
        .filter(
            (UnmatchedSegmentRow.status == "pending")
            | (UnmatchedSegmentRow.classifier_version.is_(None))
            | (UnmatchedSegmentRow.classifier_version != current_version)
        )
        .distinct()
        .order_by(UnmatchedSegmentRow.sid)
    )
    if limit is not None:
        q = q.limit(limit)
    return [r.sid for r in q.all()]


async def reclassify_session(db: Session, client: VLLMClient, sid: str) -> Dict:
    """한 세션의 UNMATCHED 세그먼트를 현재 버전으로 재분류."""
    rows = (
        db.query(UnmatchedSegmentRow)
        .filter(UnmatchedSegmentRow.sid == sid)
        .order_by(UnmatchedSegmentRow.segment_id)
        .all()
    )
    if not rows:
        return {"sid": sid, "segments": 0, "accepted": 0}

    segments = [_row_to_segment(r) for r in rows]
    summary = db.query(SessionSummary).filter(SessionSummary.sid == sid).first()
    known_patterns = [
        w.pattern
        for w in db.query(PatternWindowRow)
        .filter(PatternWindowRow.sid == sid, PatternWindowRow.source == "rule")
        .all()
    ]

    candidates = await classify_unmatched(
        client, sid, segments, known_patterns,
        lang=summary.lang if summary else None,
        problem_id=str(rows[0].problem_id),
        total_duration_ms=summary.total_ms if summary else 0,
    )
    save_llm_candidates(
        db, sid=sid, user_id=rows[0].user_id, problem_id=rows[0].problem_id,
        segments=segments, results=candidates, classifier_version=CLASSIFIER_VERSION,
    )
    return {"sid": sid, "segments": len(segments), "accepted": len(candidates)}


async def backfill_classifier(
    db: Session,
    client: Optional[VLLMClient] = None,
    limit: Optional[int] = None,
) -> Dict:
    """stale 세션 전체 재분류. LLM 미가용을 만나면 중단(남은 세션은 pending/stale 유지)."""
    client = client or VLLMClient()
    sids = stale_session_ids(db, limit=limit)
    done: List[Dict] = []
    for sid in sids:
        try:
            done.append(await reclassify_session(db, client, sid))
        except LLMUnavailable as exc:
            logger.warning("백필 중단: LLM 미가용 (sid=%s): %s", sid, exc)
            break
        except Exception:
            logger.exception("백필 세션 처리 실패 (sid=%s) — 건너뜀", sid)
    return {
        "classifier_version": CLASSIFIER_VERSION,
        "sessions_targeted": len(sids),
        "sessions_processed": len(done),
        "segments_processed": sum(d["segments"] for d in done),
        "candidates_accepted": sum(d["accepted"] for d in done),
    }
