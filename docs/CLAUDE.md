# 프로젝트 컨텍스트 — CodeTest Feedback Platform

이 문서는 Claude Code가 이 저장소에서 작업할 때 참고하는 컨텍스트입니다. 작업 전 반드시 읽고, 특히 "지금 진행 가능한 작업"과 "Claude Code 작업 시 유의사항"을 확인하세요.

## 프로젝트 개요

IBM Project CodeNet 데이터셋에서 **풀이 기록(제출 이력)이 확보된 AtCoder 문제만** 골라 출제하는 자체 코딩테스트 연습 플랫폼입니다.

핵심 가치: 사용자가 문제를 푸는 동안의 행동(편집 패턴, 재시도 간격, 정지 시간, 붙여넣기 여부 등)을 실시간으로 수집하고, 동일 문제를 푼 다른 응시자(CodeNet 데이터 기반) 대비 통계와, 로컬 LLM이 생성하는 "결과가 아닌 과정" 기반 피드백을 제공합니다.

## 팀 구성과 현재 우선순위 (2026-07-24 갱신)

- **팀원A**: 백엔드 담당
- **팀원B(이 저장소 작업자)**: 프론트엔드 담당

**변경 사항**: CodeNet 데이터 다운로드가 예상보다 오래 걸려서, 원래 A가 전담하려던 "CodeNet 전처리 → Claude로 풀이 과정/오류 수정 패턴 구조화 → Qwen 프롬프트 설계"는 보류 TODO로 내렸습니다. 데이터 다운로드가 끝나는 시점에 팀이 분업합니다(누가 무엇을 맡을지는 그때 결정 — 지금 확정하지 않음).

**2026-07-27 갱신**: 채점 엔진(항목 3)은 팀 분업 대기와 무관하게, AtCoder_100 testcase.csv 기반 채점이 필요해져 **먼저 착수·구현 완료**했습니다(자체 호스팅 Judge0 경유, 아래 "중요 미해결 항목" 1번 참고). 나머지 두 항목(전처리→프롬프트 설계)은 여전히 보류입니다.

**보류 TODO (데이터 다운로드 완료 후 착수, 분업 미정)**
1. CodeNet 전처리 → Claude(강한 모델)로 풀이 과정/오류 수정 패턴 구조화
2. 위 구조화 결과 기반 Qwen 프롬프트 설계

그 전까지 A(백엔드)/B(프론트엔드)는 아래 "지금 진행 가능한 작업"만 각자 진행합니다. **회원가입 같은 기능이 대표적인 예시입니다 — "풀이 과정 구조화"나 "사용자 데이터 수집 항목"이 뭐가 되든 전혀 영향받지 않는 영역이기 때문입니다.**

## 현재 상태 (2026-07-24 기준)

| 항목 | 상태 |
|---|---|
| 문제 소스: CodeNet의 AtCoder 문제, 풀이 기록 있는 것만 선별 | 확정 (다운로드 진행 중, 완료 시점 미정) |
| 코드 에디터: Monaco Editor (자체 통합) | 확정 |
| 실시간 로그 전송 계층 (백엔드 Ingest Gateway `backend/app/api/ingest.py`) | 구현 완료. EditOp 스키마 확정(`backend/app/schema/analysis.py`) |
| 실시간 로그 전송 계층 (프론트엔드 `activity-logger.ts`, `useMonacoActivityLogger.ts`) | 2026-07-28 기준 확정된 EditOp 프로토콜에 맞춰 재구현(이전에는 자유 payload를 보내던 구버전) |
| 팀 역할 분담 | A=백엔드, B=프론트엔드로 확정 |
| CodeNet 전처리 → 프롬프트 설계 | **보류** — 데이터 다운로드 완료 후 착수 |
| 채점 엔진 구현 (Judge0 경유) | 구현 완료 (`backend/app/judge`, `backend/docker-compose.yml`의 `judge0-*`) |
| 키스트로크 분석 결과 조회 (`GET /sessions/{id}/analysis`) | 백엔드 구현 완료. 프론트엔드 조회 UI 2026-07-28 신규 구현 |
| 수집 항목의 정확한 필드/이벤트 목록 | **확정** — `docs/api-spec.md` "실시간 이벤트 수집" 절 참고 |
| 백엔드-프론트엔드 API 명세, DB 스키마 (현재 확정 가능한 범위) | `docs/api-spec.md`, `docs/db-schema.md` 참고 — 확장 가능하게 설계됨 |

## 중요 미해결 항목 (팀 논의 필요, Claude Code가 임의로 확정하면 안 됨)

1. **코드 실행/채점 엔진**: **구현 완료.** 사용자가 제출한 코드를 Judge0(자체 호스팅, `backend/docker-compose.yml`의 `judge0-server`/`judge0-workers`, 이미지 `judge0/judge0:1.13.1`)로 실행해 `AtCoder_100/{problems.source}/io/testcases.csv`와 비교 채점합니다(`backend/app/judge`, `POST /submissions`). 백엔드는 여전히 사용자 코드를 직접 eval/exec 하지 않고 전부 Judge0 경유. **미확인 채로 남은 것**: Judge0의 과거 샌드박스 이스케이프 취약점(CVE-2024-29021 등) 대응 — 배포 전 실제 운용 버전 패치 여부와 네트워크 격리 설정을 팀이 별도로 점검해야 함.
2. **CodeNet 데이터셋 라이선스**: 문제 지문을 CodeNet에서 그대로 가져와 서비스에 노출해도 되는지(AtCoder 원 저작권 vs CodeNet 재배포 라이선스 조건)를 법무 검토 전까지 확정하지 않습니다.

## 기술 스택

| 영역 | 선택 | 비고 |
|---|---|---|
| 코드 에디터 | Monaco Editor | 공식 API 사용, 리버스엔지니어링 불필요 |
| 실시간 로그 전송 | WebSocket(`/ws/events`) + sendBeacon 폴백(`/events/beacon`) | `frontend/src/lib/activity-logger.ts`. EditOp 스키마 확정, 더 이상 자유 payload 아님 |
| 코드 실행/채점 | Judge0 (자체 호스팅, 구현 완료) | 위 "중요 미해결 항목" 참고. `backend/app/judge`, `JUDGE0_URL` |
| LLM 서빙 | vLLM (OpenAI 호환 `/v1/chat/completions`) | **주의: "로컬 LLM"이 아니라 별도 클라우드 호스트로 확인됨** (`backend/app/llm/client.py` 코드 주석: "LLM 서버는 별도 클라우드망 호스트"). 기존 계획(Ollama + 로컬 서빙)에서 실제로 바뀐 것인지 팀 확인 필요. 모델: `qwen3-coder:30b-a3b`(`LLM_MODEL` 환경변수). 프롬프트 내용은 여전히 고정 템플릿(팀A 산출물 반영 전) |
| 백엔드 프레임워크 | FastAPI | `backend/app` |
| DB | SQLite(로컬 개발, `DATABASE_URL`) / PostgreSQL(운영 가정) | 스키마는 `docs/db-schema.md` 참고 |
| Ingest Gateway 중복 제거 | Redis (`redis.asyncio`) | `(sid, seq)` last_seq, TTL 24h. `backend/app/util/messaging.py` |
| Ingest Gateway 이벤트 로그 | Kafka (`aiokafka`), 토픽 `keystroke-events`, 파티션 키=`sid` | Replay Worker는 별도 서비스가 아니라 백엔드 프로세스 내 Kafka consumer(`backend/app/worker/consumer.py`)로 구현. 로컬 개발용 Redis/Kafka는 `backend/docker-compose.yml` |
| Raw 이벤트 blob 저장 | 로컬 디스크 (`RAW_STORE_DIR`, zstd 압축) | S3 대신 로컬 VM 서버 운용 전제로 대체(dev-plan §3.3) |

## 디렉토리 구조 (제안)

```
/frontend
  /src
    /components/Editor        # Monaco 래핑 컴포넌트
    /components/Auth          # 회원가입/로그인
    /components/ProblemCatalog
    /components/Feedback      # mock 응답 표시 UI
    /lib/activity-logger.ts   # 구현 완료
    /lib/api-client.ts        # IApiClient 추상화 (mock ↔ 실제 백엔드 교체용)
    /hooks/useMonacoActivityLogger.ts  # 구현 완료
    /pages (or /routes)
/backend
  /src
    /api        # REST/WebSocket 핸들러
    /judge      # Judge0 클라이언트 래퍼 (구현 완료 — 실제 경로는 backend/app/judge)
    /llm        # Ollama/Qwen 클라이언트 래퍼 (mock 프롬프트로 통신 레이어만 우선 구현)
    /db         # 스키마/마이그레이션 — docs/db-schema.md 기준
/docs
  api-spec.md   # 현재 확정 가능한 범위의 API 명세
  db-schema.md  # 현재 확정 가능한 범위의 DB 스키마
/infra
  docker-compose.yml  # Postgres, backend, frontend (Judge0/Ollama는 착수 시점에 추가)
```

## 지금 진행 가능한 작업

### 백엔드(A) — 상세

1. **프로젝트 셋업 & 인프라**
   - 백엔드 프레임워크 선정(미확정 항목 — 결정 즉시 이 문서 갱신) 및 초기 세팅
   - PostgreSQL 연결, 마이그레이션 도구 세팅
   - Docker Compose로 로컬 개발 환경 구성 (postgres + backend)
   - 공통 로깅/에러 핸들링 미들웨어, 환경변수(.env) 관리
2. **인증 API** — `docs/api-spec.md`의 `/auth/*`, `/users/me` 구현. 비밀번호 해싱(bcrypt/argon2), JWT 발급/검증
3. **문제 카탈로그 API** — `/problems`, `/problems/:id` 구현. CodeNet 전체 다운로드를 기다리지 않고, 먼저 받아진 일부 표본 문제로 `problems` 테이블 시딩 스크립트를 미리 검증
4. **세션 라이프사이클 API** — `/sessions` 생성/종료. 이벤트 스키마와 무관하게 세션 시작~종료 시각과 상태만 다룸
5. **이벤트 수집 백엔드 (Ingest Gateway)** — **구현 완료, 설계 변경됨**: 당초 계획한 "제너릭 인제스천"(자유 `event_type`/`payload`를 검증 없이 `events` 테이블에 적재)이 아니라, EditOp 스키마를 확정해 WebSocket(`/ws/events`)에서 스키마 검증 + `(sid, seq)` 중복 제거(Redis) + Kafka 적재까지 수행하도록 구현됨(`backend/app/api/ingest.py`). sendBeacon 폴백용 `POST /events/beacon`도 동일 스키마로 구현됨. AST 파싱 등 무거운 연산은 별도 Replay Worker(Kafka consumer, `backend/app/worker/consumer.py`)가 `session.end` 수신 시 비동기 처리
6. **제출(Submission) API** — `/submissions` 생성/조회. **구현 완료**: Judge0 경유로 동기 채점하며, AC면 세션을 자동 종료, 그 외에는 세션을 유지한 채 제출 기록만 남김(`backend/app/api/submission.py`)
7. **LLM 통신 레이어 뼈대** — `/feedback` 접수 → Ollama에 mock 프롬프트를 보내고 응답을 그대로 반환. 실제 프롬프트는 보류 TODO 완료 후 교체
8. **테스트/문서화** — 엔드포인트별 기본 통합 테스트, OpenAPI 스펙 자동 생성 세팅

### 프론트엔드(B) — 상세

1. **프로젝트 셋업** — React + Vite 스캐폴딩, 라우팅, 상태관리(API 미확정 구간은 mock으로 개발), 디자인 시스템 선정
2. **인증 화면** — 회원가입/로그인 폼 + 유효성 검사, 인증 상태 전역 관리, 보호된 라우트
3. **문제 카탈로그 화면** — 목록(페이지네이션) + 상세(지문에 수식이 포함될 수 있어 KaTeX 등 렌더러 고려)
4. **에디터 화면** — Monaco 통합(언어 선택, 테마) + `activity-logger.ts`/`useMonacoActivityLogger.ts` 연결. **구현 완료**: 실행/제출은 이제 mock이 아니라 실제 Judge0 동기 채점 결과(verdict/runtime/memory)를 받아 표시
5. **피드백 UI** — "피드백 보기" 버튼 → 응답 표시 패널 + 👍👎 평가 UI. 백엔드가 실제 LLM(vLLM)을 호출하지만 프롬프트 내용은 아직 고정 템플릿(팀A 산출물 반영 전)이므로, 화면 쪽은 텍스트를 그대로 렌더링하는 현재 구조 유지
6. **키스트로크 분석 결과 UI** — `GET /sessions/{session_id}/analysis` 폴링(202→200) 후 phase별 소요시간/pause/pivot/패턴 표시. 세션 종료(`session.end`) 이후에만 데이터가 채워짐
7. **비교 통계 UI (플레이스홀더)** — "다른 응시자 대비" 통계 위젯은 목업 데이터로 레이아웃만 우선 구현 (`GET /problems/{id}/stats`는 백엔드에 자리표시자 메시지만 반환하도록 구현되어 있어, 실제 연동은 여전히 보류 TODO 완료 후)
8. **공통** — API 클라이언트를 `IApiClient` 인터페이스로 추상화해서 mock 구현체 ↔ 실제 백엔드 구현체를 나중에 그대로 교체 가능하게 설계

## 코딩 컨벤션

- 프론트엔드: TypeScript + React, 함수형 컴포넌트/훅 사용
- 백엔드: 언어/프레임워크 TBD, 결정되는 대로 이 문서 갱신
- 커밋 메시지: 무엇을/왜 변경했는지 한국어 또는 영어로 간결하게

## Claude Code에게: 작업 시 유의사항

- "보류 TODO" 2가지(전처리→프롬프트 설계)는 데이터 다운로드 완료 전까지 착수하지 않습니다. `llm` 클라이언트는 인터페이스만 정의하고 실제 로직은 mock으로만 채웁니다. (채점 엔진은 2026-07-27에 팀 지시로 먼저 구현 완료 — 더 이상 보류 아님.)
- API 명세와 DB 스키마는 `docs/api-spec.md`, `docs/db-schema.md`에 명시된 "확정 범위"만 구현하고, "확장 예정" 표시가 된 부분은 임의로 필드를 채워 넣지 않습니다.
- 사용자 코드 실행은 반드시 Judge0(또는 동급 샌드박스) 경유. 백엔드에서 어떤 형태로든 사용자 코드를 직접 eval/exec 하지 않습니다(구현: `backend/app/judge`).
- 수집 로그에는 사용자가 작성한 코드 전문이 포함될 수 있으므로, 저장/전송 시 로그인 정보(이메일 등 개인정보)와 분리 보관.
- 새로운 기술 선택(프레임워크, 라이브러리, 인프라)은 이 문서의 "미확정" 항목에 걸쳐 있다면 임의로 확정하지 말고, 이 문서에 "검토 후보"로 남긴 뒤 팀 확인을 받습니다.
