# API 명세 v1 (현재 확정 가능한 범위)

Base URL: `/api/v1`
버전 전략: 확장 시 `/api/v2`로 분리. 기존 필드는 유지한 채 추가만 하는 방식으로 하위호환 지향.
공통 에러 포맷: `{"error": {"code": "...", "message": "..."}}`
날짜 포맷: ISO 8601 UTC 문자열 (예: `"2026-07-24T05:00:00Z"`)
ID 표기: 아래 예시의 `"1"`, `"p_001"` 등은 가독성을 위한 단순화 값이며, 실제 값은 `db-schema.md`에 정의된 UUID 문자열입니다.

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
| POST | `/sessions` | 문제 풀이 세션 시작 | `{"problem_id": 1, "language": "python3"}` | 201 `{"session_id": "s_001", "user_id": "1", "problem_id": 1, "language": "python3", "started_at": "2026-07-24T05:00:00Z", "status": "active"}` |
| PATCH | `/sessions/{session_id}` | 세션 종료 처리 | `{"status": "solved", "ended_at": "2026-07-24T05:10:00Z"}` | 200 `{"session_id": "s_001", "status": "solved", "ended_at": "2026-07-24T05:10:00Z"}` |
| GET | `/sessions/{session_id}` | 세션 상세 조회 | - | 200 `{"session_id": "s_001", "user_id": "1", "problem_id": 1, "language": "python3", "started_at": "2026-07-24T05:00:00Z", "ended_at": null, "status": "active"}` |

**POST /sessions** 요청:
| 키 | 타입 | 필수 | 예시 |
|---|---|---|---|
| problem_id | integer | O | `1` |
| language | string | O | `"python3"` (`"cpp17"`, `"java17"` 등 — 지원 언어 목록은 채점 엔진 착수 시 확정) |

**PATCH /sessions/{session_id}** 요청:
| 키 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| status | string (enum) | O | `"solved"` | `"solved"` \| `"unsolved"` \| `"abandoned"` |
| ended_at | string (ISO8601) | X | `"2026-07-24T05:10:00Z"` | 생략 시 서버 시각 사용 |

응답 공통 필드(세 엔드포인트): `session_id`(string), `user_id`(string), `problem_id`(integer), `language`(string), `started_at`(string, ISO8601), `ended_at`(string\|null), `status`(string enum: `"active"`\|`"solved"`\|`"unsolved"`\|`"abandoned"`)

---

## 실시간 이벤트 수집 (Events) — 제너릭 통로만 확정, 세부 스키마는 미확정

| Method | Endpoint | 설명 | 요청 (Body) | 응답 |
|---|---|---|---|---|
| WS | `/ws/events?session_id={id}&token={access_token}` | 실시간 행동 로그 스트리밍 | `{"session_id": "s_001", "events": [{"type": "edit_snapshot", "payload": {"char_count": 42}, "ts": 1753333200000}]}` | 없음 (ack 미사용) |
| POST | `/events/beacon` | WS 불가/탭 종료 시 폴백 전송 | 위와 동일 | 204 (본문 없음) |

요청 필드:
| 키 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| session_id | string | O | `"s_001"` | |
| events | array\<object\> | O | 아래 참고 | |
| events[].type | string | O | `"edit_snapshot"` | 자유 문자열, 추후 enum으로 제한 예정 |
| events[].payload | object | O | `{"char_count": 42}` | 자유 JSON, 팀A 산출물 확정 후 구조 확정 |
| events[].ts | integer (Unix ms) | O | `1753333200000` | |

> **확장 예정**: `type` 값 목록과 `payload`의 구체 스키마는 팀A의 "풀이 과정 구조화" 산출물 확정 후 정의됩니다. 현재는 서버가 값을 검증하지 않고 그대로 저장만 합니다 (`db-schema.md`의 `events` 테이블 참고).

---

## 제출 (Submissions) — 접수 인터페이스만 확정, 채점 로직 보류

| Method | Endpoint | 설명 | 요청 (Body) | 응답 |
|---|---|---|---|---|
| POST | `/submissions` | 코드 제출 접수 | `{"session_id": "s_001", "problem_id": 1, "code": "print('hello')", "language": "python3"}` | 202 `{"submission_id": "sub_001", "status": "pending", "submitted_at": "2026-07-24T05:09:00Z"}` |
| GET | `/submissions/{submission_id}` | 제출 상태/결과 조회 | - | 200 `{"submission_id": "sub_001", "status": "pending", "verdict": null, "runtime_ms": null, "memory_kb": null, "submitted_at": "2026-07-24T05:09:00Z"}` |
| GET | `/submissions?session_id={id}` | 세션의 제출 이력 조회 | - | 200 `{"items": [{"submission_id": "sub_001", "status": "pending", "verdict": null, "submitted_at": "2026-07-24T05:09:00Z"}]}` |

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
| status | string (enum) | `"pending"` | `"pending"` \| `"judged"` (채점 엔진 착수 후 세분화) |
| verdict | string \| null | `null` | 채점 엔진 착수 전까지 항상 null. 이후 `"AC"`\|`"WA"`\|`"TLE"`\|`"RE"`\|`"CE"` |
| runtime_ms | integer \| null | `null` | |
| memory_kb | integer \| null | `null` | |
| submitted_at | string (ISO8601) | `"2026-07-24T05:09:00Z"` | |

> 현재는 접수 즉시 mock으로 `status: "pending"`을 고정 반환합니다.

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

## 비교 통계 (Stats) — 확정 불가, 자리표시자만

| Method | Endpoint | 설명 | 상태 |
|---|---|---|---|
| GET | `/problems/{problem_id}/stats` | 다른 응시자 대비 통계 | 미구현 (엔드포인트만 예약) |

> CodeNet 기반 "다른 응시자 대비 통계"는 팀A의 구조화 산출물에 의존하므로 현재 필드를 확정하지 않습니다. 프론트엔드는 이 엔드포인트를 mock으로 대체해 UI 레이아웃만 먼저 구현하세요.
