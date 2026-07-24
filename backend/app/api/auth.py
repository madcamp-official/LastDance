import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schema.user import SignupRequest, SignupResponse, LoginRequest, TokenResponse, RefreshRequest, AccessTokenResponse, MessageResponse, UserResponse
from app.model.user import User
from app.model.refresh_token import RefreshToken
from app.util.security import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    get_password_hash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---- 공통 에러 포맷: {"error": {"code": "...", "message": "..."}} ----
class APIError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def install_error_handler(app: FastAPI) -> None:
    """APIError를 api-spec.md의 {"error": {...}} 포맷으로 직렬화한다. main에서 앱 생성 후 호출."""

    @app.exception_handler(APIError)
    async def _handle_api_error(request: Request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# ---- 엔드포인트 ----
@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, db: Session = Depends(get_db)):
    # 이메일 유일성 검증
    if db.query(User).filter(User.email == body.email).first():
        raise APIError(status.HTTP_409_CONFLICT, "EMAIL_TAKEN", "이미 가입된 이메일입니다.")

    user = User(
        user_id=str(uuid.uuid4()),
        nickname=body.nickname,
        email=body.email,
        profile_img=None,
        hashed_password=get_password_hash(body.password),
        account_created=datetime.now(tz=UTC),
        introduction=None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return SignupResponse(
        user_id=user.user_id,
        nickname=user.nickname,
        email=user.email,
        profile_img=user.profile_img,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise APIError(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_CREDENTIALS",
            "이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    access_token = create_access_token(data={"sub": user.email})
    refresh_token = create_refresh_token(data={"sub": user.email})

    db.add(
        RefreshToken(
            refresh_token=refresh_token,
            user_id=user.user_id,
            expires_at=datetime.now(tz=UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    db.commit()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    invalid = APIError(
        status.HTTP_401_UNAUTHORIZED,
        "INVALID_REFRESH_TOKEN",
        "유효하지 않거나 만료된 토큰입니다.",
    )

    # 1) 서버에 저장된 토큰인지 확인 (로그아웃/폐기된 토큰 차단)
    if not db.query(RefreshToken).filter(RefreshToken.refresh_token == body.refresh_token).first():
        raise invalid

    # 2) 서명/만료 검증
    try:
        payload = decode_token(body.refresh_token)
    except jwt.PyJWTError:
        raise invalid

    if payload.get("type") != "refresh":
        raise invalid

    email = payload.get("sub")
    if not email or not db.query(User).filter(User.email == email).first():
        raise invalid

    access_token = create_access_token(data={"sub": email})
    return AccessTokenResponse(access_token=access_token)


@router.post("/logout", response_model=MessageResponse)
async def logout(body: RefreshRequest, db: Session = Depends(get_db)):
    # 서버에 저장된 refresh token 폐기 (없어도 idempotent하게 성공 처리)
    db.query(RefreshToken).filter(RefreshToken.refresh_token == body.refresh_token).delete()
    db.commit()
    return MessageResponse(message="로그아웃 하였습니다.")


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    # 보안 스킴(HTTPBearer) 사용 라우트. Swagger에 Authorize 좌물쇠 버튼 생김.
    return UserResponse(
        user_id=current_user.user_id,
        nickname=current_user.nickname,
        email=current_user.email,
        profile_img=current_user.profile_img,
        created_at=current_user.account_created.isoformat(),
    )
