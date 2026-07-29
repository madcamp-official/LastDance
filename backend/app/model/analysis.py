from sqlalchemy import Column, DateTime, Integer, String
from app.database import Base

# dev-plan §4.2 Feature Store. 계획서는 ClickHouse 기준이지만
# 현재 스택(SQLite/Postgres)에 맞춰 동일 컬럼 구성으로 구현.
# 멱등성(§8): 워커 재실행 시 sid 기준으로 기존 행을 지우고 다시 쓴다.


class SessionSummary(Base):
    __tablename__ = "session_summaries"

    # 세션당 1행
    sid = Column(String, primary_key=True, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    tier = Column(String, nullable=True)            # 난이도 티어 A~G (미정 시 NULL)
    lang = Column(String, nullable=True)
    analysis_level = Column(String, nullable=False)  # full | timing_only | degraded
    matcher_version = Column(Integer, nullable=False)
    total_ms = Column(Integer, nullable=False, default=0)
    setup_ms = Column(Integer, nullable=False, default=0)
    formation_ms = Column(Integer, nullable=False, default=0)
    debug_ms = Column(Integer, nullable=False, default=0)
    refine_ms = Column(Integer, nullable=False, default=0)
    keystroke_count = Column(Integer, nullable=False, default=0)
    pause_total_ms = Column(Integer, nullable=False, default=0)
    pause_count = Column(Integer, nullable=False, default=0)
    pivot_count = Column(Integer, nullable=False, default=0)
    local_rewrite_count = Column(Integer, nullable=False, default=0)  # 연구 7: 국소 반복 수정(TYPO 클러스터) 횟수
    code_bytes = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)


class PauseEventRow(Base):
    __tablename__ = "pause_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    t_ms = Column(Integer, nullable=False)
    duration_ms = Column(Integer, nullable=False)
    ast_label = Column(String, nullable=False, default="")   # INTERFACE_DESIGN 등
    pattern = Column(String, nullable=False, default="")     # 귀속된 구조 패턴
    phase = Column(String, nullable=False, default="")


class PivotEventRow(Base):
    __tablename__ = "pivot_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    t_ms = Column(Integer, nullable=False)
    deleted_chars = Column(Integer, nullable=False)
    pivot_type = Column(String, nullable=False, default="")
    pattern = Column(String, nullable=False, default="")


class PatternWindowRow(Base):
    __tablename__ = "pattern_windows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    pattern = Column(String, nullable=False)
    t_start_ms = Column(Integer, nullable=False)
    t_complete_ms = Column(Integer, nullable=False)
    formation_ms = Column(Integer, nullable=False)
    pause_ms_in_window = Column(Integer, nullable=False, default=0)
    pivot_count_in_window = Column(Integer, nullable=False, default=0)
