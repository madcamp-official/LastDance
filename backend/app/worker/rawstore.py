"""Raw 이벤트 저장 (dev-plan §3.3). 로컬 VM 서버 운용 전제라 S3 대신 로컬 디스크에 저장한다.
경로 구조는 S3 키(`{yyyy}/{mm}/{dd}/{problem_id}/{sid}.zst`)를 그대로 파일 경로에 대응.
재분석(알고리즘 규칙 변경 시 재처리) 용도로만 쓰고, 정상 서비스 경로에서는 읽지 않는다.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import List

import zstandard as zstd

import app.database  # noqa: F401  # .env 로드 트리거
from app.schema.analysis import EditOp

RAW_STORE_DIR = os.getenv("RAW_STORE_DIR", "./data/raw")


def raw_blob_path(problem_id: int, sid: str, when: datetime) -> Path:
    return (
        Path(RAW_STORE_DIR)
        / f"{when:%Y}" / f"{when:%m}" / f"{when:%d}"
        / str(problem_id) / f"{sid}.zst"
    )


def write_raw_blob(problem_id: int, sid: str, events: List[EditOp]) -> None:
    path = raw_blob_path(problem_id, sid, datetime.utcnow())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(e.model_dump_json() for e in events).encode("utf-8")
    path.write_bytes(zstd.ZstdCompressor().compress(payload))
