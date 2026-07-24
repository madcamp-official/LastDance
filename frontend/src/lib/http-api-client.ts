import type {
  ApiErrorBody,
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

export interface HttpApiClientConfig {
  baseUrl: string
  getRefreshToken: () => string | null
  onTokenRefreshed: (tokens: RefreshedToken) => void
  onRefreshFailed: () => void
}

export function createHttpApiClient(config: HttpApiClientConfig): IApiClient {
  let accessToken: string | null = null

  async function request<T>(
    path: string,
    init: RequestInit = {},
    allowRetry = true,
  ): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Content-Type', 'application/json')
    if (accessToken) {
      headers.set('Authorization', `Bearer ${accessToken}`)
    }

    const res = await fetch(`${config.baseUrl}${path}`, { ...init, headers })

    if (res.status === 204) {
      return undefined as T
    }

    if (res.ok) {
      // 204 외에 본문이 없는 성공 응답은 없다고 가정 (api-spec.md 기준)
      return (await res.json()) as T
    }

    // 문서화된 엔드포인트는 {"error": {code, message}} 포맷이지만, 아직 백엔드에
    // 구현되지 않은 라우트(예: /users/me)는 FastAPI 기본 {"detail": ...} 형태를
    // 반환한다. 두 경우 모두 안전하게 파싱한다.
    const raw = await res.json().catch(() => null)
    const errorBody = raw as ApiErrorBody | { detail?: unknown } | null
    const code =
      errorBody && 'error' in errorBody && errorBody.error
        ? errorBody.error.code
        : `HTTP_${res.status}`
    const message =
      errorBody && 'error' in errorBody && errorBody.error
        ? errorBody.error.message
        : typeof (errorBody as { detail?: unknown })?.detail === 'string'
          ? ((errorBody as { detail: string }).detail)
          : res.statusText || '요청을 처리하지 못했습니다.'

    const isAuthEndpoint = path.startsWith('/auth/')
    if (res.status === 401 && allowRetry && !isAuthEndpoint) {
      const refreshToken = config.getRefreshToken()
      if (refreshToken) {
        try {
          const refreshed = await request<RefreshedToken>('/auth/refresh', {
            method: 'POST',
            body: JSON.stringify({ refresh_token: refreshToken }),
          })
          accessToken = refreshed.access_token
          config.onTokenRefreshed(refreshed)
          return request<T>(path, init, false)
        } catch {
          config.onRefreshFailed()
        }
      } else {
        config.onRefreshFailed()
      }
    }

    throw new ApiError(res.status, code, message)
  }

  function toQuery(params?: Record<string, string | number | undefined>) {
    if (!params) return ''
    const entries = Object.entries(params).filter(([, v]) => v !== undefined)
    if (entries.length === 0) return ''
    return `?${new URLSearchParams(entries as [string, string][]).toString()}`
  }

  return {
    setAccessToken(token: string | null) {
      accessToken = token
    },

    auth: {
      signup(req: SignupRequest) {
        return request<UserSummary>('/auth/signup', {
          method: 'POST',
          body: JSON.stringify(req),
        })
      },
      login(req: LoginRequest) {
        return request<AuthTokens>('/auth/login', {
          method: 'POST',
          body: JSON.stringify(req),
        })
      },
      refresh(refreshToken: string) {
        return request<RefreshedToken>('/auth/refresh', {
          method: 'POST',
          body: JSON.stringify({ refresh_token: refreshToken }),
        })
      },
      logout(refreshToken: string) {
        return request<{ message: string }>('/auth/logout', {
          method: 'POST',
          body: JSON.stringify({ refresh_token: refreshToken }),
        })
      },
      me() {
        return request<CurrentUser>('/users/me')
      },
    },

    problems: {
      list(params?: ListProblemsParams) {
        return request<ProblemListResponse>(
          `/problems${toQuery({ page: params?.page, page_size: params?.page_size })}`,
        )
      },
      get(problemId: string) {
        return request<ProblemDetail>(`/problems/${problemId}`)
      },
    },

    sessions: {
      create(req: CreateSessionRequest) {
        return request<Session>('/sessions', {
          method: 'POST',
          body: JSON.stringify(req),
        })
      },
      patch(sessionId: string, req: PatchSessionRequest) {
        return request<Session>(`/sessions/${sessionId}`, {
          method: 'PATCH',
          body: JSON.stringify(req),
        })
      },
      get(sessionId: string) {
        return request<Session>(`/sessions/${sessionId}`)
      },
    },

    submissions: {
      create(req: CreateSubmissionRequest) {
        return request<CreateSubmissionResponse>('/submissions', {
          method: 'POST',
          body: JSON.stringify(req),
        })
      },
      get(submissionId: string) {
        return request<Submission>(`/submissions/${submissionId}`)
      },
      listBySession(sessionId: string) {
        return request<SubmissionListResponse>(
          `/submissions${toQuery({ session_id: sessionId })}`,
        )
      },
    },

    feedback: {
      create(sessionId: string) {
        return request<Feedback>('/feedback', {
          method: 'POST',
          body: JSON.stringify({ session_id: sessionId }),
        })
      },
      rate(feedbackId: string, rating: FeedbackRating) {
        return request<FeedbackRatingResponse>(
          `/feedback/${feedbackId}/rating`,
          {
            method: 'PATCH',
            body: JSON.stringify({ rating }),
          },
        )
      },
    },
  }
}
