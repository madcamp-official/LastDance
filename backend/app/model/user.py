from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base


class User(Base):
    __tablename__ = "users"

    # 사용자가 모르는 정수 id
    user_id = Column(String, primary_key=True, unique=True, index=True, nullable=False)
    nickname = Column(String, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    profile_img = Column(String, nullable=True)
    hashed_password = Column(String, nullable=True)
    account_created = Column(DateTime, nullable=True)
    introduction = Column(String, nullable=True)