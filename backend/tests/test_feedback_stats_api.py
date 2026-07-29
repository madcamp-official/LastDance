"""피드백/비교통계 API 검증 (api-spec.md §피드백, §비교 통계).

실행: backend 디렉토리에서  python -m tests.test_feedback_stats_api
(pytest 없이도 도는 가벼운 스크립트형 테스트 — tests/test_worker.py와 동일 스타일)

주의: 실패해도 이 스크립트는 코드를 고치지 않는다. FAILURES만 모아서 보고한다.
"""

import sys
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.llm.client import LLM_MODEL
from app.main import app
from app.model.problem import Problem
from fastapi.testclient import TestClient

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---- 격리된 in-memory 테스트 DB로 get_db 오버라이드 (실제 app.db 오염 방지) ----
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


def _signup_and_login(email: str) -> str:
    client.post("/auth/signup", json={"email": email, "nickname": "tester", "password": "pw12345"})
    r = client.post("/auth/login", json={"email": email, "password": "pw12345"})
    return r.json()["access_token"]


def _seed_problem(problem_id: int) -> None:
    db = TestingSessionLocal()
    db.add(
        Problem(
            problem_id=problem_id,
            title="Two Sum",
            statement="배열에서 두 수의 합이 target인 인덱스를 찾아라.",
            constraints=None,
            examples=[{"input": "[2,7,11,15], 9", "output": "[0,1]"}],
            source=None,
        )
    )
    db.commit()
    db.close()


def _start_session(token: str, problem_id: int) -> str:
    r = client.post(
        "/sessions",
        json={"problem_id": problem_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    return r.json()["session_id"]


def run() -> None:
    _seed_problem(1)
    token_a = _signup_and_login(f"a_{uuid.uuid4().hex[:8]}@test.com")
    token_b = _signup_and_login(f"b_{uuid.uuid4().hex[:8]}@test.com")
    session_id = _start_session(token_a, 1)

    # ---- POST /feedback ----
    r = client.post(
        "/feedback",
        json={"session_id": session_id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    check("POST /feedback -> 200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    body = r.json() if r.status_code == 200 else {}
    check("POST /feedback 응답에 feedback_id 존재", "feedback_id" in body, str(body))
    # LLM 서버 미가용 환경에서는 fallback 문구가, 가용 환경에서는 생성 텍스트가 온다.
    check(
        "POST /feedback text 비어있지 않음",
        isinstance(body.get("text"), str) and len(body["text"]) > 0,
        str(body.get("text")),
    )
    check(
        f"POST /feedback model_used == {LLM_MODEL}",
        body.get("model_used") == LLM_MODEL,
        str(body.get("model_used")),
    )
    check(
        "POST /feedback generated_at ISO8601 'Z' 형식",
        isinstance(body.get("generated_at"), str) and body["generated_at"].endswith("Z"),
        str(body.get("generated_at")),
    )
    feedback_id = body.get("feedback_id")

    # ---- POST /feedback: 인증 없이 호출 -> 401 ----
    r = client.post("/feedback", json={"session_id": session_id})
    check("POST /feedback 인증 없음 -> 401", r.status_code == 401, f"status={r.status_code}")

    # ---- POST /feedback: 존재하지 않는 session_id -> 404 ----
    r = client.post(
        "/feedback",
        json={"session_id": "s_does_not_exist"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    check("POST /feedback 존재하지 않는 session_id -> 404", r.status_code == 404, f"status={r.status_code}")

    # ---- POST /feedback: 남의 세션 -> 403 ----
    r = client.post(
        "/feedback",
        json={"session_id": session_id},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    check("POST /feedback 소유권 없는 세션 -> 403", r.status_code == 403, f"status={r.status_code}")

    # ---- PATCH /feedback/{feedback_id}/rating ----
    r = client.patch(
        f"/feedback/{feedback_id}/rating",
        json={"rating": "up"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    check("PATCH rating up -> 200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    body = r.json() if r.status_code == 200 else {}
    check(
        "PATCH rating 응답 == {feedback_id, rating}",
        body.get("feedback_id") == feedback_id and body.get("rating") == "up",
        str(body),
    )

    # ---- PATCH rating: enum 범위 밖 값 -> 422 ----
    r = client.patch(
        f"/feedback/{feedback_id}/rating",
        json={"rating": "meh"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    check("PATCH rating 잘못된 enum 값 -> 422", r.status_code == 422, f"status={r.status_code}")

    # ---- PATCH rating: 존재하지 않는 feedback_id -> 404 ----
    r = client.patch(
        "/feedback/f_does_not_exist/rating",
        json={"rating": "down"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    check("PATCH rating 존재하지 않는 feedback_id -> 404", r.status_code == 404, f"status={r.status_code}")

    # ---- PATCH rating: 남의 피드백 -> 403 ----
    r = client.patch(
        f"/feedback/{feedback_id}/rating",
        json={"rating": "down"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    check("PATCH rating 소유권 없음 -> 403", r.status_code == 403, f"status={r.status_code}")

    # ---- GET /problems/{problem_id}/stats ----
    r = client.get("/problems/1/stats")
    check("GET /problems/1/stats -> 200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    body = r.json() if r.status_code == 200 else {}
    check("GET stats 응답에 problem_id 포함", body.get("problem_id") == 1, str(body))

    # ---- GET /problems/{problem_id}/stats: 존재하지 않는 문제 -> 404 ----
    r = client.get("/problems/9999/stats")
    check("GET /problems/9999/stats 존재하지 않음 -> 404", r.status_code == 404, f"status={r.status_code}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
    else:
        print("ALL PASSED")


if __name__ == "__main__":
    run()
    sys.exit(1 if FAILURES else 0)
