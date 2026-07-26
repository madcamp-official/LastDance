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
import { ApiError, type IApiClient, type ListProblemsParams } from './api-client'

interface StoredUser extends UserSummary {
  password: string
  created_at: string
}

interface StoredProblem extends ProblemDetail {}

interface StoredSession extends Session {}

interface StoredSubmission extends Submission {
  session_id: string
  problem_id: number
}

interface StoredFeedback extends Feedback {
  session_id: string
}

interface MockDb {
  users: StoredUser[]
  problems: StoredProblem[]
  sessions: StoredSession[]
  submissions: StoredSubmission[]
  feedback: StoredFeedback[]
  // refresh_token -> user_id
  refreshTokens: Record<string, string>
  // access_token -> user_id — localStorage에 함께 두어 새로고침 후에도 로그인 상태 유지
  accessTokens: Record<string, string>
}

const STORAGE_KEY = 'lastdance_mock_db_v1'

// 실제 CodeNet 문제 지문을 사용하지 않는, UI 개발 전용 placeholder 데이터.
// source를 'mock_local'로 두어 확정된 'codenet_atcoder' 값과 구분한다.
function seedDb(): MockDb {
  return {
    users: [],
    problems: [
      {
        problem_id: 1,
        title: '[mock] 세 정수의 합',
        statement: '세 정수 a, b, c 가 주어질 때 그 합을 출력하시오.',
        constraints: '1 <= a, b, c <= 1000',
        examples: [{ input: '1 2 3', output: '6' }],
        source: 'mock_local',
      },
      {
        problem_id: 2,
        title: '[mock] 문자열 뒤집기',
        statement: '문자열 s가 주어질 때 이를 뒤집어 출력하시오.',
        constraints: '1 <= |s| <= 100',
        examples: [{ input: 'hello', output: 'olleh' }],
        source: 'mock_local',
      },
    ],
    sessions: [],
    submissions: [],
    feedback: [],
    refreshTokens: {},
    accessTokens: {},
  }
}

function loadDb(): MockDb {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) {
    const seeded = seedDb()
    localStorage.setItem(STORAGE_KEY, JSON.stringify(seeded))
    return seeded
  }
  return JSON.parse(raw) as MockDb
}

function saveDb(db: MockDb) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(db))
}

function genId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID()}`
}

function genToken(): string {
  return `mock.${crypto.randomUUID()}`
}

export function createMockApiClient(): IApiClient {
  const db = loadDb()
  // 로그인 이후 세팅되는 현재 access token — 실제 클라이언트의 Authorization 헤더 부착을 모사.
  let currentAccessToken: string | null = null

  function persist() {
    saveDb(db)
  }

  function requireCurrentUserId(): string {
    const userId = currentAccessToken && db.accessTokens[currentAccessToken]
    if (!userId) {
      throw new ApiError(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    }
    return userId
  }

  return {
    setAccessToken(token: string | null) {
      currentAccessToken = token
    },

    auth: {
      async signup(req: SignupRequest): Promise<UserSummary> {
        if (db.users.some((u) => u.email === req.email)) {
          throw new ApiError(409, 'EMAIL_TAKEN', '이미 가입된 이메일입니다.')
        }
        const user: StoredUser = {
          user_id: genId('u'),
          email: req.email,
          nickname: req.nickname,
          profile_img: null,
          password: req.password,
          created_at: new Date().toISOString(),
        }
        db.users.push(user)
        persist()
        const { password: _password, created_at: _createdAt, ...summary } = user
        return summary
      },

      async login(req: LoginRequest): Promise<AuthTokens> {
        const user = db.users.find(
          (u) => u.email === req.email && u.password === req.password,
        )
        if (!user) {
          throw new ApiError(
            401,
            'INVALID_CREDENTIALS',
            '이메일 또는 비밀번호가 올바르지 않습니다.',
          )
        }
        const access_token = genToken()
        const refresh_token = genToken()
        db.accessTokens[access_token] = user.user_id
        db.refreshTokens[refresh_token] = user.user_id
        persist()
        return { access_token, refresh_token, token_type: 'bearer' }
      },

      async refresh(refreshToken: string): Promise<RefreshedToken> {
        const userId = db.refreshTokens[refreshToken]
        if (!userId) {
          throw new ApiError(
            401,
            'INVALID_REFRESH_TOKEN',
            '유효하지 않거나 만료된 토큰입니다.',
          )
        }
        const access_token = genToken()
        db.accessTokens[access_token] = userId
        persist()
        return { access_token, token_type: 'bearer' }
      },

      async logout(refreshToken: string): Promise<{ message: string }> {
        delete db.refreshTokens[refreshToken]
        persist()
        return { message: '로그아웃 하였습니다.' }
      },

      async me(): Promise<CurrentUser> {
        const userId = requireCurrentUserId()
        const user = db.users.find((u) => u.user_id === userId)
        if (!user) {
          throw new ApiError(401, 'UNAUTHORIZED', '인증이 필요합니다.')
        }
        const { password: _password, ...rest } = user
        return rest
      },
    },

    problems: {
      async list(params?: ListProblemsParams): Promise<ProblemListResponse> {
        const page = params?.page ?? 1
        const pageSize = params?.page_size ?? 20
        const start = (page - 1) * pageSize
        const items = db.problems
          .slice(start, start + pageSize)
          .map(({ problem_id, title }) => ({ problem_id, title }))
        return {
          items,
          page,
          page_size: pageSize,
          total_count: db.problems.length,
        }
      },

      async get(problemId: number): Promise<ProblemDetail> {
        const problem = db.problems.find((p) => p.problem_id === problemId)
        if (!problem) {
          throw new ApiError(404, 'PROBLEM_NOT_FOUND', '문제를 찾을 수 없습니다.')
        }
        return problem
      },
    },

    sessions: {
      async create(req: CreateSessionRequest): Promise<Session> {
        const userId = requireCurrentUserId()
        const session: StoredSession = {
          session_id: genId('s'),
          user_id: userId,
          problem_id: req.problem_id,
          language: req.language,
          started_at: new Date().toISOString(),
          ended_at: null,
          status: 'active',
        }
        db.sessions.push(session)
        persist()
        return session
      },

      async patch(sessionId: string, req: PatchSessionRequest): Promise<Session> {
        const session = db.sessions.find((s) => s.session_id === sessionId)
        if (!session) {
          throw new ApiError(404, 'SESSION_NOT_FOUND', '세션을 찾을 수 없습니다.')
        }
        session.status = req.status
        session.ended_at = req.ended_at ?? new Date().toISOString()
        persist()
        return session
      },

      async get(sessionId: string): Promise<Session> {
        const session = db.sessions.find((s) => s.session_id === sessionId)
        if (!session) {
          throw new ApiError(404, 'SESSION_NOT_FOUND', '세션을 찾을 수 없습니다.')
        }
        return session
      },
    },

    submissions: {
      async create(
        req: CreateSubmissionRequest,
      ): Promise<CreateSubmissionResponse> {
        const submission: StoredSubmission = {
          submission_id: genId('sub'),
          session_id: req.session_id,
          problem_id: req.problem_id,
          status: 'pending',
          verdict: null,
          runtime_ms: null,
          memory_kb: null,
          submitted_at: new Date().toISOString(),
        }
        db.submissions.push(submission)
        persist()
        const { submission_id, status, submitted_at } = submission
        return { submission_id, status, submitted_at }
      },

      async get(submissionId: string): Promise<Submission> {
        const submission = db.submissions.find(
          (s) => s.submission_id === submissionId,
        )
        if (!submission) {
          throw new ApiError(
            404,
            'SUBMISSION_NOT_FOUND',
            '제출을 찾을 수 없습니다.',
          )
        }
        const { session_id: _sessionId, problem_id: _problemId, ...rest } =
          submission
        return rest
      },

      async listBySession(sessionId: string): Promise<SubmissionListResponse> {
        const items = db.submissions
          .filter((s) => s.session_id === sessionId)
          .map(({ submission_id, status, verdict, submitted_at }) => ({
            submission_id,
            status,
            verdict,
            submitted_at,
          }))
        return { items }
      },
    },

    feedback: {
      async create(sessionId: string): Promise<Feedback> {
        // docs/api-spec.md: 팀A 프롬프트 설계 완료 전까지 mock 고정 문구
        const feedback: StoredFeedback = {
          feedback_id: genId('f'),
          session_id: sessionId,
          text: '(mock) 아직 준비 중인 피드백입니다.',
          model_used: 'qwen2.5-coder:7b',
          generated_at: new Date().toISOString(),
        }
        db.feedback.push(feedback)
        persist()
        const { session_id: _sessionId, ...rest } = feedback
        return rest
      },

      async rate(
        feedbackId: string,
        rating: FeedbackRating,
      ): Promise<FeedbackRatingResponse> {
        const feedback = db.feedback.find((f) => f.feedback_id === feedbackId)
        if (!feedback) {
          throw new ApiError(404, 'FEEDBACK_NOT_FOUND', '피드백을 찾을 수 없습니다.')
        }
        return { feedback_id: feedbackId, rating }
      },
    },
  }
}
