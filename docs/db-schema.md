# DB 스키마 (현재 확정 가능한 범위)

**설계 전략**: 아직 필드가 확정되지 않은 영역(이벤트 payload, 피드백 feature_summary, 비교 통계)은 처음부터 JSONB로 열어두어, 팀A의 구조화 산출물이 나온 뒤에도 마이그레이션 재작업 없이 대응할 수 있게 합니다.

```sql
-- 사용자
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  nickname      TEXT NOT NULL,
  profile_img   TEXT,                  -- nullable, api-spec.md의 회원가입 응답과 동기화
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 문제 (CodeNet의 AtCoder 문제 중 풀이기록 있는 것만 시딩)
-- id를 UUID가 아닌 BIGSERIAL로 결정 (api-spec.md의 problem_id: integer와 동기화, 팀 확정)
CREATE TABLE problems (
  id            BIGSERIAL PRIMARY KEY,
  source        TEXT NOT NULL DEFAULT 'codenet_atcoder',
  external_id   TEXT NOT NULL,                  -- CodeNet/AtCoder 원본 문제 ID
  title         TEXT NOT NULL,
  statement     TEXT NOT NULL,                  -- 라이선스 검토 전까지 원문 저장 여부 재검토 (CLAUDE.md 참고)
  constraints   TEXT,
  examples      JSONB NOT NULL DEFAULT '[]',    -- [{ input, output }]
  difficulty    TEXT,                            -- 미확정, 추후 채움
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, external_id)
);

-- 세션 (한 번의 문제 풀이 시도 — 라이프사이클만, 이벤트 세부와 무관)
CREATE TABLE sessions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id),
  problem_id    BIGINT NOT NULL REFERENCES problems(id),
  language      TEXT NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at      TIMESTAMPTZ,
  final_status  TEXT                             -- 'solved' | 'unsolved' | 'abandoned' | null(진행중)
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_problem ON sessions(problem_id);

-- 실시간 행동 이벤트 (제너릭 — event_type/payload 확정 전 형태 그대로 수용)
CREATE TABLE events (
  id            BIGSERIAL PRIMARY KEY,
  session_id    UUID NOT NULL REFERENCES sessions(id),
  event_type    TEXT NOT NULL,                   -- 자유 문자열, 추후 CHECK 제약/enum 테이블로 제한 예정
  payload       JSONB NOT NULL DEFAULT '{}',
  ts            TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_events_session ON events(session_id, ts);

-- 제출 (채점 로직 보류 — verdict 관련 컬럼은 전부 nullable)
CREATE TABLE submissions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    UUID NOT NULL REFERENCES sessions(id),
  user_id       UUID NOT NULL REFERENCES users(id),
  problem_id    BIGINT NOT NULL REFERENCES problems(id),
  code          TEXT NOT NULL,
  language      TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'judged' (채점 엔진 착수 후 세분화)
  verdict       TEXT,                              -- 'AC' | 'WA' | 'TLE' | 'RE' | 'CE' | null
  runtime_ms    INTEGER,
  memory_kb     INTEGER,
  submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_submissions_session ON submissions(session_id);

-- 피드백 (프롬프트/응답 내용 미확정 — 저장 통로만 확정)
CREATE TABLE feedback_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id      UUID NOT NULL REFERENCES sessions(id),
  feature_summary JSONB,                          -- 확장 예정: 팀A 산출물 확정 후 구체 구조 정의
  llm_response    TEXT,
  model_used      TEXT,
  rating          TEXT,                            -- 'up' | 'down' | null
  generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## 확장 예정 (지금 만들지 않음, 자리만 예약)

- `reference_stats` — CodeNet 기반 "다른 응시자 대비 통계" 저장용. 팀A의 구조화 산출물 확정 후 설계
- `events.event_type` 값 목록을 제한하는 CHECK 제약 또는 별도 enum 관리 테이블
- `problems.difficulty`, 태그 등 메타데이터 확장 컬럼

## 마이그레이션 원칙

- 필드 추가는 `ALTER TABLE ... ADD COLUMN`으로 하위호환 유지 (기존 컬럼 삭제·타입 변경 지양)
- `events.payload`, `feedback_log.feature_summary`처럼 아직 불확실한 영역은 JSONB로 유지해, 이벤트 타입/피처가 늘어나도 스키마 마이그레이션 없이 대응
