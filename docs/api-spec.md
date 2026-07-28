# API 명세 v1 (현재 확정 가능한 범위)

Base URL: `/api/v1`
버전 전략: 확장 시 `/api/v2`로 분리. 기존 필드는 유지한 채 추가만 하는 방식으로 하위호환 지향.
공통 에러 포맷: `{"error": {"code": "...", "message": "..."}}`
날짜 포맷: ISO 8601 UTC 문자열 (예: `"2026-07-24T05:00:00Z"`)
ID 표기: `problem_id`는 `db-schema.md`의 `problems.id`(BIGSERIAL) 그대로 노출되는 **integer**입니다 (팀 확정). 그 외 `user_id`/`session_id`/`submission_id`/`feedback_id` 등은 `db-schema.md`에 정의된 UUID 문자열이며, 아래 예시의 `"1"`, `"p_001"` 등은 가독성을 위한 단순화 값입니다.

---

## 인증 (Auth)

| Method | Endpoint | 설명 | 요청 (Body) | 응답 |
|---|---|---|---|---|
| POST | `/auth/signup` | 회원가입 | `{"email": "example@example.com", "nickname": "example001", "password": "test12345"}` | 201 `{"user_id": "1", "nickname": "example001", "email": "example@example.com", "profile_img": null}` |
| POST | `/auth/login` | 로그인, access/refresh 토큰 발급 | `{"email": "example@example.com", "password": "test12345"}` | 200 `{"access_token": "<jwt>", "refresh_token": "<jwt>", "token_type": "bearer"}` |
| POST | `/auth/refresh` | refresh token으로 access token 재발급 | `{"refresh_token": "<jwt>"}` | 200 `{"access_token": "<jwt>", "token_type": "bearer"}` |
| POST | `/auth/logout` | 서버에 저장된 refresh token 폐기 | `{"refresh_token": "<jwt>"}` | 200 `{"message": "로그아웃 하였습니다."}` |

**POST /auth/signup**

요청 필드:
| 키 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| email | string | O | `"example@example.com"` | 로그인 ID로 사용, 서버에서 유일성 검증 |
| nickname | string | O | `"example001"` | 2~20자 제한 (팀 확정 필요) |
| password | string | O | `"test12345"` | 서버에서 bcrypt/argon2로 해싱 후 저장, 평문 저장 금지 |

응답 필드 (201):
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| user_id | string | `"1"` | 실제 값은 UUID |
| nickname | string | `"example001"` | |
| email | string | `"example@example.com"` | |
| profile_img | string \| null | `null` | 기본값 null, 이미지 업로드 기능은 확장 예정 |

에러: 409 `{"error": {"code": "EMAIL_TAKEN", "message": "이미 가입된 이메일입니다."}}`

**POST /auth/login**

요청 필드:
| 키 | 타입 | 필수 | 예시 |
|---|---|---|---|
| email | string | O | `"example@example.com"` |
| password | string | O | `"test12345"` |

응답 필드 (200):
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| access_token | string (JWT) | `"eyJhbGciOi..."` | 만료 시간은 서버 설정값 (팀 확정 필요, 예: 30분) |
| refresh_token | string (JWT) | `"eyJhbGciOi..."` | 만료 시간 예: 14일 (팀 확정 필요) |
| token_type | string | `"bearer"` | 고정값 |

에러: 401 `{"error": {"code": "INVALID_CREDENTIALS", "message": "이메일 또는 비밀번호가 올바르지 않습니다."}}`

**POST /auth/refresh**

요청: `{"refresh_token": "<jwt>"}` (키: refresh_token, 타입: string, 필수)
응답 (200): `{"access_token": "<jwt>", "token_type": "bearer"}`
에러: 401 `{"error": {"code": "INVALID_REFRESH_TOKEN", "message": "유효하지 않거나 만료된 토큰입니다."}}`

**POST /auth/logout**

요청: `{"refresh_token": "<jwt>"}` (키: refresh_token, 타입: string, 필수)
응답 (200): `{"message": "로그아웃 하였습니다."}` (키: message, 타입: string)

**GET /auth/me** (Header: `Authorization: Bearer {access_token}`)

응답 (200):
| 키 | 타입 | 예시 |
|---|---|---|
| user_id | string | `"1"` |
| email | string | `"example@example.com"` |
| nickname | string | `"example001"` |
| profile_img | string \| null | `null` |
| created_at | string (ISO8601) | `"2026-07-24T05:00:00Z"` |

에러: 401 `{"error": {"code": "UNAUTHORIZED", "message": "인증이 필요합니다."}}`

---

## 문제 카탈로그 (Problems) — 확정

| Method | Endpoint | 설명 | 요청 (Query/Body) | 응답 |
|---|---|---|---|---|
| GET | `/problems` | 문제 목록 조회 | Query: `?page=1&page_size=20` | 200 `{"items": [{"problem_id": 1, "title": "Welcome to AtCoder"}], "page": 1, "page_size": 20, "total_count": 128}` |
| GET | `/problems/{problem_id}` | 문제 상세 조회 | - | 200 `{"problem_id": 1, "title": "Welcome to AtCoder", "statement": "You are given...", "constraints": "1<=a,b,c<=1000", "examples": [{"input": "1\n2 3\n", "output": "6"}], "source": "codenet_atcoder"}` |

**GET /problems** 요청 쿼리:
| 키 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| page | integer | X (기본 1) | `1` | 1-base |
| page_size | integer | X (기본 20) | `20` | 최대값 팀 확정 필요 (예: 100) |

**GET /problems** 응답 필드:
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| items | array\<object\> | 아래 참고 | |
| items[].problem_id | integer | `1` | |
| items[].title | string | `"Welcome to AtCoder"` | |
| page | integer | `1` | |
| page_size | integer | `20` | |
| total_count | integer | `128` | 전체 문제 수 |

> `difficulty`, `tags` 필터는 CodeNet 메타데이터 확정 후 쿼리 파라미터로 추가 (하위호환 추가)

**GET /problems/{problem_id}** 응답 필드:
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| problem_id | integer | `1` | |
| title | string | `"Welcome to AtCoder"` | |
| statement | string | `"You are given..."` | 라이선스 검토 전까지 원문 노출 여부 재검토 (`CLAUDE.md` 참고) |
| constraints | string \| null | `"1<=a,b,c<=1000"` | |
| examples | array\<object\> | `[{"input": "1\n2 3\ntest", "output": "6 test"}]` | |
| examples[].input | string | `"1\n2 3\n"` | |
| examples[].output | string | `"6"` | |
| source | string | `"codenet_atcoder"` | |

에러: 404 `{"error": {"code": "PROBLEM_NOT_FOUND", "message": "문제를 찾을 수 없습니다."}}`

---

## 세션 (Sessions) — 확정

문제 풀이 시작~종료 라이프사이클만 다룸. 이벤트 세부 스키마와 무관.

| Method | Endpoint | 설명 | 요청 (Body) | 응답 |
|---|---|---|---|---|
| POST | `/sessions` | 문제 풀이 세션 시작 | `{"problem_id": 1}` | 200 `{"session_id": "s_001", "problem_id": 1, "user_id": "u_001", "title": "Welcome to AtCoder", "statement": "You are given...", "constraints": "1<=a,b,c<=1000", "examples": [{"input": "1\n2 3\n", "output": "6"}], "source": "codenet_atcoder"}` |
| PATCH | `/sessions/{session_id}` | 세션 종료 처리 | `{"status": "solved", "language": "python3"}` | 200 `{"session_id": "s_001", "status": "solved"}` |
| GET | `/sessions/{session_id}` | 세션 상세 조회 | - | 200 `{"session_id": "s_001", "user_id": "1", "problem_id": 1, "language": "python3", "started_at": "2026-07-24T05:00:00Z", "ended_at": null, "status": "active"}` |

**POST /sessions** 요청:
| 키 | 타입 | 필수 | 예시 |
|---|---|---|---|
| problem_id | integer | O | `1` |
// 언어는 문제를 보고 택할 수 있어야 하므로 요청 body에서 제거

**PATCH /sessions/{session_id}** 요청:
| 키 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| status | string (enum) | O | `"solved"` | `"solved"` \| `"abandoned"` |
//unsolved라는 enum 제거, 서버가 시간을 측정해도 상관 없어서 시간 field도 제거.

응답 공통 필드(세 엔드포인트): `session_id`(string), `user_id`(string), `problem_id`(integer), `started_at`(string, ISO8601), `ended_at`(string\|null), `status`(string enum: `"active"`\|`"solved"`\|`"abandoned"`)

---
// keystroke-analysis-dev-plan.md와 같이 읽어야 이해하기 쉬움.
## 실시간 이벤트 수집 (Ingest Gateway) — 확정

`keystroke-analysis-dev-plan.md` §2~3 반영. 에디터 편집 연산(EditOp) 스키마가 확정되어(`backend/app/schema/analysis.py`) 더 이상 자유 페이로드가 아님. 게이트웨이는 스키마 검증·`(sid, seq)` 중복 제거·인증만 수행하고, AST 파싱 등 무거운 연산은 하지 않는다(Replay Worker가 세션 종료 후 비동기로 수행 — 아래 "키스트로크 분석" 섹션 참고).

**구현 완료**: 게이트웨이는 `backend/app/api/ingest.py`. `(sid, seq)` 중복 제거는 Redis(last_seq, TTL 24h)로, Event Log는 Kafka(`aiokafka`, 토픽 `keystroke-events`, 파티션 키=`sid`)로 구현되어 있다. Replay Worker는 별도 프로세스가 아니라 같은 백엔드 안에서 도는 Kafka consumer(`backend/app/worker/consumer.py`)로, `session.end` 수신 시 그 세션 이벤트를 모아 재생·분석한다. Raw blob 저장(§3.3)은 S3 대신 로컬 VM 디스크(`RAW_STORE_DIR`, 기본 `./data/raw`)에 zstd 압축 저장한다. 로컬 개발용 Redis/Kafka는 `backend/docker-compose.yml`로 기동.

| Method | Endpoint | 설명 | 방향 | 응답 |
|---|---|---|---|---|
| WS | `/ws/events?session_id={id}&token={access_token}` | 편집 로그 실시간 스트리밍 | Client→Server 5종 메시지 (아래) | Server→Client 3종 메시지 (아래) |
| POST | `/events/beacon` | WS 불가/탭 종료 시 폴백 전송 | `edit.batch`와 동일 payload | 204 (본문 없음) |

**Client → Server 메시지** (공통 필드: `type`, `sid`):

| type | 추가 필드 | 설명 |
|---|---|---|
| `session.start` | `problem_id`(integer), `lang`(string), `client_ts`(integer, Unix ms), `editor`(string, 예: `"monaco@0.45"`), `initial_code`(string) | 세션 시작. `client_ts`는 서버-클라 시계 드리프트 보정용 |
| `edit.batch` | `seq`(integer), `base_t`(integer, 배치 첫 이벤트 상대 ms), `ops`(array\<EditOp\>) | 주 트래픽. 1000ms 주기 또는 버퍼 200개/제출 직전/unload 시 강제 플러시 |
| `session.heartbeat` | `t`(integer), `cursor`(integer, 코드포인트 오프셋) | 5초 주기, 편집 없어도 전송. 90초간 미수신 시 서버가 `reason: "timeout"`으로 세션 강제 종료 |
| `submission.mark` | `t`(integer), `submission_id`(string) | 제출 시각 마킹. `AnalysisResult`의 phase 분리·`submission_ts_ms` 기준점 |
| `session.end` | `t`(integer), `reason`(string enum: `"submitted_ac"`\|`"closed"`\|`"timeout"`) | 세션 종료. 이 시점에 Replay Worker 큐 적재 트리거 |

**EditOp** (`ops` 배열 원소, `backend/app/schema/analysis.py`의 `EditOp`와 동일):
| 키 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| t | integer | O | `1200` | 세션 시작 기준 상대 ms |
| op | integer (0\|1) | O | `0` | `0`=insert, `1`=delete |
| pos | integer | O | `42` | 코드포인트 기준 절대 오프셋 |
| len | integer | X (기본 0) | `1` | delete 길이 |
| txt | string | X (기본 `""`) | `"int"` | insert 문자열. IME 조합 중간 상태는 기록하지 않고 `compositionend` 확정 문자열만 하나의 insert로 기록 |
| src | string (enum) | X (기본 `"user"`) | `"user"` | `"user"` \| `"autoindent"` \| `"autocomplete"`. 워커는 `src != "user"`를 pause 계산에서 제외 |

> undo/redo는 `op: 2`(별도 타입)로 기록 예정이나 **현재 Replay Worker 미구현**(dev-plan §11.1 결정사항: pivot 판정에서 제외). 프런트가 `op: 2`를 보내도 워커는 아직 무시한다 — 구현 전까지는 undo를 역방향 insert/delete 이벤트로 풀어서 보내는 임시 방편을 권장.

**Server → Client 메시지**:
| type | 필드 | 설명 |
|---|---|---|
| `ack` | `sid`, `seq`(integer) | 수신 확인, 최대 수신 seq |
| `resume` | `sid`, `last_seq`(integer) | 재연결 시 마지막 수신 seq 응답 |
| `error` | `sid`, `code`(string), `seq`(integer) | 예: `"SCHEMA_INVALID"` |

**멱등성 계약**: 서버는 `(sid, seq)`로 중복 제거. 클라이언트는 `ack` 미수신 배치를 동일 `seq`로 재전송해야 하며, seq를 재사용해 다른 내용을 보내면 안 됨.

---

## 제출 (Submissions) — Judge0 연동, 동기 채점

| Method | Endpoint | 설명 | 요청 (Body) | 응답 |
|---|---|---|---|---|
| POST | `/submissions` | 코드 제출 + 즉시 채점 | `{"session_id": "s_001", "problem_id": 1, "code": "print('hello')", "language": "python3"}` | 200 `{"submission_id": "sub_001", "status": "judged", "submitted_at": "2026-07-24T05:09:00Z"}` |
| GET | `/submissions/{submission_id}` | 제출 상태/결과 조회 | - | 200 `{"submission_id": "sub_001", "status": "judged", "verdict": "AC", "runtime_ms": 120, "memory_kb": 9600, "submitted_at": "2026-07-24T05:09:00Z"}` |
| GET | `/submissions?session_id={id}` | 세션의 제출 이력 조회 | - | 200 `{"items": [{"submission_id": "sub_001", "status": "judged", "verdict": "AC", "submitted_at": "2026-07-24T05:09:00Z"}]}` |

**POST /submissions** 요청:
| 키 | 타입 | 필수 | 예시 |
|---|---|---|---|
| session_id | string | O | `"s_001"` |
| problem_id | integer | O | `1` |
| code | string | O | `"print('hello')"` |
| language | string | O | `"python3"` |

응답 필드 (`GET /submissions/{id}` 기준):
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| submission_id | string | `"sub_001"` | |
| status | string (enum) | `"judged"` | `POST` 호출 시점에 동기로 채점을 끝내고 저장하므로 항상 `"judged"`로 응답 |
| verdict | string \| null | `"AC"` | `"AC"`\|`"WA"`\|`"TLE"`\|`"RE"`\|`"CE"` |
| runtime_ms | integer \| null | `120` | 테스트케이스 전체 실행 시간 합. AC일 때만 값이 있고, 그 외에는 `null` |
| memory_kb | integer \| null | `9600` | 테스트케이스 중 최대 메모리 사용량. AC일 때만 값이 있고, 그 외에는 `null` |
| submitted_at | string (ISO8601) | `"2026-07-24T05:09:00Z"` | |

채점 구현: `backend/app/judge` (Judge0 자체 호스팅 경유, `backend/docker-compose.yml`의 `judge0-*` 서비스). 테스트케이스는 `AtCoder_100/{problems.source}/io/testcases.csv`에서 idx 순으로 읽어 첫 실패에서 채점을 중단한다. `POST /submissions`에서 verdict가 `"AC"`면 세션을 자동으로 `solved`로 종료하고, 그 외에는 세션을 `active`로 유지한 채 제출 기록만 남긴다(`PATCH /sessions/{id}`로 별도 종료 가능). 언어 매핑은 `backend/app/judge/languages.py` 참고.

---
// keystroke-analysis-dev-plan.md와 같이 읽어야 이해하기 쉬움.
## 키스트로크 분석 (Analysis) — 확정

`keystroke-analysis-dev-plan.md` §4 Replay Worker 산출물 조회. **최종 형태(게이트웨이 도입 후)는 조회 전용**: `session.end` 수신 시 게이트웨이가 큐에 적재하고 Replay Worker가 비동기로 세션당 1회 처리·저장하면, 프런트는 결과만 폴링해서 가져온다.

| Method | Endpoint | 설명 | 응답 |
|---|---|---|---|
| GET | `/sessions/{session_id}/analysis` | 분석 결과 조회 | 200(완료) / 202(처리 중, 아래) / 404 |

202 응답(처리 중): `{"status": "processing"}` — 프런트는 수 초 간격으로 재조회(폴링) 권장.

**200 응답**:
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| session_id | string | `"s_001"` | |
| result.analysis_level | string (enum) | `"full"` | `"full"` \| `"timing_only"`(미지원 언어/파서 불가) \| `"degraded"`(이벤트 수 K<50 부족) |
| result.matcher_version | integer | `1` | 패턴 매처 버전. 버전업 시 과거 세션은 raw blob에서 재처리(백필) 대상(dev-plan §11.5) |
| result.total_ms / setup_ms / formation_ms / debug_ms / refine_ms | integer | `60000` | phase별 소요 ms |
| result.keystroke_count | integer | `842` | |
| result.pause_total_ms / pause_count | integer | `12000` / `4` | |
| result.pivot_count | integer | `2` | |
| result.code_bytes | integer | `530` | |
| result.final_code | string | `""` | 재생 재구성 코드 전문. **조회 API에서는 항상 `""`** — 코드 전문은 개인정보(로그인 정보 등)와 분리 보관 원칙상 파생 테이블에 저장하지 않음(M1 바이트 일치 검증은 내부 검증 도구가 raw blob 대상으로 별도 수행, 공개 API 계약 아님) |
| result.pauses[] | array\<object\> | 아래 참고 | |
| result.pauses[].event_index | integer | `-1` | 파생 테이블에는 원본 이벤트 미보관 — 항상 `-1` |
| result.pauses[].t_ms / duration_ms | integer | `18000` / `2200` | |
| result.pauses[].ast_label | string | `"BRANCH_CONDITION"` | `INTERFACE_DESIGN`\|`LOOP_BOUNDARY`\|`BRANCH_CONDITION`\|`INDEX_REASONING`\|`ALGORITHM_ENTRY`\|`SETUP`\|`SYNTAX_STRUGGLE` |
| result.pauses[].pattern | string | `"BFS"` | 귀속된 구조 패턴, 없으면 `""` |
| result.pauses[].phase | string | `"FORMATION"` | `"SETUP"`\|`"FORMATION"`\|`"DEBUG"`\|`"REFINE"` |
| result.pivots[] | array\<object\> | 아래 참고 | |
| result.pivots[].start_index / end_index / rewrite_horizon | integer | `-1` | 위와 동일 이유로 항상 `-1` |
| result.pivots[].t_ms / deleted_chars | integer | `30000` / `55` | |
| result.pivots[].pivot_type | string | `"APPROACH_SWITCH"` | `"APPROACH_SWITCH"`\|`"COMPLEXITY_FIX"`\|`"EDGE_CASE_FIX"`\|`"OTHER"` (`"TYPO"`는 노이즈로 제외되어 나타나지 않음) |
| result.pivots[].pattern | string | `"BFS"` | |
| result.pattern_windows[] | array\<object\> | 아래 참고 | 패턴별 형성 구간(dev-plan §4.1 Step 6) |
| result.pattern_windows[].pattern | string | `"BFS"` | |
| result.pattern_windows[].t_start_ms / t_complete_ms / formation_ms | integer | `8000` / `19500` / `11500` | |
| result.pattern_windows[].pause_ms_in_window / pivot_count_in_window | integer | `2200` / `1` | |
| result.patterns_detected | array\<string\> | `["BFS", "DP"]` | 지원 패턴: `BFS`,`DFS_RECURSIVE`,`DFS_ITERATIVE`,`BINARY_SEARCH`,`DP`,`GREEDY`,`DSU` (cpp/python만 지원, 그 외 언어는 항상 `[]`) |

에러: 404 `{"error": {"code": "SESSION_NOT_FOUND", "message": "세션을 찾을 수 없습니다."}}` / 403 `{"error": {"code": "FORBIDDEN", "message": "접근 권한이 없습니다."}}`

> **확장 예정**: `pattern`/`pivot_type` 값 목록은 팀A의 CodeNet 구조화 산출물과 무관한 독립 매처(dev-plan §4.1 Step 5~6) 결과이며, 매처 버전업 시 값 목록이 늘어날 수 있음(하위호환 추가). `tier`(A~G) 기반 기준선 대비 잔차 백분위(dev-plan §5)는 미구현이라 이 응답에 포함되지 않음 — 확정되는 대로 이 섹션에 필드 추가 예정.

**구현 완료**: Ingest Gateway가 실제로 붙어(`backend/app/api/ingest.py`) 위 계약대로 GET 조회 전용으로 동작한다. `POST /sessions/{session_id}/analysis`(동기 임시 경로)는 제거됨.

---

## 피드백 (Feedback) — 통로만 확정, 내용 미확정

| Method | Endpoint | 설명 | 요청 (Body) | 응답 |
|---|---|---|---|---|
| POST | `/feedback` | 세션에 대한 과정 기반 피드백 요청 | `{"session_id": "s_001"}` | 200 `{"feedback_id": "f_001", "text": "(mock) 아직 준비 중인 피드백입니다.", "model_used": "qwen2.5-coder:7b", "generated_at": "2026-07-24T05:11:00Z"}` |
| PATCH | `/feedback/{feedback_id}/rating` | 피드백 평가(👍👎) | `{"rating": "up"}` | 200 `{"feedback_id": "f_001", "rating": "up"}` |

**POST /feedback** 요청: `session_id`(string, 필수, 예: `"s_001"`)

응답 필드:
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| feedback_id | string | `"f_001"` | |
| text | string | `"(mock) 아직 준비 중인 피드백입니다."` | 팀A 프롬프트 설계 완료 전까지 mock 고정 문구 |
| model_used | string | `"qwen2.5-coder:7b"` | |
| generated_at | string (ISO8601) | `"2026-07-24T05:11:00Z"` | |

> 필드 구조(`feedback_id`, `text`, `model_used`, `generated_at`)는 유지한 채 `text`의 실제 내용/품질만 팀A 산출물 확정 후 달라지도록 설계 — 프론트엔드 파싱 로직 변경 최소화 목적

**PATCH /feedback/{feedback_id}/rating** 요청: `rating`(string enum, 필수, `"up"` \| `"down"`)

---

## 비교 통계 (Stats)

| Method | Endpoint | 설명 | 상태 |
|---|---|---|---|
| GET | `/problems/{problem_id}/stats` | 다른 응시자 대비 통계 (AtCoder 비교군 기준선) | 구현됨 |

**GET /problems/{problem_id}/stats** 응답 (`app/schema/baseline.py` `ProblemBaselineResponse`):

| 키 | 타입 | 설명 |
|---|---|---|
| problem_id | int | 앱 문제 id |
| source_problem_id | string \| null | AtCoder 원본 id (예: `"p02540"`), 비교군 없으면 null |
| tier | string \| null | 난이도 tier `A`~`G` |
| cluster_id | int \| null | 문제 클러스터. `-1`은 tier 단독 fallback |
| metrics | array | 아래 BaselineMetric 목록. **비어 있으면 "비교 불가" 처리** |

BaselineMetric: `metric`(`total_duration`(초)/`attempt_count`/`pivot_count`/`pause_ms@LABEL`), `percentiles`(`p10`~`p90`), `n_real`, `n_synthetic`, `data_source`(`observed` \| `estimated` — `n_real < 30`이면 `estimated`, 리포트에 "추정치 기반" 명시), `user_value`/`user_band`(세션 문맥 있을 때만, stats 엔드포인트에서는 null).

> 데이터 출처: `backend/app.db`의 `baseline_cell` (AtCoder_100 실측 + IRT 합성 비교군, `app-db-ingestion-spec.md` §4).
