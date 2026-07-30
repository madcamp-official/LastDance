"""문제별 피드백 사항 + 타임라인 (git-timeline-feedback-spec.md §3.3).

Stage B(LLM 세션 분석기)의 구조화 출력을 문제 단위로 축적한다.
같은 problem_id로 조회하면 다른 사용자들이 어느 단계에서 얼마나 걸렸는지가 나온다 —
이것이 비교군 집계(app/util/cohort.py)의 유일한 원천이며, 기존 AST 기준선
(app/util/baseline.py build_baseline)을 대체한다.
"""

from sqlalchemy import Column, DateTime, Index, Integer, String

from app.database import Base


class ProblemFeedbackInsight(Base):
    __tablename__ = "problem_feedback_insights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    problem_id = Column(Integer, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    sid = Column(String, index=True, nullable=False)
    stage = Column(String, nullable=False)          # §4.2 정준 단계 enum — 집계 키
    category = Column(String, nullable=False)       # "stall" | "churn" | "debug_loop" | "smooth"
    logic_label = Column(String, nullable=False)    # 자유 서술: "DP 점화식 도출" 등
    description = Column(String, nullable=False)    # 근거 요약 (한국어 1~2문장)
    severity = Column(String, nullable=False)       # "high" | "medium" | "low"
    commit_start_seq = Column(Integer, nullable=False)   # ← 피드백 사항 관련 타임라인
    commit_end_seq = Column(Integer, nullable=False)
    t_start_ms = Column(Integer, nullable=False)
    t_end_ms = Column(Integer, nullable=False)
    duration_ms = Column(Integer, nullable=False)
    evidence_json = Column(String, nullable=False, default="[]")  # 참조 커밋/세그먼트 id 목록
    advice = Column(String, nullable=True)
    analyzer_version = Column(String, nullable=False)   # 프롬프트+모델 버전 (백필 기준)
    status = Column(String, nullable=False, default="valid")  # valid | discarded (검증 실패)
    created_at = Column(DateTime(timezone=True), nullable=False)

    # 비교군 집계 쿼리 전용 복합 인덱스 (§3.3)
    __table_args__ = (
        Index("ix_problem_feedback_insights_problem_stage", "problem_id", "stage"),
    )
