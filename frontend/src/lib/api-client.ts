import type {
  AuthTokens,
  CreateSessionRequest,
  CreateSubmissionRequest,
  CreateSubmissionResponse,
  CurrentUser,
  Feedback,
  FeedbackRating,
  FeedbackRatingResponse,
  LoginRequest,
  PatchSessionRequest,
  ProblemDetail,
  ProblemListResponse,
  RefreshedToken,
  Session,
  SignupRequest,
  Submission,
  SubmissionListResponse,
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
}

/**
 * docs/api-spec.md 의 "확정" 엔드포인트만 포함한다.
 * Stats(비교 통계)는 문서상 미구현 예약 엔드포인트라 인터페이스에 넣지 않는다 — 화면은 정적 mock으로 처리.
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
    create(req: CreateSessionRequest): Promise<Session>
    patch(sessionId: string, req: PatchSessionRequest): Promise<Session>
    get(sessionId: string): Promise<Session>
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
  }
}
