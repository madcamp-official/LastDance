from sqlalchemy import Column, DateTime, Integer, String
from app.database import Base


class JudgeSubmission(Base):
    """세션당 여러 번 제출 가능한 개별 채점 기록 (docs/api-spec.md 제출 절).

    app.model.session.Submission(세션 라이프사이클, session_id PK 1행)과는 별개로,
    한 세션 안에서의 각 제출 시도·결과를 별도 행으로 쌓는다.
    """

    __tablename__ = "judge_submissions"

    submission_id = Column(String, primary_key=True, unique=True, index=True, nullable=False)
    session_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    language = Column(String, nullable=False)
    code = Column(String, nullable=False)
    status = Column(String, nullable=False)  # "pending" | "judged"
    verdict = Column(String, nullable=True)  # "AC" | "WA" | "TLE" | "RE" | "CE" | None
    runtime_ms = Column(Integer, nullable=True)
    memory_kb = Column(Integer, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=False)
