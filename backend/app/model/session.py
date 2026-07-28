from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.database import Base


class Submission(Base):
    __tablename__ = "submissions"

    # 한 번의 문제 풀이 시도와 그 결과도 가능하다면 저장할 수 있는 테이블
    session_id = Column(String, primary_key=True, unique=True, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    language = Column(String, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    final_status = Column(String, nullable=True)
