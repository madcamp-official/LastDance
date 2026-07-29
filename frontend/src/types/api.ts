// docs/api-spec.md 에 명시된 "확정" 범위만 정의한다. 확장 예정 필드는 추가하지 않는다.

export interface ApiErrorBody {
  error: {
    code: string
    message: string
  }
}

// ── 인증 ────────────────────────────────────────────────

export interface SignupRequest {
  email: string
  nickname: string
  password: string
}

export interface UserSummary {
  user_id: string
  nickname: string
  email: string
  profile_img: string | null
}

export interface LoginRequest {
  email: string
  password: string
}

export interface AuthTokens {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface RefreshedToken {
  access_token: string
  token_type: string
}

export interface CurrentUser extends UserSummary {
  created_at: string
}

// ── 문제 카탈로그 ────────────────────────────────────────
// docs/api-spec-additions.md 요청 1: difficulty(A=쉬움~G=어려움), solved_at(인증 유저 기준) — 아직 미구현.

export type DifficultyTier = 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G'

export interface ProblemListItem {
  problem_id: number
  title: string
  difficulty: DifficultyTier | null
  solved_at: string | null
}

export interface ProblemListResponse {
  items: ProblemListItem[]
  page: number
  page_size: number
  total_count: number
}

export interface ProblemExample {
  input: string
  output: string
}

export interface ProblemDetail {
  problem_id: number
  title: string
  statement: string
  constraints: string | null
  examples: ProblemExample[]
  source: string
  difficulty: DifficultyTier | null
  // docs/api-spec.md: GET /problems/{id}에만 추가된 필드 — 배포된 백엔드는 아직 내려주지 않음(optional로 방어).
  time_limit?: string | null
  memory_limit?: string | null
}

// ── 세션 ────────────────────────────────────────────────
// 백엔드 응답 3종(POST/GET/PATCH)이 서로 다른 필드 구성이라 각각 별도 타입으로 둔다
// (backend/app/schema/session.py 기준 — 하나의 Session 타입으로 억지로 합치지 않음).

export type SessionStatus = 'active' | 'solved' | 'abandoned'

export interface CreateSessionRequest {
  problem_id: number
  // 세션 시작 시점엔 언어를 받지 않는다 — "언어는 문제를 보고 택할 수 있어야 하므로 요청 body에서 제거"(api-spec.md)
}

/** POST /sessions 응답 — 세션 식별자 + 문제 상세가 함께 온다. status/started_at 등은 없음. */
export interface SessionStartResult {
  session_id: string
  problem_id: number
  user_id: string
  title: string
  statement: string
  constraints: string | null
  examples: ProblemExample[]
  source: string | null
}

/** GET /sessions/{id} 응답 */
export interface SessionDetail {
  session_id: string
  user_id: string
  problem_id: number
  language: string | null
  started_at: string
  ended_at: string | null
  status: SessionStatus
}

export interface PatchSessionRequest {
  status: Exclude<SessionStatus, 'active'>
  language?: string
}

/** PATCH /sessions/{id} 응답 — session_id/status만 온다(started_at 등은 별도 GET 필요). */
export interface SessionEndResult {
  session_id: string
  status: string
}

// ── 유저 기준 세션 목록 (docs/api-spec-additions.md 요청 2, 아직 미구현) ──────

export interface UserSessionListItem {
  session_id: string
  problem_id: number
  problem_title: string
  difficulty: DifficultyTier | null
  language: string | null
  status: SessionStatus
  started_at: string
  ended_at: string | null
  latest_verdict: SubmissionVerdict
  latest_submitted_at: string | null
}

export interface UserSessionListResponse {
  items: UserSessionListItem[]
  page: number
  page_size: number
  total_count: number
}

export interface ListMineSessionsParams {
  problem_id?: number
  status?: SessionStatus
  page?: number
  page_size?: number
}

// ── 실시간 이벤트 수집 (Ingest Gateway, /ws/events, /events/beacon) ──────
// docs/api-spec.md "실시간 이벤트 수집 (Ingest Gateway)" 절 / backend/app/schema/ingest.py, analysis.py 기준.

/** ops 배열 원소. backend/app/schema/analysis.py의 EditOp와 동일. */
export interface EditOp {
  t: number // 세션 시작 기준 상대 ms
  op: 0 | 1 // 0 = insert, 1 = delete
  pos: number // 코드포인트 기준 절대 오프셋
  len?: number // delete 길이 (기본 0)
  txt?: string // insert 문자열 (기본 "")
  src?: 'user' | 'autoindent' | 'autocomplete' // 기본 "user"
}

export interface SessionStartMessage {
  type: 'session.start'
  sid: string
  problem_id: number
  lang: string
  client_ts: number
  editor: string
  initial_code: string
}

export interface EditBatchMessage {
  type: 'edit.batch'
  sid: string
  seq: number
  base_t: number
  ops: EditOp[]
}

export interface HeartbeatMessage {
  type: 'session.heartbeat'
  sid: string
  t: number
  cursor: number
}

export interface SubmissionMarkMessage {
  type: 'submission.mark'
  sid: string
  t: number
  submission_id: string
}

export type SessionEndReason = 'submitted_ac' | 'closed' | 'timeout'

export interface SessionEndMessage {
  type: 'session.end'
  sid: string
  t: number
  reason: SessionEndReason
}

export type ClientIngestMessage =
  | SessionStartMessage
  | EditBatchMessage
  | HeartbeatMessage
  | SubmissionMarkMessage
  | SessionEndMessage

export interface AckMessage {
  type: 'ack'
  sid: string
  seq: number
}

export interface ResumeMessage {
  type: 'resume'
  sid: string
  last_seq: number
}

export interface IngestErrorMessage {
  type: 'error'
  sid: string
  code: string
  seq: number | null
}

export type ServerIngestMessage = AckMessage | ResumeMessage | IngestErrorMessage

/** POST /events/beacon 요청 본문 (session_id/token은 query string) */
export interface BeaconRequest {
  seq: number
  base_t: number
  ops: EditOp[]
}

// ── 키스트로크 분석 (GET /sessions/{id}/analysis) ─────────────────────
// backend/app/schema/analysis.py 기준.

export type AnalysisLevel = 'full' | 'timing_only' | 'degraded'
export type AnalysisPhase = 'SETUP' | 'FORMATION' | 'DEBUG' | 'REFINE'
export type AstLabel =
  | 'INTERFACE_DESIGN'
  | 'LOOP_BOUNDARY'
  | 'BRANCH_CONDITION'
  | 'INDEX_REASONING'
  | 'ALGORITHM_ENTRY'
  | 'SETUP'
  | 'SYNTAX_STRUGGLE'
  | ''
export type PivotType = 'APPROACH_SWITCH' | 'COMPLEXITY_FIX' | 'EDGE_CASE_FIX' | 'OTHER' | ''
export type StructurePattern =
  | 'BFS'
  | 'DFS_RECURSIVE'
  | 'DFS_ITERATIVE'
  | 'BINARY_SEARCH'
  | 'DP'
  | 'GREEDY'
  | 'DSU'

export interface PausePoint {
  event_index: number // 파생 테이블에는 원본 이벤트 미보관 — 항상 -1
  t_ms: number
  duration_ms: number
  ast_label: AstLabel
  pattern: string
  phase: AnalysisPhase | ''
}

export interface PivotPoint {
  start_index: number // 항상 -1
  end_index: number // 항상 -1
  rewrite_horizon: number // 항상 -1
  t_ms: number
  deleted_chars: number
  pivot_type: PivotType
  pattern: string
}

export interface PatternWindow {
  pattern: string
  t_start_ms: number
  t_complete_ms: number
  formation_ms: number
  pause_ms_in_window: number
  pivot_count_in_window: number
}

export interface AnalysisResult {
  analysis_level: AnalysisLevel
  matcher_version: number
  total_ms: number
  setup_ms: number
  formation_ms: number
  debug_ms: number
  refine_ms: number
  keystroke_count: number
  pause_total_ms: number
  pause_count: number
  pivot_count: number
  code_bytes: number
  final_code: string // 조회 API에서는 항상 ""
  pauses: PausePoint[]
  pivots: PivotPoint[]
  pattern_windows: PatternWindow[]
  patterns_detected: string[]
}

export interface AnalyzeResponse {
  session_id: string
  result: AnalysisResult
}

export interface AnalysisProcessing {
  status: 'processing'
}

/** GET /sessions/{id}/analysis: 200(완료)/202(처리 중)을 하나의 유니온으로 표현. 404는 ApiError로 던져진다. */
export type AnalysisPollResult = AnalyzeResponse | AnalysisProcessing

// ── 제출 ────────────────────────────────────────────────

export type SubmissionStatus = 'pending' | 'judged'
export type SubmissionVerdict = 'AC' | 'WA' | 'TLE' | 'RE' | 'CE' | null

export interface CreateSubmissionRequest {
  session_id: string
  problem_id: number
  code: string
  language: string
}

export interface CreateSubmissionResponse {
  submission_id: string
  status: SubmissionStatus
  submitted_at: string
}

export interface Submission {
  submission_id: string
  status: SubmissionStatus
  verdict: SubmissionVerdict
  runtime_ms: number | null
  memory_kb: number | null
  submitted_at: string
  // docs/api-spec-additions.md 요청 3 — 아직 미구현. 반영 전 실서버 응답엔 필드 자체가 없을 수
  // 있으니(런타임 undefined), 소비하는 쪽에서 `code ?? ''`로 방어할 것.
  code: string
}

export interface SubmissionListItem {
  submission_id: string
  status: SubmissionStatus
  verdict: SubmissionVerdict
  submitted_at: string
}

export interface SubmissionListResponse {
  items: SubmissionListItem[]
}

// ── 피드백 ──────────────────────────────────────────────

export interface Feedback {
  feedback_id: string
  text: string
  model_used: string
  generated_at: string
}

export type FeedbackRating = 'up' | 'down'

export interface FeedbackRatingResponse {
  feedback_id: string
  rating: FeedbackRating
}
