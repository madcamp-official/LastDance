"""로컬 E2E 테스트용 최소 데모 문제 1개를 심는다.

- problems 테이블에 problem_id=1 "세 정수의 합"을 넣고
- app/judge/testcases.py의 기본 ATCODER_DATA_DIR(레포 루트의 AtCoder_100/)에
  testcase_dir="sum3"용 testcases.csv를 만든다.

실행: backend/ 디렉토리에서 venv 활성화 후 `python scripts/seed_demo_problem.py`
(이미 문제가 있으면 아무것도 하지 않고 조용히 종료한다 — 여러 번 실행해도 안전)
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.database import Base, engine, SessionLocal
from app.model import (
    analysis,
    feedback,
    ingest,
    problem,
    session as session_model,
    submission,
    user,
)
from app.model.problem import Problem

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTCASE_DIR = REPO_ROOT / "AtCoder_100" / "sum3" / "io"

TESTCASES = [
    {"idx": 1, "input": "1 2 3\n", "output": "6\n"},
    {"idx": 2, "input": "10 20 30\n", "output": "60\n"},
    {"idx": 3, "input": "-5 5 100\n", "output": "100\n"},
]


def seed_problem() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(Problem).filter(Problem.problem_id == 1).first():
            print("problem_id=1 이미 존재 — 건너뜀")
            return
        db.add(
            Problem(
                problem_id=1,
                title="세 정수의 합",
                statement="세 정수 a, b, c가 주어질 때 그 합을 출력하시오.",
                constraints="-1000 <= a, b, c <= 1000",
                examples=[{"input": "1 2 3\n", "output": "6\n"}],
                source="demo_local",
                testcase_dir="sum3",
            )
        )
        db.commit()
        print("problem_id=1 시딩 완료 (testcase_dir=sum3)")
    finally:
        db.close()


def write_testcases() -> None:
    TESTCASE_DIR.mkdir(parents=True, exist_ok=True)
    path = TESTCASE_DIR / "testcases.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["idx", "input", "output"])
        writer.writeheader()
        writer.writerows(TESTCASES)
    print(f"testcases.csv 작성 완료: {path}")


if __name__ == "__main__":
    seed_problem()
    write_testcases()
