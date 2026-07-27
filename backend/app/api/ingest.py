"""Ingest Gateway (dev-plan §3). stateless: 인증/소유권 검증, 스키마 검증,
(sid, seq) 중복 제거(Redis), Kafka append, ack 응답만 한다.
AST 파싱 등 무거운 연산은 하지 않는다 — 그건 app/worker/consumer.py(Replay Worker)가
Kafka 이벤트 로그를 구독해 session.end 시점에 비동기로 수행한다.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.ingest import IngestSessionState
from app.model.session import Submission
from app.model.user import User
from app.schema.ingest import (
    MAX_BATCH_BYTES,
    MAX_BATCH_EVENTS,
    BeaconRequest,
    EditBatchMessage,
    HeartbeatMessage,
    SessionEndMessage,
    SessionStartMessage,
    SubmissionMarkMessage,
)
from app.util.messaging import KAFKA_TOPIC, LAST_SEQ_TTL_SECONDS, get_producer, get_redis
from app.util.security import decode_token

router = APIRouter(tags=["ingest"])

HEARTBEAT_TIMEOUT_SECONDS = 90.0  # dev-plan §2.3: 90초간 하트비트 없으면 서버가 강제 종료


def _authenticate(token: str, db: Session) -> Optional[User]:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return db.query(User).filter(User.email == payload.get("sub")).first()


def _load_owned_session(sid: str, user_id: str, db: Session) -> Optional[Submission]:
    sub = db.query(Submission).filter(Submission.session_id == sid).first()
    if not sub or sub.user_id != user_id:
        return None
    return sub


def _get_or_create_state(db: Session, sid: str, sub: Submission) -> IngestSessionState:
    state = db.query(IngestSessionState).filter(IngestSessionState.sid == sid).first()
    if state is None:
        state = IngestSessionState(
            sid=sid,
            user_id=sub.user_id,
            problem_id=sub.problem_id,
            lang=None,
            seq_gap_detected=False,
            ended=False,
            created_at=datetime.now(tz=UTC),
        )
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


async def _publish(sid: str, payload: dict) -> None:
    producer = get_producer()
    await producer.send_and_wait(
        KAFKA_TOPIC, value=json.dumps(payload).encode("utf-8"), key=sid.encode("utf-8")
    )


async def _check_seq(sid: str, seq: int) -> tuple[bool, bool]:
    """Redis 기반 (sid, seq) 중복 제거(§3.1). (is_duplicate, gap_detected) 반환."""
    redis = get_redis()
    key = f"ingest:last_seq:{sid}"
    last = await redis.get(key)
    last_seq = int(last) if last is not None else -1
    if seq <= last_seq:
        return True, False
    gap = seq > last_seq + 1
    await redis.set(key, seq, ex=LAST_SEQ_TTL_SECONDS)
    return False, gap


def _batch_too_large(ops: list) -> bool:
    if len(ops) > MAX_BATCH_EVENTS:
        return True
    size = len(json.dumps([op.model_dump() for op in ops]).encode("utf-8"))
    return size > MAX_BATCH_BYTES


@router.websocket("/ws/events")
async def ws_events(
    websocket: WebSocket,
    session_id: str = Query(...),
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    user = _authenticate(token, db)
    if user is None:
        await websocket.close(code=4401)
        return
    sub = _load_owned_session(session_id, user.user_id, db)
    if sub is None:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    state = _get_or_create_state(db, session_id, sub)
    redis = get_redis()
    last = await redis.get(f"ingest:last_seq:{session_id}")
    await websocket.send_json(
        {"type": "resume", "sid": session_id, "last_seq": int(last) if last is not None else -1}
    )

    async def end(reason: str, t: int = 0) -> None:
        await _publish(session_id, {"type": "session.end", "sid": session_id, "t": t, "reason": reason})

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_json(), timeout=HEARTBEAT_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                await end("timeout")
                await websocket.close(code=1000)
                return

            msg_type = raw.get("type")

            if msg_type == "session.start":
                try:
                    msg = SessionStartMessage(**raw)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "sid": session_id, "code": "SCHEMA_INVALID", "seq": None}
                    )
                    continue
                await _publish(session_id, msg.model_dump())

            elif msg_type == "edit.batch":
                try:
                    msg = EditBatchMessage(**raw)
                except ValidationError:
                    await websocket.send_json(
                        {
                            "type": "error", "sid": session_id, "code": "SCHEMA_INVALID",
                            "seq": raw.get("seq"),
                        }
                    )
                    continue
                if _batch_too_large(msg.ops):
                    await websocket.send_json(
                        {"type": "error", "sid": session_id, "code": "SCHEMA_INVALID", "seq": msg.seq}
                    )
                    continue

                is_dup, gap = await _check_seq(session_id, msg.seq)
                if gap:
                    state.seq_gap_detected = True
                    db.commit()
                if not is_dup:
                    await _publish(session_id, msg.model_dump())
                await websocket.send_json({"type": "ack", "sid": session_id, "seq": msg.seq})

            elif msg_type == "session.heartbeat":
                try:
                    HeartbeatMessage(**raw)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "sid": session_id, "code": "SCHEMA_INVALID", "seq": None}
                    )
                # 하트비트는 저장 대상이 아님 — 수신 자체가 연결 생존의 증거(§2.3)

            elif msg_type == "submission.mark":
                try:
                    msg = SubmissionMarkMessage(**raw)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "sid": session_id, "code": "SCHEMA_INVALID", "seq": None}
                    )
                    continue
                await _publish(session_id, msg.model_dump())

            elif msg_type == "session.end":
                try:
                    msg = SessionEndMessage(**raw)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "sid": session_id, "code": "SCHEMA_INVALID", "seq": None}
                    )
                    continue
                await _publish(session_id, msg.model_dump())
                await websocket.close(code=1000)
                return

            else:
                await websocket.send_json(
                    {"type": "error", "sid": session_id, "code": "SCHEMA_INVALID", "seq": raw.get("seq")}
                )

    except WebSocketDisconnect:
        # 네트워크 단절/탭 종료 등 비정상 종료 — reason="closed"로 합성해 Replay Worker가
        # 지금까지 수신된 이벤트만으로라도 마감 처리하도록 트리거(§8)
        await end("closed")
    finally:
        db.close()


@router.post("/events/beacon", status_code=status.HTTP_204_NO_CONTENT)
async def events_beacon(
    body: BeaconRequest,
    session_id: str = Query(...),
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    user = _authenticate(token, db)
    if user is None:
        raise APIError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "인증이 필요합니다.")
    sub = _load_owned_session(session_id, user.user_id, db)
    if sub is None:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")

    state = _get_or_create_state(db, session_id, sub)
    if _batch_too_large(body.ops):
        raise APIError(status.HTTP_400_BAD_REQUEST, "SCHEMA_INVALID", "배치 크기 상한을 초과했습니다.")

    is_dup, gap = await _check_seq(session_id, body.seq)
    if gap:
        state.seq_gap_detected = True
        db.commit()
    if not is_dup:
        await _publish(
            session_id,
            {
                "type": "edit.batch", "sid": session_id, "seq": body.seq,
                "base_t": body.base_t, "ops": [op.model_dump() for op in body.ops],
            },
        )
    return None
