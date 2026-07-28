import type {
  AnalysisPollResult,
  AuthTokens,
  CreateSessionRequest,
  CreateSubmissionRequest,
  CreateSubmissionResponse,
  CurrentUser,
  DifficultyTier,
  Feedback,
  FeedbackRating,
  FeedbackRatingResponse,
  ListMineSessionsParams,
  LoginRequest,
  PatchSessionRequest,
  ProblemDetail,
  ProblemListResponse,
  RefreshedToken,
  SessionDetail,
  SessionEndResult,
  SessionStartResult,
  SignupRequest,
  Submission,
  SubmissionListResponse,
  UserSessionListResponse,
  UserSummary,
} from '../types/api'

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export interface ListProblemsParams {
  page?: number
  page_size?: number
  // docs/api-spec-additions.md 요청 1 — 아직 미구현. 실서버 대상으론 무시되고 기본 정렬/전체 목록이 온다.
  sort?: 'difficulty_asc' | 'difficulty_desc' | 'problem_id'
  difficulty?: DifficultyTier[]
  exclude_solved?: boolean
}

/**
 * docs/api-spec.md 의 "확정" 엔드포인트만 포함한다.
 * Stats(비교 통계)는 문서상 미구현 예약 엔드포인트라 인터페이스에 넣지 않는다 — 화면은 정적 mock으로 처리.
 * sessions.listMine / feedback.getBySession은 docs/api-spec-additions.md에 요청해둔 미구현 엔드포인트다.
 */
export interface IApiClient {
  /** 로그인/토큰 갱신/로그아웃 시 authStore가 호출해, 이후 요청에 실릴 access token을 갱신한다. */
  setAccessToken(token: string | null): void
  auth: {
    signup(req: SignupRequest): Promise<UserSummary>
    login(req: LoginRequest): Promise<AuthTokens>
    refresh(refreshToken: string): Promise<RefreshedToken>
    logout(refreshToken: string): Promise<{ message: string }>
    me(): Promise<CurrentUser>
  }
  problems: {
    list(params?: ListProblemsParams): Promise<ProblemListResponse>
    get(problemId: number): Promise<ProblemDetail>
  }
  sessions: {
    create(req: CreateSessionRequest): Promise<SessionStartResult>
    patch(sessionId: string, req: PatchSessionRequest): Promise<SessionEndResult>
    get(sessionId: string): Promise<SessionDetail>
    /** GET /users/me/sessions (docs/api-spec-additions.md 요청 2, 아직 미구현). */
    listMine(params?: ListMineSessionsParams): Promise<UserSessionListResponse>
  }
  submissions: {
    create(req: CreateSubmissionRequest): Promise<CreateSubmissionResponse>
    get(submissionId: string): Promise<Submission>
    listBySession(sessionId: string): Promise<SubmissionListResponse>
  }
  feedback: {
    create(sessionId: string): Promise<Feedback>
    rate(
      feedbackId: string,
      rating: FeedbackRating,
    ): Promise<FeedbackRatingResponse>
    /** GET /feedback?session_id= (docs/api-spec-additions.md 요청 4, 아직 미구현). 생성이 아니라 조회만. */
    getBySession(sessionId: string): Promise<Feedback | null>
  }
  /** GET /sessions/{id}/analysis. 세션 종료 후에만 의미 있는 데이터가 채워진다. */
  analysis: {
    get(sessionId: string): Promise<AnalysisPollResult>
  }
}
