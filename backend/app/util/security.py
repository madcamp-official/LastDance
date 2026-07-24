from pathlib import Path

from passlib.context import CryptContext

import jwt, os, uuid
from datetime import UTC, datetime, timedelta
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from sqlalchemy.orm import Session

from app.database import get_db
from app.model.user import User

from typing import Optional


def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

SECRET_KEY = os.getenv("SECRET_KEY")  # 환경변수로 관리
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY 환경변수가 설정되지 않았습니다.")
ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES = 30  # access token 유효 시간 (분)
REFRESH_TOKEN_EXPIRE_DAYS = 14    # refresh token 유효 시간 (일)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)) -> str:
    to_encode = data.copy()
    expire = datetime.now(tz=UTC) + expires_delta

    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)) -> str:
    to_encode = data.copy()
    expire = datetime.now(tz=UTC) + expires_delta

    # jti로 매 발급마다 토큰을 유일하게 만든다.
    to_encode.update({"exp": expire, "type": "refresh", "jti": str(uuid.uuid4())})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> dict:
    # 만료/서명 오류 시 jwt.PyJWTError를 그대로 던진다. 호출부에서 처리.
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

bearer_Scheme = HTTPBearer()

async def get_current_user(
    token: HTTPAuthorizationCredentials = Depends(bearer_Scheme),  # Header에 포함된 토큰
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증 정보를 확인할 수 없습니다",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = token.credentials  # HTTPAuthorizationCredentials 객체에서 토큰 문자열 추출

    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise credentials_exception

    # access token만 인증에 허용 (refresh token 재사용 차단)
    if payload.get("type") != "access":
        raise credentials_exception
    email: str = payload.get("sub")

    # DB로부터 사용자 조회
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception

    return user
