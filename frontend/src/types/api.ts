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

export interface ProblemListItem {
  problem_id: number
  title: string
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
}

// ── 세션 ────────────────────────────────────────────────

export type SessionStatus = 'active' | 'solved' | 'unsolved' | 'abandoned'

export interface CreateSessionRequest {
  problem_id: number
  language: string
}

export interface Session {
  session_id: string
  user_id: string
  problem_id: number
  language: string
  started_at: string
  ended_at: string | null
  status: SessionStatus
}

export interface PatchSessionRequest {
  status: Exclude<SessionStatus, 'active'>
  ended_at?: string
}

// ── 실시간 이벤트 ────────────────────────────────────────

export interface ActivityEvent {
  type: string
  payload: Record<string, unknown>
  ts: number
}

export interface EventsBatch {
  session_id: string
  events: ActivityEvent[]
}

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
