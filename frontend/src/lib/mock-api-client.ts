import type {
  AnalysisPollResult,
  AnalysisResult,
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
  SessionDetail,
  SessionEndResult,
  SessionStartResult,
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

interface StoredSession extends SessionDetail {}

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
      async create(req: CreateSessionRequest): Promise<SessionStartResult> {
        const userId = requireCurrentUserId()
        const problem = db.problems.find((p) => p.problem_id === req.problem_id)
        if (!problem) {
          throw new ApiError(404, 'PROBLEM_NOT_FOUND', '문제를 찾을 수 없습니다.')
        }
        const session: StoredSession = {
          session_id: genId('s'),
          user_id: userId,
          problem_id: req.problem_id,
          language: null,
          started_at: new Date().toISOString(),
          ended_at: null,
          status: 'active',
        }
        db.sessions.push(session)
        persist()
        return {
          session_id: session.session_id,
          problem_id: problem.problem_id,
          user_id: userId,
          title: problem.title,
          statement: problem.statement,
          constraints: problem.constraints,
          examples: problem.examples,
          source: problem.source,
        }
      },

      async patch(sessionId: string, req: PatchSessionRequest): Promise<SessionEndResult> {
        const session = db.sessions.find((s) => s.session_id === sessionId)
        if (!session) {
          throw new ApiError(404, 'SESSION_NOT_FOUND', '세션을 찾을 수 없습니다.')
        }
        if (session.status !== 'active') {
          throw new ApiError(409, 'SESSION_ALREADY_ENDED', '이미 종료된 세션입니다.')
        }
        session.status = req.status
        if (req.language) session.language = req.language
        session.ended_at = new Date().toISOString()
        persist()
        return { session_id: session.session_id, status: session.status }
      },

      async get(sessionId: string): Promise<SessionDetail> {
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
        // 실제 백엔드(Judge0 경유)는 동기 채점이라 항상 status: "judged"로 응답한다.
        // mock도 동일한 흐름을 재현하기 위해 즉석에서 verdict를 합성한다(실제 실행은 하지 않음).
        const verdict = Math.random() < 0.6 ? 'AC' : (['WA', 'RE', 'TLE'] as const)[Math.floor(Math.random() * 3)]
        const submission: StoredSubmission = {
          submission_id: genId('sub'),
          session_id: req.session_id,
          problem_id: req.problem_id,
          status: 'judged',
          verdict,
          runtime_ms: verdict === 'AC' ? Math.round(50 + Math.random() * 400) : null,
          memory_kb: verdict === 'AC' ? Math.round(8000 + Math.random() * 4000) : null,
          submitted_at: new Date().toISOString(),
        }
        db.submissions.push(submission)

        if (verdict === 'AC') {
          const session = db.sessions.find((s) => s.session_id === req.session_id)
          if (session && session.status === 'active') {
            session.status = 'solved'
            session.language = req.language
            session.ended_at = submission.submitted_at
          }
        }
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
          model_used: 'qwen3-coder:30b-a3b',
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

    analysis: {
      async get(sessionId: string): Promise<AnalysisPollResult> {
        const session = db.sessions.find((s) => s.session_id === sessionId)
        if (!session) {
          throw new ApiError(404, 'ANALYSIS_NOT_FOUND', '분석 결과가 없습니다.')
        }
        // 실제 Replay Worker는 session.end 수신 후에만 비동기로 결과를 채운다.
        // mock은 워커가 없으므로, 세션이 아직 진행 중이면 "처리 중"으로 흉내만 낸다.
        if (session.status === 'active') {
          return { status: 'processing' }
        }
        return { session_id: sessionId, result: synthesizeMockAnalysis(sessionId) }
      },
    },
  }
}

// sessionId를 시드로 한 결정적 pseudo-random — 새로고침해도 같은 세션은 같은 분석 결과를 보여준다.
function seededRandom(seed: string): () => number {
  let h = 0
  for (let i = 0; i < seed.length; i++) {
    h = (Math.imul(31, h) + seed.charCodeAt(i)) | 0
  }
  return () => {
    h = (Math.imul(1103515245, h) + 12345) | 0
    return ((h >>> 1) % 10000) / 10000
  }
}

const MOCK_PATTERNS = ['BFS', 'DFS_ITERATIVE', 'BINARY_SEARCH', 'DP', 'GREEDY'] as const
const MOCK_AST_LABELS = [
  'INTERFACE_DESIGN',
  'LOOP_BOUNDARY',
  'BRANCH_CONDITION',
  'INDEX_REASONING',
  'ALGORITHM_ENTRY',
] as const
const MOCK_PIVOT_TYPES = ['APPROACH_SWITCH', 'COMPLEXITY_FIX', 'EDGE_CASE_FIX'] as const
const MOCK_PHASES = ['SETUP', 'FORMATION', 'DEBUG', 'REFINE'] as const

function synthesizeMockAnalysis(sessionId: string): AnalysisResult {
  const rand = seededRandom(sessionId)
  const pick = <T,>(arr: readonly T[]) => arr[Math.floor(rand() * arr.length)]

  const setupMs = Math.round(10000 + rand() * 20000)
  const formationMs = Math.round(20000 + rand() * 40000)
  const debugMs = Math.round(10000 + rand() * 30000)
  const refineMs = Math.round(5000 + rand() * 15000)
  const totalMs = setupMs + formationMs + debugMs + refineMs

  const pauseCount = 2 + Math.floor(rand() * 4)
  const pauses = Array.from({ length: pauseCount }, (_, i) => {
    const duration_ms = Math.round(1000 + rand() * 8000)
    return {
      event_index: -1,
      t_ms: Math.round((totalMs * (i + 1)) / (pauseCount + 1)),
      duration_ms,
      ast_label: pick(MOCK_AST_LABELS),
      pattern: rand() > 0.4 ? pick(MOCK_PATTERNS) : '',
      phase: pick(MOCK_PHASES),
    }
  })
  const pauseTotalMs = pauses.reduce((sum, p) => sum + p.duration_ms, 0)

  const pivotCount = Math.floor(rand() * 3)
  const pivots = Array.from({ length: pivotCount }, (_, i) => ({
    start_index: -1,
    end_index: -1,
    rewrite_horizon: -1,
    t_ms: Math.round((totalMs * (i + 1)) / (pivotCount + 1)),
    deleted_chars: Math.round(10 + rand() * 100),
    pivot_type: pick(MOCK_PIVOT_TYPES),
    pattern: rand() > 0.5 ? pick(MOCK_PATTERNS) : '',
  }))

  const windowCount = 1 + Math.floor(rand() * 2)
  const usedPatterns = new Set<string>()
  const patternWindows = Array.from({ length: windowCount }, (_, i) => {
    const pattern = pick(MOCK_PATTERNS)
    usedPatterns.add(pattern)
    const t_start_ms = Math.round((totalMs * i) / windowCount)
    const formation_ms = Math.round(5000 + rand() * 15000)
    return {
      pattern,
      t_start_ms,
      t_complete_ms: t_start_ms + formation_ms,
      formation_ms,
      pause_ms_in_window: Math.round(rand() * 3000),
      pivot_count_in_window: Math.floor(rand() * 2),
    }
  })

  return {
    analysis_level: 'full',
    matcher_version: 1,
    total_ms: totalMs,
    setup_ms: setupMs,
    formation_ms: formationMs,
    debug_ms: debugMs,
    refine_ms: refineMs,
    keystroke_count: Math.round(300 + rand() * 900),
    pause_total_ms: pauseTotalMs,
    pause_count: pauseCount,
    pivot_count: pivotCount,
    code_bytes: Math.round(300 + rand() * 800),
    final_code: '',
    pauses,
    pivots,
    pattern_windows: patternWindows,
    patterns_detected: Array.from(usedPatterns).sort(),
  }
}
