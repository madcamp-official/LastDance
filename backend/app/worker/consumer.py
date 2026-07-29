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
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional, Tuple

from aiokafka import AIOKafkaConsumer

from app.database import SessionLocal
from app.llm.classifier import CLASSIFIER_VERSION, classify_unmatched
from app.llm.client import LLMUnavailable, VLLMClient
from app.model.ingest import IngestSessionState
from app.model.session import Submission
from app.schema.analysis import AnalysisResult, EditOp
from app.util.messaging import KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC
from app.worker.pipeline import analyze_session
from app.worker.rawstore import write_raw_blob
from app.worker.store import save_analysis, save_llm_candidates

logger = logging.getLogger(__name__)

CONSUMER_GROUP_ID = "replay-worker-v1"

# session.end 하나당 analyze_session(tree-sitter 파싱 + 패턴매칭)이 무거워서, Kafka
# 하트비트/오토커밋과 같은 이벤트루프에서 동기로 돌리면 그 루프를 막아 세션 타임아웃으로
# 그룹에서 튕겨나간다(UnknownMemberIdError). 큐로 "언제 처리할지"를, 프로세스풀로
# "누가 CPU를 쓰는지"를 분리해서 컨슈머 루프가 analyze_session 실행 시간과 완전히
# 무관하게 돈다.
_FINALIZE_WORKERS = 3
_finalize_queue: "asyncio.Queue[str]" = asyncio.Queue()
_pool: Optional[ProcessPoolExecutor] = None
_finalize_tasks: List[asyncio.Task] = []


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


def _prepare_finalize(db, sid: str) -> Optional[Tuple[_SessionBuffer, List[EditOp], str]]:
    """가벼운 부분만: 버퍼 pop, 멱등 체크, raw blob 저장. analyze_session은 여기서 안 부른다."""
    buf = _buffers.pop(sid, None)
    state = db.query(IngestSessionState).filter(IngestSessionState.sid == sid).first()
    if buf is None or state is None or state.ended:
        return None  # 멱등: 이미 처리됐거나 버퍼가 없으면(§8) 재처리하지 않음

    # 최종 언어는 PATCH /sessions/{id}(세션 종료, 언어 변경 가능)가 권위 소스.
    # 아직 반영 전이면(REST 호출과 WS session.end 순서 미보장) session.start의 lang으로 대체.
    sub = db.query(Submission).filter(Submission.session_id == sid).first()
    lang = (sub.language if sub is not None else None) or buf.lang or "unknown"
    events = sorted(buf.events, key=lambda e: e.t)
    write_raw_blob(buf.problem_id, sid, events)
    return buf, events, lang


def _commit_finalize(db, sid: str, buf: _SessionBuffer, lang: str, result: AnalysisResult) -> None:
    state = db.query(IngestSessionState).filter(IngestSessionState.sid == sid).first()
    if state is None:
        return
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
            # 무거운 analyze_session은 여기서 절대 안 부른다 — sid만 큐에 넣고
            # 바로 다음 Kafka 메시지로 넘어가서 하트비트/커밋 코루틴을 안 막는다.
            _finalize_queue.put_nowait(sid)
    finally:
        db.close()


async def _finalize_worker() -> None:
    """큐에서 sid를 꺼내 무거운 analyze_session만 별도 프로세스에 위임해 실행.

    DB 조회/커밋은 가벼워서 이 코루틴에서 직접 하고, CPU 무거운 analyze_session만
    ProcessPoolExecutor로 보내 메인 이벤트루프(Kafka 하트비트 포함)를 절대 막지 않는다.
    """
    while True:
        sid = await _finalize_queue.get()
        try:
            db = SessionLocal()
            try:
                prepared = _prepare_finalize(db, sid)
            finally:
                db.close()
            if prepared is None:
                continue
            buf, events, lang = prepared

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(_pool, analyze_session, events, lang, buf.submission_ts)

            db = SessionLocal()
            try:
                _commit_finalize(db, sid, buf, lang, result)
            finally:
                db.close()

            # (addendum §2) UNMATCHED 세그먼트가 있으면 세션당 1회만 LLM 구조 분류기 호출.
            # 결정론적 분석 결과는 위에서 이미 커밋됐으므로, 여기 실패는 세그먼트를
            # UNMATCHED로 남길 뿐 파이프라인을 실패시키지 않는다.
            if result.analysis_level == "full" and result.unmatched_segments:
                try:
                    candidates = await classify_unmatched(
                        VLLMClient(), sid, result.unmatched_segments,
                        result.patterns_detected, lang,
                        problem_id=str(buf.problem_id),
                        total_duration_ms=result.total_ms,
                    )
                    if candidates:
                        db = SessionLocal()
                        try:
                            save_llm_candidates(
                                db, sid=sid, user_id=buf.user_id, problem_id=buf.problem_id,
                                segments=result.unmatched_segments, results=candidates,
                                classifier_version=CLASSIFIER_VERSION,
                            )
                        finally:
                            db.close()
                except LLMUnavailable as exc:
                    logger.warning("구조 분류기 LLM 연결 실패 (sid=%s): %s — UNMATCHED 유지", sid, exc)
                except Exception:
                    logger.exception("구조 분류기 처리 실패 (sid=%s) — UNMATCHED 유지", sid)
        except Exception:
            logger.exception("finalize worker 처리 실패: sid=%s", sid)
        finally:
            _finalize_queue.task_done()


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
    global _task, _pool, _finalize_tasks
    if _task is None:
        _pool = ProcessPoolExecutor(max_workers=_FINALIZE_WORKERS)
        _finalize_tasks = [asyncio.create_task(_finalize_worker()) for _ in range(_FINALIZE_WORKERS)]
        _task = asyncio.create_task(_consume_loop())


async def stop_consumer() -> None:
    global _task, _pool, _finalize_tasks
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None

    # 큐에 남은 finalize 작업이 있으면 처리될 시간을 잠깐 주고, 그래도 안 끝나면 포기하고 취소
    try:
        await asyncio.wait_for(_finalize_queue.join(), timeout=60)
    except asyncio.TimeoutError:
        logger.warning("finalize queue 드레인 타임아웃, 남은 작업 %d개 취소", _finalize_queue.qsize())

    for t in _finalize_tasks:
        t.cancel()
    for t in _finalize_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    _finalize_tasks = []

    if _pool is not None:
        _pool.shutdown(wait=True)
        _pool = None
