"""제출/채점 API 검증 (docs/api-spec.md §제출, backend/app/judge).

실행: backend 디렉토리에서  python -m tests.test_submission_api
(pytest 없이도 도는 가벼운 스크립트형 테스트 — tests/test_feedback_stats_api.py와 동일 스타일)

Judge0는 실제로 띄우지 않는다: FakeJudge0Client로 app.api.submission.get_judge_client를
오버라이드해 net/Docker 의존 없이 판정 로직(엔진 + API 라우팅)만 검증한다.
"""

import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.judge.testcases as testcases_module
from app.api.submission import get_judge_client
from app.database import Base, get_db
from app.judge.client import Judge0Result
from app.main import app
from app.model.problem import Problem
from fastapi.testclient import TestClient

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---- 격리된 in-memory 테스트 DB ----
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

# ---- 임시 AtCoder 테스트케이스 픽스처: 실제 AtCoder_100 디렉토리에 의존하지 않는다 ----
_FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="judge_fixture_"))
_PROBLEM_SOURCE = "p_fixture"
_io_dir = _FIXTURE_ROOT / _PROBLEM_SOURCE / "io"
_io_dir.mkdir(parents=True)
(_io_dir / "testcases.csv").write_text(
    'idx,input,output\n1,"1 2\n",3\n2,"5 5\n",10\n', encoding="utf-8"
)
testcases_module.ATCODER_DATA_DIR = _FIXTURE_ROOT


class FakeJudge0Client:
    """stdin의 두 정수를 더한 값을 stdout으로 돌려주는 정답 코드처럼 흉내낸다.
    code 문자열에 특수 마커를 넣어 시나리오(정답/오답/TLE/CE/RE)를 강제한다."""

    def run(self, *, source_code, language_id, stdin, cpu_time_limit, memory_limit_kb, compiler_options=None):
        if source_code == "__CE__":
            return Judge0Result(6, "Compilation Error", "", "", "boom", None, None, None)
        if source_code == "__TLE__":
            return Judge0Result(5, "Time Limit Exceeded", "", "", "", None, None, None)
        if source_code == "__RE__":
            return Judge0Result(11, "Runtime Error (NZEC)", "", "traceback", "", 0.01, 1000, None)
        if source_code == "__MLE__":
            # judge0에는 별도 MLE status가 없어 SIGKILL 계열이 RE(Other)로 옴 — memory_kb가
            # 기본 메모리 제한(1024MB=1,048,576KB)을 넘겨서 엔진이 MLE로 재분류해야 한다.
            return Judge0Result(12, "Runtime Error (Other)", "", "", "", 0.05, 2_000_000, None)
        if source_code == "__MLE_SIGKILL__":
            # memory_kb가 None/피크보다 낮게 찍혀도 exit_signal=9(SIGKILL)로 MLE 판정돼야 한다.
            return Judge0Result(12, "Runtime Error (Other)", "", "", "", 0.05, None, 9)
        if source_code == "__WRONG__":
            return Judge0Result(3, "Accepted", "999\n", "", "", 0.02, 2000, None)
        # 정답 흉내: "a b" -> a+b
        a, b = (int(x) for x in stdin.split())
        return Judge0Result(3, "Accepted", f"{a + b}\n", "", "", 0.02, 2000, None)


app.dependency_overrides[get_judge_client] = lambda: FakeJudge0Client()
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
            title="Add Two Numbers",
            statement="두 정수의 합을 출력하라.",
            constraints=None,
            examples=[{"input": "1 2", "output": "3"}],
            source=None,
            testcase_dir=_PROBLEM_SOURCE,
        )
    )
    db.commit()
    db.close()


def _start_session(token: str, problem_id: int) -> str:
    r = client.post(
        "/sessions", json={"problem_id": problem_id}, headers={"Authorization": f"Bearer {token}"}
    )
    return r.json()["session_id"]


def run() -> None:
    _seed_problem(1)
    token_a = _signup_and_login(f"a_{uuid.uuid4().hex[:8]}@test.com")
    token_b = _signup_and_login(f"b_{uuid.uuid4().hex[:8]}@test.com")

    def submit(token, session_id, code, language="python3", problem_id=1):
        return client.post(
            "/submissions",
            json={"session_id": session_id, "problem_id": problem_id, "code": code, "language": language},
            headers={"Authorization": f"Bearer {token}"},
        )

    # ---- WA: 세션은 계속 진행, 기록만 남음 ----
    sid = _start_session(token_a, 1)
    r = submit(token_a, sid, "__WRONG__")
    check("POST /submissions WA -> 200", r.status_code == 200, f"{r.status_code} {r.text}")
    body = r.json()
    submission_id = body.get("submission_id")

    r = client.get(f"/submissions/{submission_id}", headers={"Authorization": f"Bearer {token_a}"})
    check("GET 제출 상세 verdict == WA", r.json().get("verdict") == "WA", r.text)
    check("WA는 runtime/memory 기록 안 함", r.json().get("runtime_ms") is None, r.text)

    r = client.get(f"/sessions/{sid}", headers={"Authorization": f"Bearer {token_a}"})
    check("WA 후에도 세션 status == active", r.json().get("status") == "active", r.text)

    # ---- AC: 세션 자동 종료(solved) ----
    r = submit(token_a, sid, "correct")  # 마커 없음 -> a+b 정답 경로
    check("POST /submissions AC -> 200", r.status_code == 200, f"{r.status_code} {r.text}")
    ac_submission_id = r.json().get("submission_id")

    r = client.get(f"/submissions/{ac_submission_id}", headers={"Authorization": f"Bearer {token_a}"})
    ac_body = r.json()
    check("GET 제출 상세 verdict == AC", ac_body.get("verdict") == "AC", r.text)
    check("AC는 runtime_ms 기록됨", isinstance(ac_body.get("runtime_ms"), int), r.text)
    check("AC는 memory_kb 기록됨", isinstance(ac_body.get("memory_kb"), int), r.text)

    r = client.get(f"/sessions/{sid}", headers={"Authorization": f"Bearer {token_a}"})
    check("AC 후 세션 status == solved", r.json().get("status") == "solved", r.text)

    # ---- 이미 종료된 세션에 재제출 -> 409 ----
    r = submit(token_a, sid, "correct")
    check("종료된 세션 재제출 -> 409", r.status_code == 409, f"{r.status_code} {r.text}")

    # ---- CE / TLE / RE 판정 ----
    for marker, expected in (
        ("__CE__", "CE"),
        ("__TLE__", "TLE"),
        ("__RE__", "RE"),
        ("__MLE__", "MLE"),
        ("__MLE_SIGKILL__", "MLE"),
    ):
        sid2 = _start_session(token_a, 1)
        r = submit(token_a, sid2, marker)
        check(f"POST /submissions {expected} -> 200", r.status_code == 200, f"{r.status_code} {r.text}")
        sub_id = r.json().get("submission_id")
        r = client.get(f"/submissions/{sub_id}", headers={"Authorization": f"Bearer {token_a}"})
        check(f"verdict == {expected}", r.json().get("verdict") == expected, r.text)

    # ---- 지원하지 않는 언어 -> 400 ----
    sid3 = _start_session(token_a, 1)
    r = submit(token_a, sid3, "correct", language="brainfuck")
    check("지원하지 않는 언어 -> 400", r.status_code == 400, f"{r.status_code} {r.text}")

    # ---- 남의 세션에 제출 -> 403 ----
    r = submit(token_b, sid3, "correct")
    check("소유권 없는 세션에 제출 -> 403", r.status_code == 403, f"{r.status_code} {r.text}")

    # ---- 존재하지 않는 세션 -> 404 ----
    r = submit(token_a, "s_does_not_exist", "correct")
    check("존재하지 않는 세션 -> 404", r.status_code == 404, f"{r.status_code} {r.text}")

    # ---- GET /submissions?session_id= 이력 조회 ----
    r = client.get(f"/submissions?session_id={sid}", headers={"Authorization": f"Bearer {token_a}"})
    check("GET 제출 이력 -> 200", r.status_code == 200, f"{r.status_code} {r.text}")
    items = r.json().get("items", [])
    check("제출 이력 2건(WA, AC) 순서대로", [i["verdict"] for i in items] == ["WA", "AC"], str(items))

    r = client.get(f"/submissions?session_id={sid}", headers={"Authorization": f"Bearer {token_b}"})
    check("남의 제출 이력 조회 -> 403", r.status_code == 403, f"{r.status_code} {r.text}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
    else:
        print("ALL PASSED")


if __name__ == "__main__":
    try:
        run()
    finally:
        shutil.rmtree(_FIXTURE_ROOT, ignore_errors=True)
    sys.exit(1 if FAILURES else 0)
