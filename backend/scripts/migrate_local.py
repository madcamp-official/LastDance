"""로컬 DB 스키마 동기화 (alembic 없는 임시 마이그레이션).

app.main을 import하면 Kafka producer/consumer까지 딸려오므로, 여기서는 모델 모듈만
전부 import해서 Base.metadata를 채운 뒤 create_all + sync_missing_columns만 돌린다.
  - 새 테이블: create_all이 생성
  - 기존 테이블에 추가된 컬럼: sync_missing_columns가 ALTER TABLE ADD COLUMN
기존 데이터는 건드리지 않는다 (drop/rename 없음).

실행: backend 디렉토리에서  python -m scripts.migrate_local
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SQLALCHEMY_DATABASE_URL, Base, engine, sync_missing_columns  # noqa: E402

# Base.metadata 등록용 — main.py의 import 목록과 동일하게 유지할 것
from app.model import analysis as _analysis          # noqa: E402,F401
from app.model import ast_tree as _ast_tree          # noqa: E402,F401
from app.model import baseline as _baseline          # noqa: E402,F401
from app.model import feedback as _feedback          # noqa: E402,F401
from app.model import ingest as _ingest              # noqa: E402,F401
from app.model import problem as _problem            # noqa: E402,F401
from app.model import refresh_token as _refresh      # noqa: E402,F401
from app.model import session as _session            # noqa: E402,F401
from app.model import submission as _submission      # noqa: E402,F401
from app.model import summary as _summary            # noqa: E402,F401
from app.model import user as _user                  # noqa: E402,F401

from sqlalchemy import inspect  # noqa: E402


def main() -> int:
    print(f"DB: {SQLALCHEMY_DATABASE_URL}")
    before = set(inspect(engine).get_table_names())

    Base.metadata.create_all(engine)
    sync_missing_columns()

    after = set(inspect(engine).get_table_names())
    created = sorted(after - before)
    print(f"기존 테이블 {len(before)}개 -> 현재 {len(after)}개")
    print("생성된 테이블: " + (", ".join(created) if created else "(없음: 이미 최신)"))

    inspector = inspect(engine)
    for name in ("ast_tree_evolutions", "ast_snapshots", "ast_diff_events"):
        if name not in after:
            print(f"[FAIL] {name} 없음")
            return 1
        cols = [c["name"] for c in inspector.get_columns(name)]
        print(f"  {name}: {len(cols)} columns: {', '.join(cols)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
