from sqlalchemy import Column, Integer, String, JSON
from app.database import Base


class Problem(Base):
    __tablename__ = "problems"

    # 사용자가 모르는 정수 id (api-spec.md: problem_id는 integer)
    problem_id = Column(Integer, primary_key=True, index=True, nullable=False)
    title = Column(String, index=True, nullable=False)
    statement = Column(String, nullable=False)
    constraints = Column(String, nullable=True)
    examples = Column(JSON, nullable=True)  # [{"input": "...", "output": "..."}]
    source = Column(String, nullable=True)
    # AtCoder_100/{testcase_dir}/io/testcases.csv 채점용 폴더명(예: "p02537"). 공개 API에는 노출 안 함
    # (api-spec.md ProblemDetailResponse에는 없는 내부 전용 필드 — app/schema/problem.py에도 추가하지 않음).
    testcase_dir = Column(String, nullable=True)
    # 난이도 티어 NULL|"A".."G" (A=가장 쉬움, G=가장 어려움). keystroke-analysis-dev-plan.md §5.1 Enum8('A'..'G') 재사용.
    difficulty = Column(String, nullable=True)
