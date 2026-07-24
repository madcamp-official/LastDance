from pydantic import BaseModel, ConfigDict, EmailStr, Field
from typing import Optional

# ---- 요청/응답 스키마 ----
class SignupRequest(BaseModel):
    email: EmailStr
    nickname: str
    password: str


class SignupResponse(BaseModel):
    user_id: str
    nickname: str
    email: str
    profile_img: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    message: str

class UserResponse(BaseModel):
    user_id: str
    nickname: str
    email: EmailStr
    profile_img: Optional[str] = None
    created_at: str