# DB 스키마 (실제 구현 기준, `backend/app/model/*.py`에서 추출)

> 이 문서는 예전에 "확정 가능한 범위"만 미리 설계했던 초안이었습니다. 백엔드가 SQLite(`backend/app.db`, `DATABASE_URL` 미설정 시 기본값) +
> SQLAlchemy로 실제 구현되면서 컬럼/테이블 구성이 초안과 달라졌습니다. 아래는 `Base.metadata.create_all(engine)`로 실제 생성되는
> 테이블을 SQLAlchemy 모델 정의 그대로 옮긴 것입니다. `id` 대신 애플리케이션이 발급하는 `uuid4` 문자열을 PK로 쓰는 테이블이 많고,
> UUID 컬럼도 SQLite 호환을 위해 `String` 타입으로 저장됩니다(Postgres 전환 시 `UUID` 타입으로 바꾸는 것을 검토).

```sql
-- 사용자
CREATE TABLE users (
  user_id         TEXT PRIMARY KEY,           -- 애플리케이션에서 발급하는 uuid4 문자열
  nickname        TEXT NOT NULL,
  email           TEXT UNIQUE NOT NULL,
  profile_img     TEXT,                        -- nullable, 기본값 null
  hashed_password TEXT,                        -- bcrypt (passlib)
  account_created TIMESTAMP,
  introduction    TEXT
);

-- refresh token (발급된 토큰 원문을 PK로 저장, 로그아웃 시 해당 행 삭제)
CREATE TABLE refresh_tokens (
  refresh_token TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  expires_at    TIMESTAMP                      -- 발급 시각 + 14일(REFRESH_TOKEN_EXPIRE_DAYS)
);
-- access token 만료: 30분(ACCESS_TOKEN_EXPIRE_MINUTES), refresh token 만료: 14일 — backend/app/util/security.py

-- 문제 (CodeNet의 AtCoder 문제 중 풀이기록 있는 것만 시딩)
CREATE TABLE problems (
  problem_id    INTEGER PRIMARY KEY,           -- api-spec.md: 공개 API에 그대로 노출되는 정수 id
  title         TEXT NOT NULL,
  statement     TEXT NOT NULL,
  constraints   TEXT,
  examples      JSON DEFAULT '[]',             -- [{ input, output }]
  source        TEXT,                          -- 예: "codenet_atcoder"
  testcase_dir  TEXT                            -- 채점용 내부 필드. AtCoder_100/{testcase_dir}/io/testcases.csv
                                                 -- 공개 API(ProblemDetailResponse)에는 노출하지 않음
);

-- 세션 (한 번의 문제 풀이 시도 — 라이프사이클만). 테이블명은 submissions이지만
-- "제출 기록"이 아니라 세션 자체를 뜻한다 (아래 judge_submissions와 혼동 주의).
CREATE TABLE submissions (
  session_id    TEXT PRIMARY KEY,              -- uuid4
  problem_id    INTEGER NOT NULL,
  user_id       TEXT NOT NULL,
  language      TEXT,                          -- 세션 시작 시점엔 NULL. 제출/종료 시 채워짐
  started_at    TIMESTAMP NOT NULL,
  ended_at      TIMESTAMP,
  final_status  TEXT                            -- NULL(=active) | 'solved' | 'abandoned'
);

-- 제출 (세션 하나에 여러 번 제출 가능 — 채점 시도 1건당 1행)
CREATE TABLE judge_submissions (
  submission_id TEXT PRIMARY KEY,               -- uuid4
  session_id    TEXT NOT NULL,
  problem_id    INTEGER NOT NULL,
  user_id       TEXT NOT NULL,
  language      TEXT NOT NULL,
  code          TEXT NOT NULL,
  status        TEXT NOT NULL,                  -- 'pending' | 'judged' (동기 채점이라 응답 시점엔 항상 'judged')
  verdict       TEXT,                            -- 'AC' | 'WA' | 'TLE' | 'RE' | 'CE' | NULL
  runtime_ms    INTEGER,
  memory_kb     INTEGER,
  submitted_at  TIMESTAMP NOT NULL
);
-- POST /submissions에서 verdict == 'AC'면 submissions(세션).final_status를 'solved'로,
-- ended_at/language를 함께 갱신한다(자동 세션 종료). 그 외 verdict는 세션을 active로 유지.

-- 인제스트 게이트웨이 세션 메타데이터 (Redis가 아닌 DB에 두는 것: 세션 메타 + 종료 여부만)
-- (sid, seq) 중복 제거 자체는 Redis(last_seq, TTL 24h)가 전담 — 여기엔 없음.
CREATE TABLE ingest_session_states (
  sid               TEXT PRIMARY KEY,           -- = submissions.session_id
  user_id           TEXT NOT NULL,
  problem_id        INTEGER NOT NULL,
  lang              TEXT,                        -- session.start 메시지에서 채움
  seq_gap_detected  BOOLEAN NOT NULL DEFAULT FALSE, -- true면 Replay Worker가 degraded로 판정
  ended             BOOLEAN NOT NULL DEFAULT FALSE, -- Replay Worker가 처리를 끝내면 true (멱등 처리용)
  created_at        TIMESTAMP NOT NULL
);

-- 키스트로크 분석 결과 — 세션당 1행 (Replay Worker가 session.end 수신 후 비동기로 채움)
CREATE TABLE session_summaries (
  sid               TEXT PRIMARY KEY,           -- = submissions.session_id
  user_id           TEXT NOT NULL,
  problem_id        INTEGER NOT NULL,
  tier              TEXT,                        -- 난이도 티어 A~G, 미정 시 NULL (미구현)
  lang              TEXT,
  analysis_level    TEXT NOT NULL,               -- 'full' | 'timing_only' | 'degraded'
  matcher_version   INTEGER NOT NULL,
  total_ms          INTEGER NOT NULL DEFAULT 0,
  setup_ms          INTEGER NOT NULL DEFAULT 0,
  formation_ms      INTEGER NOT NULL DEFAULT 0,
  debug_ms          INTEGER NOT NULL DEFAULT 0,
  refine_ms         INTEGER NOT NULL DEFAULT 0,
  keystroke_count   INTEGER NOT NULL DEFAULT 0,
  pause_total_ms    INTEGER NOT NULL DEFAULT 0,
  pause_count       INTEGER NOT NULL DEFAULT 0,
  pivot_count       INTEGER NOT NULL DEFAULT 0,
  code_bytes        INTEGER NOT NULL DEFAULT 0,
  created_at        TIMESTAMP NOT NULL
  -- 코드 전문(final_code)은 이 테이블/공개 API 어디에도 저장하지 않는다(개인정보 분리 원칙).
  -- 재생 재구성 코드는 raw blob(RAW_STORE_DIR, zstd 압축)에만 존재.
  --
  -- 2026-07-30 컬럼 의미 재정의 (git-timeline-feedback-spec.md §3.4) — 컬럼 구성은 그대로:
  --   analysis_level : 'full' | 'degraded' ('timing_only'는 타임라인 파이프라인에 없음 — 언어 무관)
  --   matcher_version: timeline_version 값을 기록
  --   setup_ms       : 항상 0 (SETUP 단계 개념 폐기)
  --   formation_ms   : STALL_SUSPECT 세그먼트 지속 합
  --   debug_ms       : DEBUG_LOOP + HIGH_CHURN 세그먼트 지속 합
  --   refine_ms      : STEADY + BURST_WRITE 세그먼트 지속 합
  --   pause_total_ms / pause_count : 커밋 경계 pause(pause_before_ms) 합 / 개수
  --   pivot_count / local_rewrite_count : 항상 0 (AST pivot 개념 폐기)
);

-- 정지(pause) 구간 — 세션당 N행
CREATE TABLE pause_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  sid          TEXT NOT NULL,
  user_id      TEXT NOT NULL,
  t_ms         INTEGER NOT NULL,
  duration_ms  INTEGER NOT NULL,
  ast_label    TEXT NOT NULL DEFAULT '',   -- INTERFACE_DESIGN | LOOP_BOUNDARY | BRANCH_CONDITION | ...
  pattern      TEXT NOT NULL DEFAULT '',   -- 귀속된 구조 패턴, 없으면 ''
  phase        TEXT NOT NULL DEFAULT ''    -- SETUP | FORMATION | DEBUG | REFINE
);

-- 재작성(pivot, 삭제 버스트) — 세션당 N행
CREATE TABLE pivot_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  sid           TEXT NOT NULL,
  user_id       TEXT NOT NULL,
  t_ms          INTEGER NOT NULL,
  deleted_chars INTEGER NOT NULL,
  pivot_type    TEXT NOT NULL DEFAULT '',  -- APPROACH_SWITCH | COMPLEXITY_FIX | EDGE_CASE_FIX | OTHER
  pattern       TEXT NOT NULL DEFAULT ''
);

-- 구조 패턴 형성 구간 — 세션당 N행 (패턴 하나당 1행)
CREATE TABLE pattern_windows (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  sid                    TEXT NOT NULL,
  user_id                TEXT NOT NULL,
  problem_id             INTEGER NOT NULL,
  pattern                TEXT NOT NULL,     -- BFS | DFS_RECURSIVE | DFS_ITERATIVE | BINARY_SEARCH | DP | GREEDY | DSU
  t_start_ms             INTEGER NOT NULL,
  t_complete_ms          INTEGER NOT NULL,
  formation_ms           INTEGER NOT NULL,
  pause_ms_in_window      INTEGER NOT NULL DEFAULT 0,
  pivot_count_in_window   INTEGER NOT NULL DEFAULT 0
);

-- 피드백 (LLM 응답 저장 통로)
CREATE TABLE feedbacks (
  feedback_id   TEXT PRIMARY KEY,           -- uuid4
  session_id    TEXT NOT NULL,
  text          TEXT NOT NULL,
  model_used    TEXT NOT NULL,
  generated_at  TIMESTAMP NOT NULL,
  rating        TEXT                         -- 'up' | 'down' | NULL
);

-- ============================================================================
-- git 방식 타임라인 파이프라인 (git-timeline-feedback-spec.md §3, 2026-07-30 신규)
-- 위 pause_events/pivot_events/pattern_windows/unmatched_segments/ast_* 는 신규 기록
-- 중단(과거 세션 조회용으로만 유지)되고, 신규 세션은 아래 두 테이블 + insights를 쓴다.
-- ============================================================================

-- 세션별 git 방식 코드 작성 기록 — 커밋 1개 = 1행 (제출도 같은 seq 공간)
CREATE TABLE code_commits (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  sid              TEXT NOT NULL,
  user_id          TEXT NOT NULL,
  problem_id       INTEGER NOT NULL,
  seq              INTEGER NOT NULL,          -- sid 내 순서
  kind             TEXT NOT NULL,             -- 'edit' | 'submit'
  t_ms             INTEGER NOT NULL,          -- 커밋을 닫은 시각
  pause_before_ms  INTEGER NOT NULL DEFAULT 0,-- 이 커밋 시작 전 정지 시간 (경계 임계값 5000ms)
  duration_ms      INTEGER NOT NULL DEFAULT 0,-- 커밋 안에서 타이핑한 시간
  hunks_json       TEXT NOT NULL DEFAULT '[]',-- [{op:add|del|mod, old_start, new_start, old_lines[], new_lines[]}]
  verdict          TEXT,                      -- kind='submit'일 때만: AC|WA|TLE|RE|CE|PENDING
  lines_added      INTEGER NOT NULL DEFAULT 0,
  lines_deleted    INTEGER NOT NULL DEFAULT 0,
  lines_modified   INTEGER NOT NULL DEFAULT 0,
  net_lines        INTEGER NOT NULL DEFAULT 0,-- 추가 − 삭제
  churn_lines      INTEGER NOT NULL DEFAULT 0,-- 누적 2회 이상 수정된 라인 수 (stable line id 기준)
  snapshot_hash    TEXT NOT NULL DEFAULT '',  -- sha256 앞 16자
  snapshot_text    TEXT,                      -- 제출·세션 종료 커밋만
  timeline_version INTEGER NOT NULL,
  UNIQUE (sid, seq)
);

-- 결정론적 라벨 구간 — 같은 라벨의 연속 편집 커밋 묶음 (제출이 경계)
CREATE TABLE session_segments (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  sid              TEXT NOT NULL,
  user_id          TEXT NOT NULL,
  problem_id       INTEGER NOT NULL,
  seg_id           TEXT NOT NULL,             -- 'sg_0' ...
  label            TEXT NOT NULL,             -- STALL_SUSPECT | HIGH_CHURN | DEBUG_LOOP | BURST_WRITE | STEADY
  commit_start_seq INTEGER NOT NULL,
  commit_end_seq   INTEGER NOT NULL,
  -- t_start_ms는 첫 커밋의 pause가 시작된 시점(= 직전 커밋의 마지막 이벤트)이다.
  -- 정지 시간을 구간에 포함시켜야 STALL_SUSPECT의 지속 시간이 "사고 시간"이 된다
  -- (timeline_version 2부터. v1은 타이핑 시간만 담아 STALL이 수 초로 찍혔다).
  -- 세그먼트들은 이 정의 아래 세션 시간축을 빈틈없이 분할한다.
  t_start_ms       INTEGER NOT NULL,
  t_end_ms         INTEGER NOT NULL,
  pause_ms         INTEGER NOT NULL DEFAULT 0,
  lines_touched    INTEGER NOT NULL DEFAULT 0,
  net_lines        INTEGER NOT NULL DEFAULT 0,
  timeline_version INTEGER NOT NULL
);

-- 문제별 피드백 사항 + 관련 타임라인 (LLM 세션 분석기 산출).
-- 같은 problem_id로 조회하면 다른 사용자들이 어느 단계에서 얼마나 걸렸는지가 나온다
-- — 비교군 집계(GET /problems/{id}/stats, POST /feedback)의 유일한 원천.
CREATE TABLE problem_feedback_insights (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  problem_id       INTEGER NOT NULL,
  user_id          TEXT NOT NULL,
  sid              TEXT NOT NULL,
  stage            TEXT NOT NULL,             -- 정준 enum 9종 (docs/api-spec.md Timeline 절)
  category         TEXT NOT NULL,             -- 'stall' | 'churn' | 'debug_loop' | 'smooth'
  logic_label      TEXT NOT NULL,             -- 자유 서술: 'DP 점화식 도출' 등
  description      TEXT NOT NULL,
  severity         TEXT NOT NULL,             -- 'high' | 'medium' | 'low'
  commit_start_seq INTEGER NOT NULL,
  commit_end_seq   INTEGER NOT NULL,
  t_start_ms       INTEGER NOT NULL,
  t_end_ms         INTEGER NOT NULL,
  duration_ms      INTEGER NOT NULL,
  evidence_json    TEXT NOT NULL DEFAULT '[]',
  advice           TEXT,
  analyzer_version TEXT NOT NULL,             -- 프롬프트+모델 버전 (백필 기준)
  status           TEXT NOT NULL DEFAULT 'valid'  -- 'valid' | 'discarded'(검증 실패, discard율 메트릭용)
);
CREATE INDEX ix_problem_feedback_insights_problem_stage
  ON problem_feedback_insights (problem_id, stage);

-- 사용자별 누적 통계 (테이블만 생성됨 — 현재 이 테이블을 채우거나 읽는 API 없음. 미사용/스캐폴드)
CREATE TABLE summaries (
  user_id           TEXT PRIMARY KEY,
  total_submission  INTEGER NOT NULL,
  total_correct     INTEGER NOT NULL,
  total_wrong       INTEGER NOT NULL
);
```

## 초안과 달라진 점 (참고용)

- `events` 테이블(자유 `event_type`/`payload` JSONB)은 실제로 만들어지지 않았습니다. 대신 원본 편집 이벤트는 DB가 아니라
  **Kafka 토픽(`keystroke-events`) → 로컬 디스크 raw blob(zstd 압축, `RAW_STORE_DIR`)** 에 보관되고, DB에는 Replay Worker가
  뽑아낸 파생 결과(`session_summaries`/`pause_events`/`pivot_events`/`pattern_windows`)만 저장됩니다. 원본 이벤트는 파생
  테이블에 남지 않으므로 `pauses[].event_index`/`pivots[].start_index` 등은 API 응답에서 항상 `-1`입니다.
- `submissions` 테이블 하나로 세션 라이프사이클 + 채점 결과를 같이 두려던 초안과 달리, 실제로는 **세션 라이프사이클(`submissions`
  테이블, PK `session_id`)과 채점 시도(`judge_submissions` 테이블, PK `submission_id`)가 분리**되어 있습니다. 세션 하나에
  제출은 여러 번 있을 수 있기 때문입니다.
- `users.id`/`problems.id` 등 컬럼명이 각각 `users.user_id`/`problems.problem_id`로, PK 컬럼명이 테이블마다 의미 있는 이름을
  씁니다(`id` 단일 관례 아님).
- `refresh_tokens`, `ingest_session_states`, `session_summaries`, `pause_events`, `pivot_events`, `pattern_windows`,
  `summaries`는 초안에 없던, 실제 구현 과정에서 추가된 테이블입니다.
- UUID 컬럼은 Postgres `UUID` 타입이 아니라 SQLite 호환을 위해 `TEXT`/`String`으로 저장됩니다(값 자체는 여전히 `uuid4()` 문자열).
- 현재 `DATABASE_URL` 미설정 시 SQLite(`sqlite:///./app.db`)를 기본값으로 씁니다. Postgres 전환 시점은 미확정입니다.

## 확장 예정 (지금 만들지 않음, 자리만 예약)

- `reference_stats` — CodeNet 기반 "다른 응시자 대비 통계" 저장용. `GET /problems/{id}/stats`는 현재도 자리표시자 메시지만
  반환하도록 구현되어 있고, 실제 통계 산출 로직/테이블은 아직 없습니다.
- `events.event_type` 값 목록을 제한하는 CHECK 제약 또는 별도 enum 관리 테이블 — 애초에 `events` 테이블 자체가 이제 Kafka로
  대체되어 해당 없음.
- `problems.difficulty`, 태그 등 메타데이터 확장 컬럼.
- `summaries` 테이블은 만들어져 있으나 실제로 채우는 로직이 없습니다 — 사용자별 누적 통계 기능 착수 시 이 테이블을 이어서 쓸지,
  새로 설계할지 팀 확인 필요.

## 마이그레이션 원칙

- 필드 추가는 하위호환 유지 (기존 컬럼 삭제·타입 변경 지양)
- 아직 불확실한 영역(`problems.examples` 등)은 JSON으로 유지해, 필드가 늘어나도 스키마 마이그레이션 없이 대응
- 현재 마이그레이션 도구(Alembic 등) 없이 `Base.metadata.create_all()`로만 테이블을 생성합니다 — 컬럼 변경 시 기존 `app.db`를
  지우고 재생성해야 합니다(운영 전환 전 마이그레이션 도구 도입 필요).
