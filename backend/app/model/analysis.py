from sqlalchemy import Column, DateTime, Float, Integer, String
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
    # addendum §7: "rule"(결정론적 분류) | "llm"(구조 분류기 후보). llm 행은 통계 제외.
    source = Column(String, nullable=False, default="rule", server_default="rule")
    classifier_version = Column(String, nullable=True)   # source="llm"일 때만
    confidence = Column(Float, nullable=True)            # source="llm"일 때만


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
    # addendum §7: "rule" | "llm_candidate". llm_candidate는 기준선 삽입·피드백 프롬프트에서 제외.
    source = Column(String, nullable=False, default="rule", server_default="rule")
    classifier_version = Column(String, nullable=True)   # 프롬프트+모델 버전 (백필 기준)
    confidence = Column(Float, nullable=True)
    proposed_label = Column(String, nullable=True)       # pattern="OTHER"일 때 LLM 제안 라벨 (주 1회 검수 대상)


class UnmatchedSegmentRow(Base):
    """규칙 매처가 못 덮은 구조 변화 구간 (addendum §2~§3).

    diff 이벤트를 JSON으로 영속화해서
      - LLM 분류 실패/미가용 시에도 구간이 UNMATCHED로 남고 (§6.5)
      - classifier_version이 바뀌면 raw 재생 없이 재분류 백필이 가능하며 (§7)
      - UNMATCHED 비율(M3)·grounding 실패율(M3.5) 메트릭을 계산할 수 있다 (§8).
    상태 전이: pending(분류 전/LLM 미가용) → classified(후보 채택) | discarded(검증 최종 실패·저신뢰).
    """
    __tablename__ = "unmatched_segments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    segment_id = Column(String, nullable=False)          # "seg_0" 등, sid 내 유일
    t_start_ms = Column(Integer, nullable=False)
    t_end_ms = Column(Integer, nullable=False)
    diff_events_json = Column(String, nullable=False, default="[]")
    final_shape_json = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending", server_default="pending", index=True)
    pattern = Column(String, nullable=True)              # classified일 때 채택 라벨
    proposed_label = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    classifier_version = Column(String, nullable=True)   # 마지막으로 시도한 분류기 버전
    created_at = Column(DateTime(timezone=True), nullable=True)
