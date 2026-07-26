# 키스트로크 기반 PS 학습 분석 시스템 개발 계획서

> **문서 목적**: 프런트엔드와 백엔드가 동일한 데이터 모델·프로토콜·책임 경계를 공유하기 위한 설계 문서.
> **핵심 원칙**: 분석 파이프라인 전 구간은 **결정론적(deterministic)**. LLM은 최종 리포트를 자연어로 렌더링하는 단계에서만 호출한다.

---

## 0. 목표 및 비목표

### 0.1 목표

사용자가 PS 문제를 푸는 동안 발생한 키스트로크 로그를 수집하여, 다음 세 가지 질문에 정량적으로 답한다.

| 질문 | 산출 지표 |
|---|---|
| 어디서 막히는가? | pause 이벤트의 AST 컨텍스트 분포 |
| 어떤 알고리즘 구현에 오래 걸리는가? | 구조 패턴별 형성 구간(pattern formation window) 소요 시간 |
| 남들보다 어디가 약한가? | 난이도 티어(A~G) × 문제 클러스터 기준선 대비 잔차 백분위 |

### 0.2 비목표 (이번 범위 아님)

- 붙여넣기/스니펫 탐지 — AtCoder 규정상 스니펫 사용이 금지되므로 제외
- 실시간 코칭/힌트 제공 — 분석은 세션 종료 후 비동기 처리
- LLM 기반 코드 이해·분류 — 명시적으로 배제 (§7 참조)

### 0.3 규모 가정 (설계 근거)

| 항목 | 값 |
|---|---|
| 등록 유저 | 5×10⁵ |
| 문제 수 | 5×10⁴ |
| DAU | 5×10⁴ |
| 피크 동시 코딩 세션 | 1×10⁴ |
| 세션당 키 이벤트 | 평균 2,000 (P95 ≈ 6,000) |
| 세션당 최종 코드 길이 | 2~6 KB |
| 연간 신규 세션 | ≈ 2.7×10⁷ |

**용어**: *세션(session)* = `(user_id, problem_id, attempt_round)` 단위의 한 번의 연속 코딩 활동. 브라우저 탭 하나에 대응.

---

## 1. 시스템 아키텍처

```
┌─────────────┐
│  Frontend   │  에디터 + 이벤트 수집기
│  (Browser)  │
└──────┬──────┘
       │ WebSocket (batched, seq-numbered)
       ▼
┌─────────────────────────────────────────────────┐
│  Ingest Gateway (stateless, 수평 확장)            │
│  - 스키마 검증 / seq 중복 제거 / 인증             │
│  - 무거운 연산 금지 (AST 파싱 없음)               │
└──────┬──────────────────────────────────────────┘
       │ append
       ▼
┌─────────────────────┐      ┌──────────────────────┐
│  Event Log (Kafka)  │─────▶│  Raw Blob Store (S3) │
│  파티션 키: session_id│      │  세션 단위 압축 저장   │
└──────┬──────────────┘      └──────────────────────┘
       │ session.end 트리거
       ▼
┌─────────────────────────────────────────────────┐
│  Replay Worker (비동기, 수평 확장, 멱등)          │
│  1) 이벤트 재생 → 증분 AST 유지                   │
│  2) pause 탐지 + AST 컨텍스트 라벨링              │
│  3) 삭제 버스트 탐지 + AST diff 분류              │
│  4) 구조 패턴 매칭 (디바운스)                     │
│  5) phase 세그멘테이션                           │
└──────┬──────────────────────────────────────────┘
       │ derived rows
       ▼
┌─────────────────────┐      ┌──────────────────────┐
│  Feature Store      │─────▶│  Baseline Store      │
│  (ClickHouse 등)     │      │  (t-digest 스케치)    │
│  세션별 파생 피처     │      │  tier×cluster 기준선  │
└──────┬──────────────┘      └──────────┬───────────┘
       │                                │
       └────────────┬───────────────────┘
                    ▼
       ┌──────────────────────────┐
       │  Report Builder          │  결정론적 JSON 생성
       └────────────┬─────────────┘
                    ▼
       ┌──────────────────────────┐
       │  LLM Renderer (외부 API)  │  ← LLM은 여기서만
       │  + grounding 검증기       │
       └────────────┬─────────────┘
                    ▼
              Frontend (리포트 UI)
```

**핵심 설계 결정**: 실시간 경로(WebSocket → Ingest)에서는 **저장 외에 아무 분석도 하지 않는다.** AST 파싱·패턴 매칭은 전부 세션 종료 후 비동기 워커에서 수행한다. 이유는 §6.1 참조.

---

## 2. 프런트엔드 명세

### 2.1 수집 대상 이벤트

에디터 버퍼에 대한 **최소 편집 연산**만 기록한다. keydown 원본이 아니라 **버퍼 변경(document change)** 단위다. (CodeMirror `transaction`, Monaco `onDidChangeModelContent` 등에서 획득)

```ts
type EditOp = {
  t:   number;   // 세션 시작 기준 상대 시각 (ms)
  op:  0 | 1;    // 0 = insert, 1 = delete
  pos: number;   // UTF-16 코드유닛 기준 절대 오프셋
  len: number;   // delete일 때 삭제 길이
  txt?: string;  // insert일 때 삽입 문자열
};
```

**주의사항 (프런트 필수 준수)**

1. `t`는 절대 시각이 아닌 **세션 시작으로부터의 상대 ms**. 클라이언트 시계 오차 영향을 줄이기 위함.
2. IME(한글/일본어) 조합 중간 상태는 기록하지 않는다. **조합 확정(compositionend) 시점의 최종 문자열만** 하나의 insert로 기록.
3. 자동완성/자동 들여쓰기로 인한 편집도 그대로 기록하되, `src` 필드로 구분:
   `src: "user" | "autoindent" | "autocomplete"`. 워커는 `src != "user"`인 이벤트를 pause 계산에서 제외한다.
4. **커서 이동만 하는 동작은 기록하지 않는다.** 단, pause 시점의 AST 컨텍스트 판정에 커서 위치가 필요하므로, `session.heartbeat`에 현재 커서 오프셋을 포함시킨다(§2.3).

### 2.2 버퍼링 및 전송 정책

| 항목 | 값 | 근거 |
|---|---|---|
| 플러시 주기 | 1,000 ms | 피크 1만 세션 × 1 msg/s = 10k msg/s, 게이트웨이 감당 가능 |
| 강제 플러시 조건 | 버퍼 ≥ 200 이벤트 또는 제출 직전 또는 페이지 unload | 데이터 손실 최소화 |
| 인코딩 | `t`는 직전 이벤트 대비 델타, 정수는 varint, 전체 배열은 CBOR 또는 msgpack | 이벤트당 평균 8 B 이하 목표 |
| 압축 | WebSocket `permessage-deflate` 활성화 | 추가 30~50 % 절감 |

**로컬 버퍼 정책**: 네트워크 단절 시 IndexedDB에 미전송 배치를 보관하고, 재연결 시 `seq`부터 재전송한다. 최대 보관량 5 MB 초과 시 가장 오래된 배치부터 폐기하고 `session.degraded` 플래그를 세운다(해당 세션은 분석 대상에서 제외되거나 부분 분석만 수행).

### 2.3 WebSocket 프로토콜

모든 메시지는 `{ "type": string, "sid": string, ... }` 형태.

#### Client → Server

```jsonc
// 1) 세션 시작
{ "type": "session.start", "sid": "uuid-v7",
  "problem_id": "abc302_c", "lang": "cpp20",
  "client_ts": 1730000000000,          // 클라 절대시각 (드리프트 보정용)
  "editor": "monaco@0.45", "initial_code": "" }

// 2) 편집 배치 (주 트래픽)
{ "type": "edit.batch", "sid": "...", "seq": 42,
  "base_t": 128400,                     // 배치 첫 이벤트의 상대시각
  "ops": "<msgpack/varint 인코딩된 EditOp[]>" }

// 3) 하트비트 (5초 주기, 편집 없을 때도 전송)
{ "type": "session.heartbeat", "sid": "...", "t": 131000, "cursor": 842 }

// 4) 제출 마킹 (채점 결과는 별도 경로로 들어와도 무방)
{ "type": "submission.mark", "sid": "...", "t": 240100,
  "submission_id": "sub_88123" }

// 5) 세션 종료
{ "type": "session.end", "sid": "...", "t": 300000,
  "reason": "submitted_ac" | "closed" | "timeout" }
```

#### Server → Client

```jsonc
{ "type": "ack",    "sid": "...", "seq": 42 }        // 최대 수신 seq
{ "type": "resume", "sid": "...", "last_seq": 41 }   // 재연결 응답
{ "type": "error",  "sid": "...", "code": "SCHEMA_INVALID", "seq": 43 }
```

**멱등성 계약**: 서버는 `(sid, seq)`로 중복을 제거한다. 클라이언트는 `ack`를 받지 못한 배치를 동일 `seq`로 재전송해야 하며, **seq를 재사용해 다른 내용을 보내서는 안 된다.**

**세션 타임아웃**: 하트비트가 90초간 없으면 서버가 `reason: "timeout"`으로 세션을 강제 종료 처리한다.

### 2.4 리포트 UI

리포트는 §7.1의 JSON 스키마와 §7.2의 자연어 텍스트를 함께 수신한다. **프런트는 자연어 텍스트를 신뢰하되, 수치는 반드시 JSON 필드에서 렌더링한다** (LLM 출력 안의 숫자를 화면에 직접 쓰지 않는다).

---

## 3. 백엔드: 인제스트 계층

### 3.1 책임

Ingest Gateway는 **stateless**하며 다음만 수행한다.

1. 인증 및 세션 소유권 검증 (`sid`가 해당 유저의 것인가)
2. 스키마 검증 (크기 상한: 배치당 500 이벤트, 64 KB)
3. `(sid, seq)` 중복 제거 — Redis에 세션별 `last_seq`만 보관 (TTL 24h)
4. Kafka에 append (파티션 키 = `sid` → 세션 내 순서 보장)
5. `ack` 응답

**금지사항**: 이 계층에서 AST 파싱, 패턴 매칭, DB write-heavy 작업을 하지 않는다. 게이트웨이는 CPU가 아니라 커넥션 수로 스케일되어야 한다.

### 3.2 처리량 산정

```
피크 동시 세션            10,000
세션당 메시지 주기         1 msg/s
→ 게이트웨이 메시지 처리량  10,000 msg/s
평균 메시지 크기           40 events × 8 B + 헤더 ≈ 400 B
→ 대역폭                  ≈ 4 MB/s (압축 전)
```

WebSocket 커넥션 1만 개는 노드당 2만 커넥션 기준 **인스턴스 1~2대**로 처리 가능. 스티키 세션이 필요 없도록(재연결 시 아무 노드로 붙어도 되도록) `last_seq`는 반드시 공유 Redis에 둔다.

### 3.3 Raw 저장

Kafka consumer(별도 sink)가 `session.end` 또는 30분 경과 시점에 세션 단위로 이벤트를 모아 **하나의 zstd 압축 blob**으로 S3에 저장한다.

```
s3://ps-keystroke-raw/{yyyy}/{mm}/{dd}/{problem_id}/{sid}.zst
```

| 항목 | 계산 |
|---|---|
| 세션당 raw | 2,000 events × 8 B = 16 KB → zstd ≈ **4 KB** |
| 연간 | 2.7×10⁷ × 4 KB ≈ **108 GB/년** |
| 수명주기 | Hot(S3 Standard) 90일 → Glacier 1년 → 삭제 |

Raw blob은 **재분석(알고리즘 규칙 변경 시 재처리)** 용도로만 존재한다. 정상 서비스 경로에서는 읽지 않는다.

---

## 4. 백엔드: Replay Worker (핵심)

세션당 정확히 1회 실행되는 비동기 배치 작업. **멱등**하며, 같은 입력에 항상 같은 출력을 낸다.

### 4.1 단계별 처리

#### Step 1 — 이벤트 재생 + 증분 AST 유지

```
tree = parser.parse(None, "")
for ev in events:                      # 시간순
    apply_to_buffer(ev)                # 문자열 버퍼 갱신
    tree.edit(ev)                      # tree-sitter edit 정보 주입
    tree = parser.parse(tree, buffer)  # 증분 재파싱
```

- 세션 전체에서 **AST는 단 하나만 유지**한다. pause마다 새로 만들지 않는다.
- tree-sitter의 증분 파싱은 편집 영향 범위만 재파싱하므로 이벤트당 비용이 코드 길이 N에 비례하지 않는다 → 세션당 **O(K)** (K = 이벤트 수).
- 워커는 언어별 파서를 로드한다(`cpp`, `python`, `rust`, `java`, ...). 파서 미지원 언어는 pause 탐지까지만 수행하고 AST 라벨링·패턴 매칭을 건너뛴다(`analysis_level: "timing_only"`).

#### Step 2 — pause 탐지 (개인 기준선)

```
intervals = [ev[i].t - ev[i-1].t  for i in 1..K  if ev[i].src == "user"]
med = median(intervals)
mad = median(|x - med| for x in intervals)
threshold = med + 5 * mad          # 개인·세션별 적응형
pauses = [i for i in 1..K if intervals[i] > threshold]
```

- 고정 임계값(예: 2초)을 쓰지 않는 이유: 타이핑 속도의 개인차가 커서 고정값은 빠른 타이피스트의 정체를 놓치고 느린 타이피스트를 과탐지한다.
- 세션 표본이 작을 때(K < 100) 안정성이 떨어지므로, 그 경우 **유저 전역 기준선**(과거 전체 세션의 med/mad, 유저 프로필에 캐시)으로 fallback한다.
- 하한 클램프: `threshold = max(threshold, 1500 ms)` — 단순 타이핑 리듬 흔들림을 pause로 잡지 않기 위함.

#### Step 3 — pause의 AST 컨텍스트 라벨링

각 pause 시점에서 커서(또는 마지막 편집 위치)를 포함하는 최소 AST 노드를 찾고, 조상 체인을 따라 올라가며 라벨을 부여한다.

| AST 컨텍스트 | 라벨 |
|---|---|
| `function_definition` 파라미터/시그니처 내부 | `INTERFACE_DESIGN` |
| `for_statement` / `while_statement`의 조건식 내부 | `LOOP_BOUNDARY` |
| `if_statement`의 조건식 내부 | `BRANCH_CONDITION` |
| 배열 첨자(`subscript_expression`) 내부 | `INDEX_REASONING` |
| 함수 본문의 시작 직후(첫 문장 이전) | `ALGORITHM_ENTRY` |
| 최상위(선언부/헤더) | `SETUP` |
| ERROR 노드 내부 (구문이 깨진 상태) | `SYNTAX_STRUGGLE` |

tree-sitter는 불완전한 코드에서도 부분 AST와 ERROR 노드를 생성하므로, 작성 도중 상태에서도 라벨링이 가능하다.

#### Step 4 — 삭제 버스트 탐지 + 구조적 diff 분류

- 인접 delete 이벤트를 클러스터링(간격 < 500 ms) → 버스트 단위로 묶음
- 버스트 총 삭제량이 임계값(예: 40자 또는 전체 버퍼의 10 %) 이상이면 **pivot 후보**
- 버스트 직전/직후 AST를 비교(서브트리 해시 기반 diff)하여 분류:

| 조건 | pivot 유형 |
|---|---|
| 제어구조 노드 종류가 바뀜 (loop ↔ recursion, 재귀 호출 노드 신설/제거) | `APPROACH_SWITCH` |
| 자료구조 타입 선언만 교체 (동일 제어 흐름) | `COMPLEXITY_FIX` |
| `if` 조건식 또는 비교 연산자만 변경 | `EDGE_CASE_FIX` |
| 삭제 후 동일 구조로 재작성 (해시 동일) | `TYPO` (지표에서 제외) |

#### Step 5 — 구조 패턴 매칭 (디바운스 필수)

함수·변수 **이름에 의존하지 않고** 제어 흐름 + 자료구조 사용 형태로 알고리즘을 식별한다.

| 패턴 | 이름-무관 시그니처 |
|---|---|
| `BFS` | 큐 계열 컨테이너 선언 + `while` 루프 + 루프 내부에서 동일 컨테이너에 push + boolean 배열 인덱싱 |
| `DFS_RECURSIVE` | 함수 정의 내부에 **자기 자신 호출 노드** 존재 + 호출 전후로 배열 원소 대입 |
| `DFS_ITERATIVE` | 스택 사용(append/pop 말단) + `while` + 방문 배열 |
| `BINARY_SEARCH` | 정수 변수 쌍 (a, b)에 대해 `while (a < b)` 형태 + 루프 내부에서 중간값 계산 후 a 또는 b **한쪽만** 갱신 |
| `DP` | 배열/맵의 인덱스 i를 채울 때 동일 배열의 다른 인덱스를 참조 (또는 조회-후-없으면-계산 분기 = memoization) |
| `GREEDY` | 정렬 호출 직후 단일 패스 루프, 재귀/백트래킹 없음 |
| `DSU` | `p[i] = i` 형태 자기참조 초기화 + 해당 배열을 따라 올라가는 루프/재귀 |

**성능 제약 (중요)**: 서브트리 패턴 매칭은 편집당 O(1)이 아니다. 매 이벤트마다 실행하면 세션 비용이 O(K·N)으로 폭발한다. 따라서 **다음 시점에만 실행**한다.

- pause가 감지된 시점
- 직전 매칭 이후 변경 누적량이 임계값(예: 200자) 초과한 시점
- 제출 마킹 시점 및 세션 종료 시점

실행 횟수는 세션당 수십 회 수준으로 억제된다 → **O(P·M)**, P = 매칭 실행 횟수, M = 매칭 1회 비용.

#### Step 6 — 패턴 형성 구간 역산 (backward labeling)

구조 패턴은 정의상 **완성되어야** 매칭된다. 그러나 알고 싶은 것은 "그 패턴을 짜느라 헤맨 구간"이다. 따라서 사후 역산한다.

```
1. 세션 종료 시점의 AST에서 확정된 패턴 집합 P를 얻는다.
2. 각 패턴 p에 대해, p를 구성하는 AST 노드들의 소스 범위 R(p)를 얻는다.
3. 이벤트 로그를 역방향으로 훑어, R(p) 범위에 최초로 문자가 삽입된 시각 t_start(p)를 찾는다.
   (버퍼 오프셋은 이후 편집으로 이동하므로, 재생 중 각 문자에 origin_event_id를 부착해 추적)
4. p가 매칭에 처음 성공한 시각을 t_complete(p)로 한다.
5. formation_window(p) = [t_start(p), t_complete(p)]
6. 이 구간 내의 pause·pivot을 패턴 p에 귀속시킨다.
```

**구현 노트**: 3번의 문자별 origin 추적을 위해 버퍼를 단순 문자열이 아닌 **piece table 또는 rope + origin 태그** 구조로 유지한다. 메모리 오버헤드는 세션당 코드 길이 × 4 B 수준(수십 KB)으로 무시 가능.

#### Step 7 — Phase 세그멘테이션

세션 전체를 시간축으로 분할한다.

| Phase | 판정 규칙 |
|---|---|
| `SETUP` | 세션 시작 ~ 첫 함수 본문 진입 |
| `FORMATION` | 첫 함수 본문 진입 ~ 마지막 패턴의 `t_complete` |
| `DEBUG` | 첫 제출 마킹 ~ 마지막 제출 (제출 사이 구간) |
| `REFINE` | 최종 AC 이후 편집 (있는 경우) |

Phase 분해가 중요한 이유: "총 소요 시간은 평균인데 FORMATION 비중이 유독 큰" 유저와 "FORMATION은 빠른데 DEBUG가 긴" 유저는 완전히 다른 처방이 필요하다.

### 4.2 워커 출력 스키마 (Feature Store)

```sql
-- 세션 요약 (세션당 1행)
CREATE TABLE session_summary (
  sid              UUID,
  user_id          UInt64,
  problem_id       String,
  tier             Enum8('A','B','C','D','E','F','G'),
  lang             LowCardinality(String),
  analysis_level   Enum8('full','timing_only','degraded'),
  total_ms         UInt32,
  setup_ms         UInt32,
  formation_ms     UInt32,
  debug_ms         UInt32,
  refine_ms        UInt32,
  keystroke_count  UInt32,
  pause_total_ms   UInt32,
  pause_count      UInt16,
  pivot_count      UInt16,
  submission_count UInt8,
  verdict_seq      Array(LowCardinality(String)),
  final_verdict    LowCardinality(String),
  code_bytes       UInt32,
  created_at       DateTime
) ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (user_id, problem_id, created_at);

-- pause 상세 (세션당 수십 행)
CREATE TABLE pause_event (
  sid          UUID,
  user_id      UInt64,
  t_ms         UInt32,
  duration_ms  UInt32,
  ast_label    LowCardinality(String),   -- INTERFACE_DESIGN 등
  pattern      LowCardinality(String),   -- 귀속된 구조 패턴, 없으면 ''
  phase        LowCardinality(String)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(...)
ORDER BY (user_id, ast_label, t_ms);

-- pivot 상세
CREATE TABLE pivot_event (
  sid, user_id, t_ms, deleted_chars UInt32,
  pivot_type LowCardinality(String), pattern LowCardinality(String)
) ...;

-- 패턴 형성 구간 (세션당 0~5행)
CREATE TABLE pattern_window (
  sid, user_id, problem_id, tier,
  pattern       LowCardinality(String),
  t_start_ms    UInt32,
  t_complete_ms UInt32,
  formation_ms  UInt32,
  pause_ms_in_window UInt32,
  pivot_count_in_window UInt8
) ...;
```

**저장량 산정**

| 테이블 | 세션당 | 연간 (2.7×10⁷ 세션) |
|---|---|---|
| session_summary | ~200 B | 5.4 GB |
| pause_event | 40행 × 32 B = 1.3 KB | 35 GB |
| pivot_event | 5행 × 32 B = 160 B | 4.3 GB |
| pattern_window | 3행 × 48 B = 144 B | 3.9 GB |
| **합계 (압축 전)** | **~1.8 KB** | **≈ 49 GB/년** |

컬럼 지향 압축(LowCardinality + zstd) 적용 시 실효 **10~15 GB/년**. 수십만 유저 규모에서 전혀 부담이 없다.

---

## 5. 백엔드: 기준선(Baseline) 계층

### 5.1 문제 클러스터링 — 공식 태그 없이

문제별 정답 알고리즘 태그가 없으므로, **모집단 AC 코드에서 유도**한다.

```
1. 각 문제의 AC 코드 최대 300개를 샘플링
2. 각 코드에 §4.1 Step 5의 구조 패턴 매처를 적용
   → 문제별 패턴 출현 빈도 벡터 (차원 = 패턴 수, ~20)
3. 여기에 구조 통계 피처 추가: 최대 루프 중첩 깊이, 재귀 유무,
   순환 복잡도 중앙값, 사용 컨테이너 종류 → 총 40차원 내외
4. L2 정규화 후 k-means (k ≈ 40~60), 또는 계층적 클러스터링
5. 결과: problem_id → cluster_id 매핑 테이블
```

- 이 클러스터는 "공식 태그"보다 **실제 코드 형태에 밀착**되어 있다는 장점이 있다.
- 갱신 주기: 주 1회 배치. 신규 문제는 AC 코드 30개 이상 확보 시 다음 배치에서 편입, 그전까지는 `cluster_id = UNKNOWN`(티어 단독 기준선으로 fallback).
- 비용: 5×10⁴ 문제 × 300 코드 = 1.5×10⁷ 회 파싱 + 매칭. 주 1회, 병렬 처리로 수 시간. 증분 처리(신규/변경 문제만) 시 훨씬 짧다.

### 5.2 기준선 셀 구조

```
cell_key = (tier, cluster_id, metric)
metric ∈ { total_ms, formation_ms, debug_ms, pause_total_ms,
           pause_count, pivot_count, submission_count,
           formation_ms@BINARY_SEARCH, formation_ms@DP, ... }
```

셀 수 = 7 tier × 60 cluster × ~15 metric ≈ **6,300 셀**.

### 5.3 분위수 추정: t-digest (핵심 확장성 기법)

수십만 유저의 raw 값을 전부 보관해 정렬하는 방식은 확장되지 않는다. 대신 **병합 가능한 분위수 스케치**를 사용한다.

| 연산 | 복잡도 |
|---|---|
| 값 1개 삽입 | O(log n) 상각, 실질 상수 |
| 스케치 병합 | O(스케치 크기), 압축 계수 δ=100 기준 수백 센트로이드 |
| 임의 분위수 조회 | O(log n), 사실상 상수 |
| 스케치 1개 크기 | ≈ 2 KB |

```
전체 기준선 저장량 = 6,300 셀 × 2 KB ≈ 13 MB
```

**13 MB.** 전 서비스의 모든 난이도·클러스터별 기준선이 Redis 한 인스턴스 메모리에 통째로 들어간다. 이것이 이 설계가 수십만 유저에서 성립하는 이유다.

- 갱신: Replay Worker가 세션 처리 완료 시 해당 셀 스케치에 값을 삽입 (비동기 큐, 배치 병합)
- 조회: 리포트 생성 시 `percentile(user_value)` 를 O(1)에 얻음
- 신뢰성: 셀당 표본 수를 함께 저장하고, **n < 30이면 백분위를 리포트에 노출하지 않는다** (상위 티어 셀로 fallback 또는 "표본 부족" 표기)

### 5.4 티어 기반 기대값 회귀

티어는 순서형 변수이므로, 클러스터별로 `expected_metric ~ tier` 단조 회귀(isotonic regression)를 적용해 기대값 곡선을 만든다. 유저의 잔차:

```
residual = observed_metric - expected_metric(tier, cluster)
```

이 잔차를 다시 셀 스케치에 넣어 백분위를 구하면, 표본이 적은 셀도 인접 티어 정보로 보강된다. 회귀 계수는 일 1회 배치로 갱신하며, 셀당 파라미터 수십 바이트에 불과하다.

### 5.5 유저 프로필 집계

```sql
CREATE TABLE user_profile_agg (
  user_id, cluster_id, metric,
  sample_count UInt32,
  residual_percentile_avg Float32,
  dominant_ast_label LowCardinality(String),
  dominant_pivot_type LowCardinality(String),
  updated_at DateTime
) ENGINE = ReplacingMergeTree ORDER BY (user_id, cluster_id, metric);
```

희소 저장(유저가 실제로 접한 클러스터만). 유저당 평균 20 클러스터 × 15 metric × 40 B ≈ **12 KB** → 5×10⁵ 유저 = **6 GB**. 충분히 감당 가능.

---

## 6. 복잡도 종합

### 6.1 시간 복잡도

| 단계 | 복잡도 | 실측 목표 | 비고 |
|---|---|---|---|
| 클라 이벤트 수집 | 편집당 O(1) | < 0.1 ms | 메인 스레드 블로킹 금지 |
| Ingest 검증·append | 메시지당 O(배치 크기) | < 2 ms | AST 파싱 없음 |
| **증분 AST 재생** | 세션당 **O(K)** | 100~300 ms | K = 이벤트 수, 코드 길이 N과 무관 |
| pause 탐지 | O(K) | < 5 ms | 타임스탬프 통계만 |
| AST 컨텍스트 라벨링 | pause당 O(depth) ≈ O(20) | 무시 가능 | |
| **구조 패턴 매칭** | **O(P·M)** | 50~150 ms | P = 디바운스된 실행 횟수(수십) |
| 패턴 구간 역산 | O(K + \|P\|) | < 10 ms | origin 태그 추적 |
| 기준선 삽입 | 값당 O(log n) | < 1 ms | t-digest |
| 리포트 조회 | O(클러스터 수) ≈ O(60) | < 20 ms | 스케치 조회는 상수 |

**세션 1건 처리 총 비용 ≈ 200~500 ms (단일 코어)**

```
일 신규 세션 = DAU 5×10⁴ × 1.5 ≈ 7.5×10⁴ 세션/일
소요 CPU-시간 = 7.5×10⁴ × 0.4 s ≈ 8.3 CPU-hour/일
→ 워커 코어 1개면 이론상 처리 가능 (8.3h < 24h)
→ 실제로는 피크 흡수 + 여유를 위해 8~16 코어 오토스케일 권장
```

**왜 실시간 경로에서 분석하지 않는가**: 증분 파싱 자체는 싸지만, 피크 1만 동시 세션 × 초당 수 이벤트를 게이트웨이에서 파싱하면 CPU가 커넥션 수와 결합되어 스케일 특성이 나빠진다. 큐를 통해 분리하면 게이트웨이는 I/O 바운드(커넥션 기준 스케일), 워커는 CPU 바운드(큐 길이 기준 스케일)로 **독립적으로 확장**된다. 또한 워커가 밀려도 사용자 경험(에디터)에는 영향이 없다.

### 6.2 공간 복잡도

| 계층 | 세션당 | 연간 총량 | 보관 정책 |
|---|---|---|---|
| Raw 이벤트 blob (S3) | 4 KB | 108 GB | 90일 Hot → 1년 Glacier → 삭제 |
| 파생 피처 (ClickHouse) | 1.8 KB → 압축 후 ~0.5 KB | 10~15 GB | 영구 |
| 기준선 스케치 (Redis) | — | **13 MB (총량)** | 영구, 인메모리 |
| 유저 프로필 집계 | — | 6 GB | 영구 |
| 워커 런타임 메모리 | 세션 1건분 AST + 버퍼 ≈ 수백 KB | — | 세션 처리 후 즉시 해제 |

**중요**: 워커는 동시에 처리 중인 세션의 AST만 메모리에 유지한다. 모든 유저의 AST를 동시에 들고 있는 구조가 아니므로, 유저 수 증가가 메모리 사용량을 선형으로 늘리지 않는다.

### 6.3 확장 병목 지점

| 병목 | 증상 | 대응 |
|---|---|---|
| WebSocket 커넥션 수 | 동시 세션 > 10만 | 게이트웨이 수평 확장 (stateless이므로 단순) |
| 워커 큐 적체 | 리포트 지연 > 5분 | 워커 오토스케일 (큐 길이 기준 HPA) |
| 클러스터링 배치 | 문제 수 > 50만 | 증분 클러스터링(신규 문제만 기존 중심에 배정) |
| 기준선 셀 희소화 | 셀당 표본 < 30 | 클러스터 k 축소 또는 티어 인접 셀 병합 |
| ClickHouse pause 테이블 | 행 수 > 10¹¹ | 90일 초과분은 세션 단위 집계로 롤업 후 상세 행 삭제 |

---

## 7. LLM 연동 (유일한 LLM 사용 지점)

### 7.1 입력: 결정론적 리포트 JSON

Report Builder가 규칙만으로 생성한다. **LLM은 이 JSON을 만드는 데 전혀 관여하지 않는다.**

```jsonc
{
  "user_id": 12345,
  "period": "2026-06-01/2026-07-26",
  "sessions_analyzed": 87,
  "findings": [
    {
      "rank": 1,
      "cluster_id": 17,
      "cluster_label": "binary_search_heavy",     // 대표 패턴에서 자동 생성
      "representative_patterns": ["BINARY_SEARCH"],
      "sample_count": 11,
      "metric": "formation_ms",
      "user_median_ms": 340000,
      "baseline_median_ms": 95000,
      "residual_percentile": 12,
      "dominant_phase": "FORMATION",
      "dominant_ast_label": "LOOP_BOUNDARY",
      "dominant_pivot_type": "APPROACH_SWITCH",
      "pivot_count_median": 3
    },
    { "rank": 2, "...": "..." }
  ],
  "trend": {
    "first_half_percentile_avg": 28,
    "second_half_percentile_avg": 41,
    "direction": "improving"
  }
}
```

`rank`(우선순위)도 **규칙으로 결정**한다: `residual_percentile` 오름차순 → 동률 시 `sample_count` 내림차순 → 최근성 가중. LLM이 순서를 바꾸지 않는다.

### 7.2 LLM 호출 규약

**시스템 프롬프트 필수 제약**

1. 입력 JSON에 존재하는 수치만 사용할 것. 새로운 수치를 추정·계산·반올림하지 말 것.
2. 어느 항목이 약점인지 판단하지 말 것 — `rank`가 이미 정해져 있음.
3. 출력은 `findings` 배열 순서를 그대로 따를 것.
4. 항목당 2~3문장, 전체 400자 이내.
5. 온도 0.2 이하, 고정 seed(지원 시).

**출력 예시**

> 이분탐색 계열 문제(11문제)에서 코드 형성 단계 소요 시간이 같은 난이도 대비 하위 12 % 수준입니다. 정체가 주로 반복문 경계 조건 작성 지점에 몰려 있고, 접근법을 통째로 갈아엎는 수정이 문제당 중앙값 3회 발생했습니다. 이분탐색 구현 자체보다 탐색 범위와 종료 조건을 확정하는 단계가 병목으로 보입니다.

### 7.3 Grounding 검증기 (LLM 응답 후 필수)

```
1. 생성 텍스트에서 모든 숫자 토큰을 정규식으로 추출
2. 각 숫자가 입력 JSON의 어떤 필드 값과 일치하는지 대조
   (단위 변환 허용 목록: ms→s/분, 소수점 반올림 ±1)
3. 매칭되지 않는 숫자가 하나라도 있으면 → 재시도 (최대 2회)
4. 2회 실패 시 → LLM 텍스트 폐기, 템플릿 기반 문장으로 fallback
5. 검증 실패율을 메트릭으로 수집 (임계 초과 시 알림)
```

**템플릿 fallback**은 반드시 구현한다. LLM 장애·정책 변경·비용 초과 시에도 서비스가 동작해야 하며, 이 시스템의 가치는 LLM이 아니라 그 앞단의 결정론적 분석에 있다.

### 7.4 호출량 및 비용 제어

- 호출 시점: 유저가 리포트를 **열람할 때** (세션 종료마다 X)
- 캐시: `(user_id, report_version)` 키로 캐시. 새 세션이 N건 쌓이기 전에는 재생성하지 않음
- 예상 호출량: DAU 5×10⁴ × 리포트 열람률 20 % ≈ 1×10⁴ 회/일. 캐시 적중 시 실제 호출은 그 절반 이하

---

## 8. 실패 처리 및 데이터 품질

| 상황 | 처리 |
|---|---|
| 클라 네트워크 단절 | IndexedDB 버퍼 → 재연결 시 `seq`부터 재전송 |
| 버퍼 오버플로 (5 MB 초과) | `session.degraded = true`, 해당 세션 분석 제외 |
| seq 누락(gap) 감지 | 워커가 `analysis_level = "degraded"` 처리, 기준선 삽입 제외 |
| 워커 처리 실패 | Kafka offset 미커밋 → 재시도. 3회 실패 시 DLQ + 알림 |
| 파서 미지원 언어 | `analysis_level = "timing_only"` (pause/phase만, 패턴·라벨 없음) |
| 세션이 비정상적으로 김 (> 6시간) | 자리 비움 가능성 → 30분 이상 무편집 구간을 총 시간에서 제외 |
| 이벤트 수 극단값 (K < 50) | 분석 제외 (문제 열람만 하고 이탈한 경우) |

**멱등성**: 워커는 같은 `sid`를 재처리해도 동일 결과를 내야 한다. 파생 테이블은 `sid` 기준 upsert(ReplacingMergeTree), 기준선 스케치는 `sid` 처리 완료 마커를 확인해 중복 삽입을 방지한다.

---

## 9. 팀별 책임 분담

### 프런트엔드

- [ ] 에디터 change 이벤트 → `EditOp` 변환 (IME/자동완성 처리 포함)
- [ ] 버퍼링·델타 인코딩·msgpack 직렬화
- [ ] WebSocket 클라이언트 (재연결, seq 관리, ack 처리)
- [ ] IndexedDB 오프라인 버퍼
- [ ] 하트비트 + 커서 위치 전송
- [ ] 리포트 UI (수치는 JSON, 서술은 LLM 텍스트에서 렌더)

### 백엔드 — 인제스트

- [ ] WebSocket 게이트웨이 (stateless, 인증, 스키마 검증)
- [ ] Redis 기반 `last_seq` 중복 제거
- [ ] Kafka producer + S3 sink (세션 단위 zstd 압축)

### 백엔드 — 분석

- [ ] tree-sitter 증분 재생 엔진 (언어별 파서 로딩)
- [ ] pause 탐지 (MAD 적응형) + AST 컨텍스트 라벨러
- [ ] 삭제 버스트 탐지 + 서브트리 해시 diff 분류기
- [ ] 구조 패턴 매처 (7종, 이름-무관) + 디바운스 스케줄러
- [ ] 패턴 형성 구간 역산 (origin 태그 rope 버퍼)
- [ ] phase 세그멘터

### 백엔드 — 집계/서빙

- [ ] 문제 클러스터링 주간 배치
- [ ] t-digest 기준선 스토어 + isotonic 회귀 일 배치
- [ ] 유저 프로필 집계
- [ ] Report Builder (규칙 기반 랭킹)
- [ ] LLM 렌더러 + grounding 검증기 + 템플릿 fallback

---

## 10. 마일스톤

| 단계 | 범위 | 검증 기준 |
|---|---|---|
| **M1** | 수집 파이프라인 (프런트 → 게이트웨이 → S3) | 세션 재생으로 최종 코드가 실제 제출 코드와 **바이트 단위 일치** |
| **M2** | 재생 엔진 + pause 탐지 + AST 라벨링 | 수동 검수 50세션에서 라벨 정확도 ≥ 85 % |
| **M3** | 구조 패턴 매처 | 정답 라벨 보유 문제군에서 패턴 재현율 ≥ 80 %, 정밀도 ≥ 90 % |
| **M4** | 클러스터링 + t-digest 기준선 | 셀당 표본 ≥ 30 확보 비율 ≥ 70 % |
| **M5** | 리포트 + LLM 렌더링 | grounding 검증 통과율 ≥ 98 % |
| **M6** | 부하 테스트 | 동시 세션 1만, 워커 지연 P95 < 3분 |

**M1의 "바이트 단위 일치"가 가장 중요한 게이트다.** 재생 결과가 실제 코드와 다르면 이후 모든 분석이 무의미하므로, IME·자동완성·undo/redo 처리가 완벽해질 때까지 M2로 넘어가지 않는다.

---

## 11. 미해결 이슈 / 결정 필요 사항

1. **undo/redo 표현** — 에디터의 undo를 "역방향 편집 이벤트들"로 기록할지, 별도 `op: 2 (undo)` 타입으로 기록할지. 전자가 재생은 단순하지만 pivot 탐지에서 undo와 의도적 삭제를 구분할 수 없다. **결정사항: 별도 타입으로 기록하고 pivot 판정에서 제외.**
2. **다중 파일/탭** — AtCoder는 단일 파일이므로 MVP 범위 밖. 향후 확장 시 `file_id` 축 추가 필요.
3. **비교군 데이터 확보** — 다른 유저의 키스트로크 로그가 있는가? 없다면 pause/pivot/formation 계열 지표는 **본인 문제 간 상대 비교**로만 쓰고, 모집단 비교는 제출 기반 지표(총 소요 시간, 시도 횟수)로 제한해야 한다. 이 문서는 전자를 가정하고 작성되었다.
4. **프라이버시** — 키스트로크는 민감 데이터. 수집 동의 UI, raw 로그 보관 기간 명시, 유저 삭제 요청 시 S3 blob 및 파생 행 연쇄 삭제 경로가 필요하다.
5. **패턴 매처 규칙 버전 관리** — 규칙이 바뀌면 과거 세션과 비교가 불가능해진다. `matcher_version`을 파생 행에 기록하고, 버전 변경 시 raw blob으로부터 재처리하는 백필 잡을 준비해야 한다.
