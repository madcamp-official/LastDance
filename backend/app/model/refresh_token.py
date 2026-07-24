from sqlalchemy import Column, String, DateTime
from app.database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    # 발급된 refresh token 원문을 PK로 저장. logout 시 해당 row를 삭제한다.
    refresh_token = Column(String, primary_key=True, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=True)
