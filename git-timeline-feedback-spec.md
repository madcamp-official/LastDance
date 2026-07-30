# git 방식 학습 분석 파이프라인 설계 (AST 파이프라인 대체)

## 0. 요약

기존 AST 트리 기반 분석(패턴 매처 + 구조 분류기)을 **git 방식 라인 타임라인**으로 전면 교체한다.

- 세션의 EditOp 스트림을 재생해 **5초 이상 pause를 커밋 경계**로 하는 라인 단위 커밋 로그를 만든다.
  각 커밋은 라인 추가/수정/삭제(hunk), 직전 pause 시간(Δt), 시각을 담고, 제출 시점과 채점 결과(WA/TLE/RE 등등)가
  커밋 사이에 끼워진다.
- 결정론적 전처리기가 **STALL(막힘 추정)과 HIGH_CHURN(대량 수정)을 서로 다른 라벨로 분리**해서
  LLM에 전달한다 — "코드가 많이 바뀐 구간 = 어려웠던 구간"이라는 오진을 원천 차단.
- LLM 1차 호출(세션 분석기)이 문제 지문 + 커밋 로그를 보고 **논리 레벨의 구조화된 인사이트
  (JSON)**를 만들어 문제별 테이블에 저장한다. 이 테이블이 같은 문제를 푼 다른 사용자와의
  비교군이 된다.
- LLM 2차 호출(피드백 작성기)이 인사이트 + 비교군 집계를 받아 사용자용 한국어 피드백을 만든다.
  "알고리즘 함수 작성이 오래 걸렸어요"가 아니라 "BFS 구현 시작은 빨랐지만 문제의 탐색 정책을 적용하는 데
  시간이 오래 걸렸어요" 수준의 구체성을 요구한다.

기존 인프라(Ingest Gateway, Kafka, replay worker의 finalize 큐/프로세스풀, 멱등성 규약,
grounding 검증)는 그대로 재사용한다. 교체되는 것은 `analyze_session` 내부와 그 산출물 저장,
그리고 피드백 프롬프트 구성이다.

---

## 1. 전체 아키텍처

```
[클라이언트 에디터]
   │  WS: session.start / edit.batch / submission.mark / session.end   (기존 그대로)
   ▼
[Ingest Gateway  app/api/ingest.py]  — 검증·중복제거·append (변경 없음)
   ▼
[Kafka]
   ▼
[Replay Worker  app/worker/consumer.py]  — session.end 시 finalize 큐 (변경 없음)
   ▼
┌─ Stage A: 타임라인 빌더 (결정론적, ProcessPoolExecutor) ─────────────┐
│  EditOp 재생 → 라인 문서 유지 → 5s pause 경계로 커밋 분할            │
│  → 커밋별 diff(hunk) 계산 → churn/stall 결정론 라벨링                │
│  산출: TimelineResult (커밋 로그 + 세그먼트 라벨 + 통계)             │
└──────────────────────────────────────────────────────────────────────┘
   ▼  DB 저장 (code_commits / session_segments / session_summaries)
┌─ Stage B: LLM 세션 분석기 (finalize 후 세션당 1회, 비동기) ──────────┐
│  문제 지문 + 커밋 로그 렌더링 → LLM(JSON 모드, temp=0, seed 고정)    │
│  → 구조화 인사이트 검증(커밋 참조/시간 범위/enum) → 저장             │
│  산출: problem_feedback_insights 행들                                 │
└──────────────────────────────────────────────────────────────────────┘
   ▼
┌─ Stage C: 피드백 작성기 (POST /feedback 시점, 온디맨드) ─────────────┐
│  본인 인사이트 + 같은 problem_id 비교군 집계 → LLM → grounding 검증  │
│  산출: feedbacks 행 (기존 테이블 재사용)                              │
└──────────────────────────────────────────────────────────────────────┘
```

실패 격리 규약은 기존과 동일:

- Stage A는 결정론적이며 LLM 없이 완결된다. Stage B 실패는 인사이트를 `pending`으로 남길 뿐
  파이프라인을 죽이지 않는다 (기존 `classify_unmatched` 실패 처리와 동일 패턴).
- Stage C는 인사이트가 없으면(분석기 실패/지연) 커밋 로그 통계만으로 템플릿 피드백 폴백.

---

## 2. Stage A — git 방식 타임라인 빌더 (결정론적)

새 모듈: `app/worker/timeline.py`. `analyze_session`을 대체하는 진입점
`build_timeline(events: List[EditOp], lang: str, submission_ts: List[int], verdicts: List[str]) -> TimelineResult`.

### 2.1 커밋 경계 규칙

| 규칙 | 값 | 설명 |
|---|---|---|
| `PAUSE_COMMIT_MS` | 5000 | 연속 이벤트 간격 ≥ 5초면 그 지점에서 커밋을 닫는다 |
| 제출 경계 | 항상 | `submission.mark` 시각은 무조건 커밋을 닫고 SUBMIT 레코드를 삽입 |
| 세션 종료 | 항상 | 마지막 잔여 편집은 최종 커밋으로 닫는다 |

커밋 `c_k`의 속성:

- `t_ms`: 커밋을 닫은 시각 (마지막 이벤트의 t)
- `pause_before_ms`: 이 커밋의 첫 이벤트와 직전 커밋 마지막 이벤트 사이 간격.
  **"이 커밋을 시작하기 전에 얼마나 생각했는가"**가 의미. 첫 커밋은 세션 시작 기준.
- `duration_ms`: 커밋 내 첫~마지막 이벤트 간격 (타이핑에 쓴 시간)

### 2.2 diff 계산

커밋 경계마다 직전 스냅샷과 현재 스냅샷을 라인 단위로 비교해 hunk를 만든다
(`difflib.SequenceMatcher` 수준이면 충분 — 코드포인트 오프셋 재생은 기존 `replay.py` 재사용).

hunk 구조 (JSON 직렬화):

```json
{
  "op": "add" | "del" | "mod",
  "old_start": 12,        // 직전 스냅샷 기준 시작 라인 (1-based), add면 삽입 위치
  "new_start": 12,        // 현재 스냅샷 기준 시작 라인
  "old_lines": ["..."],   // del/mod: 삭제·수정 전 라인 원문
  "new_lines": ["..."]    // add/mod: 추가·수정 후 라인 원문
}
```

- 라인 번호는 **해당 커밋 시점 스냅샷 기준**이다. 이후 커밋의 추가/삭제로 물리 라인 번호가
  밀리는 문제는 (a) 내부적으로는 stable line id(아래 2.3)로 추적하고, (b) LLM에게는 프롬프트
  규칙으로 명시한다 (§4.2 시스템 프롬프트 규칙 3).
- `src != "user"`(autoindent/autocomplete) 이벤트는 재생에는 포함하되 keystroke 통계에서 제외
  (기존 규약 유지).

### 2.3 stable line id와 churn 계산

재생 중 각 물리 라인에 생성 시점 부여 id(`lid`)를 붙이고 삽입/삭제에 따라 시프트만 시킨다.
이러면 "같은 라인을 몇 번 고쳤는가"를 라인 번호 이동과 무관하게 셀 수 있다.

- `edit_count[lid]`: 그 라인이 mod/del로 다시 만져진 커밋 수
- 커밋의 `churn_lines`: 이번 커밋에서 만져진 라인 중 `edit_count ≥ 2`인 라인 수
- 커밋의 `net_lines`: 추가 − 삭제

### 2.4 결정론적 세그먼트 라벨 (오진 차단 핵심)

연속 커밋들을 묶어 세그먼트로 만들고 아래 라벨 중 하나를 부여한다. **LLM은 이 라벨을 뒤집을 수
없고, 라벨별로 해석 방향이 프롬프트에 고정된다.**

| 라벨 | 판정 규칙 (우선순위 순) | 의미 |
|---|---|---|
| `DEBUG_LOOP` | 실패 제출(WA/TLE/RE/CE)과 다음 제출 사이의 모든 커밋 | 제출-수정 반복 구간 |
| `STALL_SUSPECT` | `pause_before_ms ≥ 30000` **또는** 세션 내 pause 상위 10% — 이면서 직후 커밋의 `net_lines ≤ 5` | 손은 멈췄고 코드는 거의 안 늘어남 = 사고(관찰·점화식·정책 설계) 추정 |
| `HIGH_CHURN` | 윈도(연속 3커밋)에서 `Σ만진 라인 / max(1, Σnet_lines) ≥ 3` 이고 `Σ만진 라인 ≥ 10`, 또는 동일 lid를 3커밋 이상 반복 수정 | 코드는 많이 바뀌는데 안 나아감 = 대개 사소한 디버깅/타이포 |
| `BURST_WRITE` | `net_lines ≥ 15`인 커밋이 pause ≤ 10s 간격으로 연속 | 머릿속 설계를 쏟아내는 구간 — **분량이 커도 어려웠던 구간이 아님** |
| `STEADY` | 나머지 | 평이한 진행 |

임계값은 `app/worker/timeline.py` 상수로 두고 `TIMELINE_VERSION`에 포함시킨다 (§7 재현성).

### 2.5 산출 스키마 (pydantic, `app/schema/timeline.py`)

```python
class Hunk(BaseModel):
    op: Literal["add", "del", "mod"]
    old_start: int
    new_start: int
    old_lines: List[str] = []
    new_lines: List[str] = []

class Commit(BaseModel):
    seq: int                      # 세션 내 0부터, SUBMIT 레코드도 같은 시퀀스 공간 사용
    kind: Literal["edit", "submit"]
    t_ms: int
    pause_before_ms: int = 0
    duration_ms: int = 0
    hunks: List[Hunk] = []        # kind="edit"
    verdict: Optional[str] = None # kind="submit": AC|WA|TLE|RE|CE|PENDING
    lines_added: int = 0
    lines_deleted: int = 0
    lines_modified: int = 0
    net_lines: int = 0
    churn_lines: int = 0
    snapshot_hash: str = ""       # 커밋 직후 전체 코드 해시
    snapshot_text: Optional[str] = None  # 제출 직전·세션 종료 시에만 채움

class Segment(BaseModel):
    seg_id: str                   # "sg_0" ...
    label: Literal["STALL_SUSPECT", "HIGH_CHURN", "DEBUG_LOOP", "BURST_WRITE", "STEADY"]
    commit_start_seq: int
    commit_end_seq: int
    t_start_ms: int
    t_end_ms: int
    pause_ms: int                 # 세그먼트 내 pause 합
    lines_touched: int
    net_lines: int

class TimelineResult(BaseModel):
    timeline_version: int
    analysis_level: str           # "full" | "degraded" (seq gap 시)
    total_ms: int
    keystroke_count: int
    commits: List[Commit]
    segments: List[Segment]
    final_code: str
    verdict_seq: List[str]
```

---

## 3. DB 스키마

새 모듈: `app/model/timeline.py`, `app/model/insight.py`.
멱등성: 기존 규약대로 워커 재실행 시 sid 기준 삭제 후 재삽입.

### 3.1 `code_commits` — 세션별 git 방식 코드 작성 기록

```python
class CodeCommitRow(Base):
    __tablename__ = "code_commits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    seq = Column(Integer, nullable=False)              # sid 내 순서, (sid, seq) 유니크
    kind = Column(String, nullable=False)              # "edit" | "submit"
    t_ms = Column(Integer, nullable=False)
    pause_before_ms = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)
    hunks_json = Column(String, nullable=False, default="[]")
    verdict = Column(String, nullable=True)            # kind="submit"
    lines_added = Column(Integer, nullable=False, default=0)
    lines_deleted = Column(Integer, nullable=False, default=0)
    lines_modified = Column(Integer, nullable=False, default=0)
    net_lines = Column(Integer, nullable=False, default=0)
    churn_lines = Column(Integer, nullable=False, default=0)
    snapshot_hash = Column(String, nullable=False, default="")
    snapshot_text = Column(String, nullable=True)      # 제출·종료 시점만
    timeline_version = Column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("sid", "seq", name="uq_code_commits_sid_seq"),)
```

### 3.2 `session_segments` — 결정론적 라벨 구간

```python
class SessionSegmentRow(Base):
    __tablename__ = "session_segments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    seg_id = Column(String, nullable=False)
    label = Column(String, nullable=False)             # STALL_SUSPECT | HIGH_CHURN | DEBUG_LOOP | BURST_WRITE | STEADY
    commit_start_seq = Column(Integer, nullable=False)
    commit_end_seq = Column(Integer, nullable=False)
    t_start_ms = Column(Integer, nullable=False)
    t_end_ms = Column(Integer, nullable=False)
    pause_ms = Column(Integer, nullable=False, default=0)
    lines_touched = Column(Integer, nullable=False, default=0)
    net_lines = Column(Integer, nullable=False, default=0)
    timeline_version = Column(Integer, nullable=False)
```

### 3.3 `problem_feedback_insights` — 문제별 피드백 사항 + 타임라인 (비교군 원천)

Stage B(LLM 세션 분석기)의 구조화 출력을 문제 단위로 축적한다.
**같은 problem_id로 조회하면 다른 사용자들이 어느 단계에서 얼마나 걸렸는지가 나온다** — 이것이
비교군 집계의 유일한 원천이며, 기존 AST 기준선(`build_baseline`)을 대체한다.

```python
class ProblemFeedbackInsight(Base):
    __tablename__ = "problem_feedback_insights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    problem_id = Column(Integer, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    sid = Column(String, index=True, nullable=False)
    stage = Column(String, nullable=False)          # §4.3 정준 단계 enum — 집계 키
    category = Column(String, nullable=False)       # "stall" | "churn" | "debug_loop" | "smooth"
    logic_label = Column(String, nullable=False)    # 자유 서술: "DP 점화식 도출", "BFS 탐색 정책 적용" 등
    description = Column(String, nullable=False)    # 근거 요약 (한국어 1~2문장)
    severity = Column(String, nullable=False)       # "high" | "medium" | "low"
    commit_start_seq = Column(Integer, nullable=False)   # ← 피드백 사항 관련 타임라인
    commit_end_seq = Column(Integer, nullable=False)
    t_start_ms = Column(Integer, nullable=False)
    t_end_ms = Column(Integer, nullable=False)
    duration_ms = Column(Integer, nullable=False)
    evidence_json = Column(String, nullable=False, default="[]")  # 참조 커밋/세그먼트 id 목록
    advice = Column(String, nullable=True)
    analyzer_version = Column(String, nullable=False)   # 프롬프트+모델 버전 (백필 기준)
    status = Column(String, nullable=False, default="valid")  # valid | discarded (검증 실패)
    created_at = Column(DateTime(timezone=True), nullable=False)
```

인덱스: `(problem_id, stage)` 복합 인덱스 추가 — 비교군 집계 쿼리 전용.

### 3.4 기존 테이블 처리

- `session_summaries`: 유지하되 컬럼 의미 재정의 — `formation_ms/debug_ms/refine_ms`는
  세그먼트 라벨 합산으로 채움 (STALL 합, DEBUG_LOOP+HIGH_CHURN 합, STEADY/BURST 합).
  `matcher_version` 자리에 `timeline_version` 기록.
- `pause_events`/`pivot_events`/`pattern_windows`/`unmatched_segments`/`ast_trees`:
  **신규 기록 중단, 테이블은 남김** (과거 세션 조회 호환). 새 코드 경로에서 참조 제거.
- `feedbacks`: 그대로 재사용.

---

## 4. Stage B — LLM 세션 분석기

새 모듈: `app/llm/session_analyzer.py`. `consumer._finalize_worker`에서 기존
`classify_unmatched` 자리를 대체한다. 호출 조건: `analysis_level == "full"` 이고 커밋 ≥ 2개.
호출 옵션: `temperature=0, seed=20260730, json_mode=True` (기존 `chat()` 옵션 재사용).

`ANALYZER_VERSION = "session-analyzer-v1/{LLM_MODEL}"` — 프롬프트 문구가 바뀌면 버전을 올린다.

### 4.1 커밋 로그 렌더링 규칙 (프롬프트에 들어가는 텍스트 형식)

```
@c{seq} t=+{m}m{s}s pause={pause_before_ms/1000:.0f}s dur={duration_ms/1000:.0f}s [세그먼트라벨]
  + L{new_start}: {라인 원문}
  - L{old_start}: {라인 원문}
  ~ L{new_start}: {수정 전 원문}  =>  {수정 후 원문}
@s{seq} t=+{m}m{s}s SUBMIT verdict={V}

=== 스냅샷: @s{seq} 제출 시점 전체 코드 ===
{snapshot_text 전문, 라인번호 붙임}
=== 스냅샷 끝 ===
```

- 세그먼트 라벨은 세그먼트의 첫 커밋에만 `[STALL_SUSPECT]`처럼 표기하고, 세그먼트 요약
  블록을 별도로 제공한다 (아래 사용자 프롬프트 §B4).
- 토큰 상한: 커밋당 hunk 라인 최대 40줄(초과 시 `...(+N줄 생략)`), 라인당 160자 절단,
  스냅샷은 제출 시점 + 최종만 포함, 커밋 로그 전체가 모델 컨텍스트의 60%를 넘으면
  STEADY 세그먼트의 hunk 원문부터 통계 요약으로 축약(라벨·수치는 유지).

### 4.2 시스템 프롬프트 (전문 — 생략 없음)

```text
당신은 알고리즘 문제 풀이(PS) 학습 플랫폼의 세션 분석 엔진입니다. 한 사용자가 한 문제를 푸는
동안의 코드 작성 기록이 git 커밋 로그와 유사한 형식으로 주어집니다. 당신의 임무는 이 기록과
문제 지문을 종합해, 사용자가 풀이의 "어느 논리 단계"에서 시간을 썼고 어디서 막혔는지를
구조화된 JSON으로 출력하는 것입니다. 당신의 출력은 사람에게 직접 보이지 않고 DB에 저장되어
이후 피드백 생성과 다른 사용자와의 비교에 사용됩니다.

[입력 형식]
1. 커밋 로그: "@c{번호}"는 편집 커밋, "@s{번호}"는 제출입니다. 커밋 경계는 5초 이상의 입력
   중단(pause)입니다. pause=N초는 그 커밋을 시작하기 "전에" 아무것도 입력하지 않은 시간,
   dur=N초는 그 커밋 안에서 실제로 타이핑한 시간입니다.
2. 라인 표기: "+ L12:"는 추가, "- L12:"는 삭제, "~ L12: A => B"는 수정입니다.
3. 라인 번호는 "그 커밋 시점의 파일 상태" 기준입니다. 이후 커밋에서 위쪽에 라인이 추가되거나
   삭제되면 같은 코드라도 물리 라인 번호가 밀립니다. 따라서 라인 번호를 세션 전체의 고정
   좌표로 취급하지 말고, 코드 내용(함수명, 변수, 구문)으로 논리적 블록을 추적하세요.
   예: @c3에서 추가된 "L7: dp[i] = ..."와 @c9에서 수정된 "L11: dp[i] = ..."는 같은 라인일
   수 있습니다.
4. 세그먼트 라벨은 결정론적 전처리기가 통계 규칙으로 부여한 것입니다:
   - STALL_SUSPECT: 긴 입력 중단 후에도 코드가 거의 늘지 않은 구간. 관찰, 접근 설계,
     점화식 도출, 탐색 정책 결정 같은 "사고" 구간일 가능성이 높습니다.
   - HIGH_CHURN: 같은 라인들을 반복해서 고치거나, 만진 라인 수에 비해 순증가가 적은 구간.
     대개 사소한 디버깅(타이포, 인덱스, 자료형, 출력 형식)입니다.
   - DEBUG_LOOP: 실패한 제출과 다음 제출 사이의 수정 구간.
   - BURST_WRITE: 짧은 시간에 코드가 폭발적으로 늘어난 구간. 이미 머릿속에 있는 설계를
     타이핑으로 옮기는 중이므로, 분량이 커도 "어려웠던 부분"이 아닙니다.
   - STEADY: 평이한 진행.

[절대 규칙]
R1. 세그먼트 라벨의 "구분"을 뒤집지 마세요. 특히:
    - HIGH_CHURN이나 BURST_WRITE 구간을 코드 분량이 크다는 이유로 "어려워했던 부분",
      "핵심 난관"으로 진단하는 것은 금지입니다. 코드가 폭발적으로 늘어난 40줄 구간은
      거의 항상 설계가 끝난 뒤의 타이핑이거나 사소한 디버깅입니다.
    - "어려웠던 부분"(category="stall") 진단은 STALL_SUSPECT 세그먼트, 또는 큰 pause 뒤에
      핵심 로직 라인이 처음 등장하는 지점에만 허용됩니다.
    - HIGH_CHURN/DEBUG_LOOP 구간은 category="churn" 또는 "debug_loop"로만 보고하고,
      무엇을 반복 수정했는지(변수 자료형, 인덱스 경계, 출력 형식 등)를 코드 내용에서
      구체적으로 특정하세요.
R2. 모든 인사이트는 실제 커밋 번호 범위와 시간 범위를 evidence로 가져야 합니다. 입력에 없는
    커밋 번호, 입력에서 계산할 수 없는 시간을 만들어내지 마세요.
R3. logic_label은 문제의 풀이 논리 수준으로 구체적이어야 합니다.
    나쁜 예: "알고리즘 작성", "디버깅", "구현".
    좋은 예: "BFS 큐 뼈대 구현", "문제 고유 탐색 정책(말이 벽을 만나면 멈춤)의 조건 처리",
    "DP 점화식 도출", "long long 오버플로 수정", "출력 형식(공백/개행) 수정".
    문제 지문의 요구사항과 코드 내용을 대조해서 단계를 명명하세요.
5초 이상 pause마다 커밋이 잘리므로, pause가 크고 직후 커밋에서 새 논리가 등장하면 그 pause를
그 논리의 "사고 시간"으로 귀속할 수 있습니다.
R4. 확신이 없으면 인사이트를 만들지 말고 빼세요. 인사이트 0개도 유효한 출력입니다.
R5. 출력은 아래 JSON 스키마를 따르는 단일 JSON 객체만 출력합니다. JSON 밖의 텍스트, 마크다운
    코드펜스, 주석을 출력하지 마세요. description/logic_label/advice는 한국어로 씁니다.

[stage 정준 값 — 이 목록 밖의 값 금지]
- PROBLEM_UNDERSTANDING  : 문제 이해·관찰 (입력 파싱 설계 이전의 긴 정지 등)
- APPROACH_DESIGN        : 알고리즘/접근 선택 (자료구조·전략 결정)
- CORE_LOGIC_DESIGN      : 핵심 논리 설계 (점화식, 탐색 정책, 불변식, 수식 유도)
- SCAFFOLD_IMPLEMENTATION: 뼈대 구현 (입출력, 자료구조 선언, 표준 알고리즘 골격)
- CORE_IMPLEMENTATION    : 핵심 논리의 코드화 (정책·점화식을 실제 조건/식으로 옮기기)
- EDGE_CASE_HANDLING     : 경계·예외 처리 (빈 입력, 범위 끝, 중복 방문 등)
- DEBUG_LOGIC            : 논리 오류 디버깅 (잘못된 점화식/정책 수정)
- DEBUG_TRIVIAL          : 사소한 디버깅 (타이포, 자료형, 인덱스 ±1, 출력 형식)
- OPTIMIZATION           : 시간/공간 최적화 (TLE 대응 등)

[출력 JSON 스키마]
{
  "insights": [
    {
      "stage": "<정준 stage 값>",
      "category": "stall" | "churn" | "debug_loop" | "smooth",
      "logic_label": "<이 문제 풀이 논리 수준의 구체적 명명>",
      "description": "<무엇이 관찰되었는지 1~2문장, 커밋/시간 근거 포함>",
      "severity": "high" | "medium" | "low",
      "commit_range": [<시작 seq>, <끝 seq>],
      "t_range_ms": [<시작 ms>, <끝 ms>],
      "evidence": ["c3 앞 64초 정지", "c4에서 dp 점화식 라인 최초 등장"],
      "advice": "<이 문제 유형에서 관찰된 행동에 근거한 개선 제안 1문장, 없으면 null>"
    }
  ],
  "overall": "<세션 전체 흐름 요약 2~3문장. 잘한 점(빠르게 진행된 stage)도 포함>"
}
```

### 4.3 사용자 프롬프트 템플릿 (전문 — 생략 없음)

`{...}` 자리는 서버가 채운다.

```text
[문제]
제목: {problem.title}
지문:
{problem.statement — 원문 전체. 3000자 초과 시 앞 3000자 + "...(생략)"}
제약: {problem.constraints — 있으면}
입력 형식: {problem.input_format — 있으면}
출력 형식: {problem.output_format — 있으면}

[세션 정보]
언어: {lang}
총 소요: {total_ms/60000:.1f}분, 키 입력 {keystroke_count}회
제출 이력: {verdict_seq를 " → "로 연결, 예: "WA → WA → AC"}
최종 결과: {verdict_seq[-1] or "제출 없음"}

[세그먼트 요약 — 결정론적 전처리 결과, 라벨 구분을 뒤집지 말 것]
{각 세그먼트마다 한 줄:}
{seg_id} [{label}] @c{commit_start_seq}~@c{commit_end_seq} ({t_start}~{t_end}, {지속 분:초})
  pause합={pause_ms/1000:.0f}s, 만진라인={lines_touched}, 순증가={net_lines}

[커밋 로그]
{§4.1 렌더링 규칙에 따른 전체 커밋 로그}

[최종 코드]
{final_code 전문, 라인번호 붙임. 400줄 초과 시 앞 400줄}

위 기록을 분석해 시스템 지침의 JSON 스키마로 출력하세요.
```

### 4.4 렌더링 예시 (형식 확정용)

```text
@c0 t=+0m12s pause=12s dur=25s [STEADY]
  + L1: import sys
  + L2: input = sys.stdin.readline
  + L3: n, m = map(int, input().split())
@c1 t=+1m40s pause=8s dur=55s [BURST_WRITE]
  + L4: from collections import deque
  + L5: def bfs(sx, sy):
  + L6:     q = deque([(sx, sy)])
  + L7:     visited = [[False]*m for _ in range(n)]
  ...(+14줄 생략)
@c2 t=+4m02s pause=78s dur=31s [STALL_SUSPECT]
  ~ L12:     if board[nx][ny] == '#':  =>  while 0 <= nx < n and board[nx][ny] != '#':
  + L13:         nx += dx; ny += dy
@s3 t=+5m10s SUBMIT verdict=WA
=== 스냅샷: @s3 제출 시점 전체 코드 ===
L1: import sys
...
=== 스냅샷 끝 ===
@c4 t=+6m55s pause=41s dur=18s [DEBUG_LOOP]
  ~ L13:         nx += dx; ny += dy  =>  nx += dx; ny += dy
  + L14:     nx -= dx; ny -= dy
@s5 t=+7m30s SUBMIT verdict=AC
```

### 4.5 출력 검증 (결정론, `app/llm/session_analyzer.py`)

grounding 규약 확장 — 실패 시 해당 인사이트만 `status="discarded"`로 저장:

1. JSON 파싱 실패 → 1회 재시도, 재실패 시 세션 전체 인사이트 없음(로그만).
2. `stage`가 정준 enum 밖 → discard.
3. `commit_range`의 seq가 실제 커밋에 없음, `t_range_ms`가 `[0, total_ms]` 밖,
   시작 > 끝 → discard.
4. `category="stall"`인데 commit_range가 STALL_SUSPECT 세그먼트와 겹치지 않고
   pause ≥ 30s인 커밋도 포함하지 않음 → discard (R1 기계 검증).
5. 통과분만 `problem_feedback_insights`에 `status="valid"`로 저장. sid+analyzer_version
   기준 삭제 후 재삽입 (기존 `save_llm_candidates` 멱등 패턴).

---

## 5. Stage C — 피드백 작성기 (POST /feedback 재작성)

### 5.1 비교군 집계 (결정론, `app/util/cohort.py` 신규)

같은 `problem_id`의 `status="valid"` 인사이트를 (stage, category)로 그룹:

- `duration_ms`의 p25/p50/p75 (본인 세션 제외)
- 발생률: 그 문제를 푼 세션 수 대비 해당 stage 인사이트가 있는 세션 비율
- 표본 수 n. **n < 5면 그 stage 비교는 노출하지 않는다** (신뢰 불가 + 자기효능감 보호,
  기존 연구 5 규약 계승). n < 30이면 "[표본 적음]" 표시.
- 세션 수준 지표: 같은 문제의 total_ms, 제출 횟수 분포 (code_commits/submissions에서 직접).

### 5.2 시스템 프롬프트 (전문 — 생략 없음)

```text
당신은 알고리즘 문제 풀이(PS) 연습 플랫폼의 튜터입니다. 사용자가 문제를 푸는 동안의 행동
분석 결과(논리 단계별 인사이트, 시간 분포, 같은 문제를 푼 다른 사용자들과의 비교)가 주어지면,
한국어로 피드백을 작성합니다.

[작성 규칙]
1. 추상적 총평 금지. "디버깅이 오래 걸렸어요", "알고리즘 작성이 느렸어요" 같은 문장을 쓰지
   마세요. 반드시 [인사이트]에 있는 logic_label 수준의 구체성으로 쓰세요.
   좋은 예: "BFS 큐 뼈대는 1분 만에 빠르게 작성하셨지만, 문제 고유의 탐색 정책(벽을 만날
   때까지 미끄러지는 이동)을 조건으로 옮기는 데 78초 정지를 포함해 약 4분을 쓰셨어요."
2. [인사이트]에 없는 사실, [비교군]에 없는 수치를 만들어내지 마세요. 프롬프트에 있는 숫자만
   인용할 수 있습니다.
3. category를 구분해서 서술하세요:
   - stall 인사이트 = 사고 단계에서 막힌 것. "어려워한 부분"으로 표현 가능.
   - churn/debug_loop 인사이트 = 반복 수정. "어려워했다"가 아니라 "사소한 수정(자료형,
     인덱스, 출력 형식 등)에 시간이 샜다"로 표현하세요. 코드 분량이 컸다는 이유로 어려웠다고
     쓰는 것은 금지입니다.
   - smooth 인사이트 = 잘한 점. 피드백 앞부분에 1문장으로 반드시 언급하세요.
4. [비교군] 수치는 참고용 분포입니다. 본인 값이 p75보다 크면 "다른 사용자들보다 오래 걸린
   편", p25보다 작으면 "빠른 편"으로 언급하되 단정적 평가·서열 표현("하위권" 등)은 피하세요.
   "[표본 적음]" 표시가 있으면 "아직 비교 데이터가 적지만" 같은 단서를 붙이세요.
5. 개선 방향은 [인사이트]의 advice 필드에 있는 내용만 사용하고, 자연스러운 문장으로
   녹여내세요. advice가 모두 null이면 관찰된 사실만 서술하고 개선 방향은 생략합니다.
   데이터에 없는 일반론("변수명을 명확히 하세요")을 새로 만들지 마세요.
6. 분량: 4~7문장. 인사이트가 1~2개뿐이면 3~4문장으로 줄이세요.
7. 어조: 존댓말, 과정 중심. 정답 여부 자체를 요약하거나 점수를 평가하지 마세요.
```

### 5.3 사용자 프롬프트 템플릿 (전문 — 생략 없음)

```text
[문제] {problem.title}

[세션 개요]
총 소요 {total_ms/60000:.1f}분, 제출 {제출 수}회 ({verdict_seq " → " 연결}), 언어 {lang}

[인사이트 — 이 사용자의 이번 세션 분석 결과]
{각 인사이트마다:}
- ({category}/{stage}, severity={severity}) {logic_label}
  구간: 세션 {t_start_ms/60000:.0f}분~{t_end_ms/60000:.0f}분 지점, {duration_ms/1000:.0f}초 소요
  관찰: {description}
  {advice가 있으면} 개선 후보: {advice}

[비교군 — 이 문제를 푼 다른 사용자 {N}명 기준]
{각 노출 대상 stage마다:}
- {stage_한국어}({대표 logic_label 최빈값}): 소요 시간 p25={..}초, 중앙값={..}초, p75={..}초
  / 이번 세션 {본인 값}초 {"[표본 적음]" if n<30}
  (이 문제 응시자의 {발생률:.0%}가 같은 단계에서 막힘)
- 전체 풀이 시간: 중앙값 {..}분 / 이번 세션 {..}분
- 제출 횟수: 중앙값 {..}회 / 이번 세션 {..}회
{비교군 표본이 stage 기준 모두 n<5이면 이 블록 전체를 생략하고 아래 줄로 대체:}
[비교군] 아직 이 문제의 비교 데이터가 충분하지 않습니다. 비교 언급 없이 본인 기록만 서술하세요.

{본인 값이 중앙값보다 나쁜 지표가 있고 과거 세션이 있으면 (기존 연구 5 자기참조 규약):}
[자기 참조] 최근 {n}회 세션 평균 풀이 시간 {..}분 대비 이번 세션 {..}분 ({감소|증가|비슷})

위 데이터로 피드백을 작성하세요.
```

### 5.4 grounding·폴백

- 기존 `verify_grounding`(숫자 근거 검증) 재사용: 최대 2회 재시도, 실패 시 템플릿 피드백.
- 템플릿 피드백(`build_template_feedback` 재작성): 인사이트를 severity 순으로 최대 3개 뽑아
  "{logic_label} 구간에서 {duration}초를 사용하셨습니다" 형태로 기계 생성.
- 인사이트가 0개(분석기 실패/pending)면 세그먼트 통계만으로 템플릿 생성.

---

## 6. API 변경(./docs/api-spec.md에도 반영 필요)

| 엔드포인트 | 변경 |
|---|---|
| `GET /sessions/{sid}/timeline` | 신규 — code_commits + session_segments 반환 (프론트 타임라인 UI용) |
| `GET /sessions/{sid}/insights` | 신규 — 본인 세션의 valid 인사이트 |
| `POST /feedback` | 내부 재작성 (§5). 응답 스키마 불변 |
| `GET /sessions/{sid}/analysis`, `/ast-evolution` | 유지(과거 세션), 신규 세션은 404 또는 빈 결과 |
| `GET /problems/{pid}/baseline` | 내부를 cohort 집계(§5.1)로 교체 |

## 7. 재현성·마이그레이션·순서

- `TIMELINE_VERSION`(정수, Stage A 규칙 변경 시 증가), `ANALYZER_VERSION`(문자열,
  프롬프트/모델 변경 시 증가). 백필: raw blob(`write_raw_blob` 산출)이 남아 있으므로
  버전 증가 시 raw 재생으로 code_commits 재계산 가능. 인사이트 재분류는
  `analyzer_version` 기준 백필 (기존 `backfill.py` 패턴 재사용).
- LLM 호출은 temp=0 + seed 고정 + json_mode — 같은 입력이면 같은 인사이트 (§4 결정성).
- 구현 순서:
  1. `schema/timeline.py` + `worker/timeline.py` (Stage A) + 단위 테스트
     (EditOp 시퀀스 → 커밋/세그먼트 스냅샷 테스트, 기존 test_worker.py 스타일)
  2. `model/timeline.py`, `model/insight.py` + `store.py`에 `save_timeline()` 추가
  3. `consumer.py`의 finalize 경로 교체 (analyze_session → build_timeline,
     classify_unmatched → analyze_session_llm)
  4. `llm/session_analyzer.py` (Stage B) + 검증 테스트 (모의 LLM)
  5. `util/cohort.py` + `api/feedback.py` 재작성 (Stage C)
  6. 타임라인 조회 API + 프론트
- 롤아웃: 신규 세션부터 적용. 비교군 표본이 쌓이기 전(n<5)에는 §5.3 폴백 문구로 동작하므로
  콜드스타트에 별도 합성 데이터가 필요 없다 (기존 synthetic baseline 의존 제거).

## 8. 메트릭 (기존 M 시리즈 대체)

- M1': 재생 검증 — 최종 스냅샷 == 클라이언트 최종 코드 바이트 일치율
- M2': 커밋 분포 — 세션당 커밋 수, 세그먼트 라벨 비율
- M3': 인사이트 discard율 (grounding/enum/시간 검증 실패 비율) — 프롬프트 품질 지표
- M4': 피드백 rating(up/down)과 category별 상관
