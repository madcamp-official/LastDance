from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.database import Base

# dev-plan §3 Ingest Gateway 상태. last_seq 중복 제거는 Redis(§3.1)가 전담하므로
# 여기(Postgres)에는 세션 메타데이터와 finalize 완료 여부(멱등성, §8)만 둔다.


class IngestSessionState(Base):
    """세션별 인제스트 메타데이터. WS 연결 시점에 생성, Replay Worker(Kafka consumer)가 종료 처리."""

    __tablename__ = "ingest_session_states"

    sid = Column(String, primary_key=True, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, nullable=False)
    lang = Column(String, nullable=True)          # session.start 메시지에서 채움
    seq_gap_detected = Column(Boolean, nullable=False, default=False)  # §8: degraded 판정용
    ended = Column(Boolean, nullable=False, default=False)             # 멱등: 재처리 방지
    created_at = Column(DateTime(timezone=True), nullable=False)
