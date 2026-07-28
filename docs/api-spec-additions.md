# API 명세 v1 추가 요청 (프론트엔드 → 백엔드)

`docs/api-spec.md`를 대체하지 않는 별도 문서입니다. 마이페이지/문제 목록 고도화를 위해 프론트엔드가 필요로 하는 확장 사항을 정리했습니다 — 백엔드 담당자 검토 후 반영되면 `docs/api-spec.md` 본문에 옮겨주세요. 스타일은 `docs/api-spec.md`를 따릅니다.

배경: 지금은 (1) 유저가 특정 문제를 풀어본 적 있는지 서버에 물어볼 방법이 없고, (2) 문제 난이도 컬럼이 없고, (3) 제출한 코드 원문이 어느 응답에도 안 내려오고, (4) 생성된 피드백을 재조회할 방법이 없습니다. 아래 4가지 요청은 새 엔드포인트를 최소화하기 위해 가능한 한 **기존 엔드포인트를 확장**하는 쪽으로 설계했습니다.

---

## 요청 1 — `GET /problems`, `GET /problems/{problem_id}` 확장 (난이도) (완료)

`problems` 테이블에 `difficulty` 컬럼 추가: `NULL | "A"|"B"|"C"|"D"|"E"|"F"|"G"` — `keystroke-analysis-dev-plan.md` §5.1에 이미 정의된 7단계 티어(`Enum8('A'..'G')`, 피어 비교 기준선용)를 그대로 재사용합니다. **A = 가장 쉬움, G = 가장 어려움.**

**GET /problems** 요청 쿼리 추가분:
| 키 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| sort | string (enum) | X (기본 `"difficulty_asc"`) | `"difficulty_asc"` | `"difficulty_asc"` \| `"difficulty_desc"` \| `"problem_id"`(기존 기본 정렬) |
| difficulty | string (comma-separated) | X | `"A,B,C"` | 다중 선택 필터, 미지정 시 전체 |
| exclude_solved | boolean | X (기본 `false`) | `true` | `true`면 `Authorization` 헤더의 현재 유저가 이미 AC 받은 문제를 결과에서 제외. 비인증 요청이면 무시(전체 반환) |

**GET /problems** 응답 아이템 필드 추가분:
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| items[].difficulty | string \| null | `"C"` | 미배정이면 `null` |
| items[].solved_at | string(ISO8601) \| null | `"2026-07-20T10:00:00Z"` | 인증된 요청이고 현재 유저가 이 문제를 AC 받은 적 있으면 가장 최근 AC 제출 시각, 아니면 `null`. 비인증 요청이면 항상 `null` |

**GET /problems/{problem_id}** 응답에도 `difficulty` 필드 동일하게 추가.

> `page`/`page_size` 페이지네이션 계약은 그대로 유지 — 정렬/필터/제외가 전부 서버에서 적용된 뒤 페이지네이션되어야 프론트 쪽에서 필터링과 페이지 번호가 어긋나지 않습니다.

---

## 요청 2 — `GET /users/me/sessions` (신규, 유저 기준 세션 목록) (완료)

인증 필요(`Authorization: Bearer {access_token}`), 토큰에서 유저를 추론합니다.

| Method | Endpoint | 설명 | 요청 (Query) | 응답 |
|---|---|---|---|---|
| GET | `/users/me/sessions` | 현재 유저의 세션 목록(문제 전체 대상) | `?problem_id=1&status=active&page=1&page_size=20` | 200, 아래 참고 |

요청 쿼리:
| 키 | 타입 | 필수 | 예시 | 설명 |
|---|---|---|---|---|
| problem_id | integer | X | `1` | 특정 문제로 좁히기 |
| status | string (enum) | X | `"active"` | `"active"`\|`"solved"`\|`"abandoned"` |
| page | integer | X (기본 1) | `1` | |
| page_size | integer | X (기본 20) | `20` | |

응답 필드:
| 키 | 타입 | 예시 | 설명 |
|---|---|---|---|
| items[].session_id | string | `"s_001"` | |
| items[].problem_id | integer | `1` | |
| items[].problem_title | string | `"Welcome to AtCoder"` | |
| items[].difficulty | string \| null | `"C"` | |
| items[].language | string \| null | `"python3"` | |
| items[].status | string (enum) | `"solved"` | `"active"`\|`"solved"`\|`"abandoned"` |
| items[].started_at | string (ISO8601) | `"2026-07-24T05:00:00Z"` | |
| items[].ended_at | string \| null | `"2026-07-24T05:20:00Z"` | |
| items[].latest_verdict | string \| null | `"AC"` | 해당 세션의 `judge_submissions` 중 가장 최근 것의 verdict |
| items[].latest_submitted_at | string \| null | `"2026-07-24T05:19:00Z"` | 위 제출의 `submitted_at` |
| page / page_size / total_count | integer | `1` / `20` / `37` | |

정렬 기본값: `started_at desc`.

> 용도: (a) 문제 목록 화면 최상단 "풀이 중인 문제" 섹션(`status=active`로 조회), (b) 마이페이지 활동 요약(전체 조회 후 프론트에서 집계 — 별도 통계 엔드포인트는 요청하지 않습니다), (c) 마이페이지 풀이 기록 로그(문제별로 그룹핑). 카탈로그가 AtCoder_100(약 100문제) 규모라 서버 집계 없이 클라이언트 reduce로 충분하다고 판단했습니다.

---

## 요청 3 — `GET /submissions/{submission_id}` 응답에 `code`와 'lang' 추가

`judge_submissions.code`는 이미 DB에 저장되어 있으나(`backend/app/model/submission.py`) 어떤 응답 스키마에도 포함되어 있지 않습니다. "과거 제출 답안 보기" 화면에 필요하니 `SubmissionDetailResponse`에 `code: string`과 `lang: string` 필드를 추가해 그대로 내려주세요.

---

## 요청 4 — `GET /feedback?session_id=X` (신규, 피드백 재조회)

| Method | Endpoint | 설명 | 응답 |
|---|---|---|---|
| GET | `/feedback?session_id={id}` | 해당 세션에 이미 생성된 피드백 조회(재생성 아님) | 200 `{"feedback_id": "f_001", "text": "...", "model_used": "qwen3-coder:30b-a3b", "generated_at": "..."}` 또는 아직 없으면 200 `null` |

`POST /feedback`과 달리 **새로 생성하지 않습니다** — 기존 row가 있으면 그대로, 없으면 `null`을 반환합니다.

> 비고: 현재 `POST /feedback`이 세션당 중복 생성을 막지 않아(매번 새 row 삽입) 한 세션에 여러 피드백이 쌓일 수 있습니다. 이 엔드포인트는 그중 **가장 최근 1개**만 반환하는 정책으로 제안합니다. 중복 생성 자체를 막을지는 담당자 판단에 맡깁니다.

---

## 정리 — 영향받는 테이블/컬럼

- `problems`: `difficulty` 컬럼 추가(요청 1)
- `judge_submissions`: 컬럼 추가 없음, 응답 스키마만 확장(요청 3)
- 신규 쿼리 로직: `GET /problems`의 유저별 AC 여부 조인(요청 1의 `exclude_solved`/`solved_at`), `GET /users/me/sessions`의 세션×최신 제출 조인(요청 2)
- 신규 엔드포인트는 요청 2, 4 두 개뿐입니다.
