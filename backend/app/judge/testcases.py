"""AtCoder_100/{source}/io/testcases.csv 로딩.

description.html의 Sample Input/Output(io/input.txt, io/output.txt)은 지문 예시일 뿐이고,
실채점에 쓰는 실제 테스트케이스 전체는 io/testcases.csv(idx,input,output)에 들어있다.
"""

import csv
import os
from pathlib import Path
from typing import Iterator, NamedTuple

# backend/app/judge/testcases.py -> parents[3] == 저장소 루트(LastDance)
_REPO_ROOT = Path(__file__).resolve().parents[3]
ATCODER_DATA_DIR = Path(os.getenv("ATCODER_DATA_DIR", str(_REPO_ROOT / "AtCoder_100")))


class TestCase(NamedTuple):
    idx: int
    input: str
    output: str


class TestcasesNotFound(Exception):
    pass


def _testcases_csv_path(source: str) -> Path:
    return ATCODER_DATA_DIR / source / "io" / "testcases.csv"


def iter_testcases(source: str) -> Iterator[TestCase]:
    """idx 오름차순으로 테스트케이스를 하나씩 yield. 호출부가 중간에 순회를 멈추면
    (예: 첫 실패에서 채점 중단) 나머지 CSV는 읽지 않는다."""
    path = _testcases_csv_path(source)
    if not path.is_file():
        raise TestcasesNotFound(source)

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield TestCase(idx=int(row["idx"]), input=row["input"], output=row["output"])
