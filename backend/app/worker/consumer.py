"""Kafka consumer = Replay Worker 트리거 (dev-plan §1, §4).

Ingest Gateway(app/api/ingest.py)는 검증·중복제거·append만 하고 분석은 하지 않는다.
이 컨슈머가 Kafka 이벤트 로그를 구독하다가 session.end를 받으면 그 세션 전체 이벤트를
모아 재생 엔진(app/worker/pipeline.py)을 돌리고 결과를 저장한다 — 실시간 경로와
분리된 비동기 배치 처리(§6.1의 스케일 분리 근거)를 인프라 추가 없이 같은 프로세스
안에서 재현한 것. 세션 이벤트는 컨슈머가 살아있는 동안만 메모리에 버퍼링하므로,
컨슈머가 세션 도중 재시작되면 그 세션의 미종료분은 유실된다(현재 스케일에서는 허용).
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional

from aiokafka import AIOKafkaConsumer

from app.database import SessionLocal
from app.model.ingest import IngestSessionState
from app.model.session import Submission
from app.schema.analysis import EditOp
from app.util.messaging import KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC
from app.worker.pipeline import analyze_session
from app.worker.rawstore import write_raw_blob
from app.worker.store import save_analysis

logger = logging.getLogger(__name__)

CONSUMER_GROUP_ID = "replay-worker-v1"


class _SessionBuffer:
    __slots__ = ("problem_id", "user_id", "lang", "events", "submission_ts")

    def __init__(self, problem_id: int, user_id: str, lang: Optional[str]):
        self.problem_id = problem_id
        self.user_id = user_id
        self.lang = lang
        self.events: List[EditOp] = []
        self.submission_ts: List[int] = []


_buffers: Dict[str, _SessionBuffer] = {}
_task: Optional[asyncio.Task] = None


def _get_buffer(db, sid: str) -> Optional[_SessionBuffer]:
    buf = _buffers.get(sid)
    if buf is not None:
        return buf
    state = db.query(IngestSessionState).filter(IngestSessionState.sid == sid).first()
    if state is None:
        return None  # 게이트웨이가 아직 상태를 만들기 전에 컨슈머가 메시지를 먼저 본 경우
    buf = _SessionBuffer(problem_id=state.problem_id, user_id=state.user_id, lang=state.lang)
    _buffers[sid] = buf
    return buf


def _finalize(db, sid: str) -> None:
    buf = _buffers.pop(sid, None)
    state = db.query(IngestSessionState).filter(IngestSessionState.sid == sid).first()
    if buf is None or state is None or state.ended:
        return  # 멱등: 이미 처리됐거나 버퍼가 없으면(§8) 재처리하지 않음

    # 최종 언어는 PATCH /sessions/{id}(세션 종료, 언어 변경 가능)가 권위 소스.
    # 아직 반영 전이면(REST 호출과 WS session.end 순서 미보장) session.start의 lang으로 대체.
    sub = db.query(Submission).filter(Submission.session_id == sid).first()
    lang = (sub.language if sub is not None else None) or buf.lang or "unknown"
    events = sorted(buf.events, key=lambda e: e.t)
    write_raw_blob(buf.problem_id, sid, events)

    result = analyze_session(events=events, lang=lang, submission_ts_ms=buf.submission_ts)
    if state.seq_gap_detected:
        result.analysis_level = "degraded"  # §8: seq 누락 세션은 기준선 삽입 제외 대상

    save_analysis(
        db, sid=sid, user_id=buf.user_id, problem_id=buf.problem_id, lang=lang, result=result
    )
    state.ended = True
    db.commit()


def _handle_message(sid: str, msg_type: str, raw: dict) -> None:
    db = SessionLocal()
    try:
        if msg_type == "session.start":
            buf = _get_buffer(db, sid)
            if buf is not None:
                buf.lang = raw.get("lang")
                state = db.query(IngestSessionState).filter(IngestSessionState.sid == sid).first()
                if state is not None:
                    state.lang = buf.lang
                    db.commit()

        elif msg_type == "edit.batch":
            buf = _get_buffer(db, sid)
            if buf is not None:
                buf.events.extend(EditOp(**op) for op in raw.get("ops", []))

        elif msg_type == "submission.mark":
            buf = _get_buffer(db, sid)
            if buf is not None:
                buf.submission_ts.append(raw.get("t", 0))

        elif msg_type == "session.end":
            _finalize(db, sid)
    finally:
        db.close()


async def _consume_loop() -> None:
    consumer = AIOKafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await consumer.start()
    try:
        async for msg in consumer:
            try:
                raw = json.loads(msg.value.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            sid = raw.get("sid")
            msg_type = raw.get("type")
            if not sid or not msg_type:
                continue
            try:
                _handle_message(sid, msg_type, raw)
            except Exception:
                logger.exception("replay worker 처리 실패: sid=%s type=%s", sid, msg_type)
    finally:
        await consumer.stop()


def start_consumer() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_consume_loop())


async def stop_consumer() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
