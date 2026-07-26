import { create } from 'zustand'
import { apiClient, configureAuthBridge } from '../lib/api'
import { ApiError } from '../lib/api-client'
import type { CurrentUser, LoginRequest, SignupRequest } from '../types/api'

const ACCESS_TOKEN_KEY = 'lastdance_access_token'
const REFRESH_TOKEN_KEY = 'lastdance_refresh_token'

type AuthStatus = 'idle' | 'authenticated' | 'unauthenticated'

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  user: CurrentUser | null
  status: AuthStatus
  signup(req: SignupRequest): Promise<void>
  login(req: LoginRequest): Promise<void>
  logout(): Promise<void>
  hydrate(): Promise<void>
}

function persistTokens(accessToken: string | null, refreshToken: string | null) {
  if (accessToken) {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
  } else {
    localStorage.removeItem(ACCESS_TOKEN_KEY)
  }
  if (refreshToken) {
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
  } else {
    localStorage.removeItem(REFRESH_TOKEN_KEY)
  }
}

export const useAuthStore = create<AuthState>((set, get) => {
  configureAuthBridge({
    getRefreshToken: () => get().refreshToken,
    onTokenRefreshed: (tokens) => {
      persistTokens(tokens.access_token, get().refreshToken)
      set({ accessToken: tokens.access_token })
    },
    onRefreshFailed: () => {
      persistTokens(null, null)
      apiClient.setAccessToken(null)
      set({
        accessToken: null,
        refreshToken: null,
        user: null,
        status: 'unauthenticated',
      })
    },
  })

  return {
    accessToken: null,
    refreshToken: null,
    user: null,
    status: 'idle',

    async signup(req) {
      await apiClient.auth.signup(req)
    },

    async login(req) {
      const tokens = await apiClient.auth.login(req)
      apiClient.setAccessToken(tokens.access_token)
      persistTokens(tokens.access_token, tokens.refresh_token)
      set({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        status: 'authenticated',
      })
      // 사용자 정보 조회가 실패해도(네트워크 등) 로그인 자체는 이미 유효하므로 막지 않는다.
      try {
        const user = await apiClient.auth.me()
        set({ user })
      } catch {
        // user는 null로 남고, 다음 me() 성공 시 채워진다.
      }
    },

    async logout() {
      const refreshToken = get().refreshToken
      if (refreshToken) {
        try {
          await apiClient.auth.logout(refreshToken)
        } catch {
          // 서버 로그아웃 실패해도 클라이언트 상태는 정리한다.
        }
      }
      apiClient.setAccessToken(null)
      persistTokens(null, null)
      set({ accessToken: null, refreshToken: null, user: null, status: 'unauthenticated' })
    },

    async hydrate() {
      const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY)
      const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
      if (!accessToken || !refreshToken) {
        set({ status: 'unauthenticated' })
        return
      }
      apiClient.setAccessToken(accessToken)
      // access token 자체의 유효성은 아직 모르므로 우선 authenticated로 낙관적 진입.
      // 사용자 정보 조회가 401을 반환할 때만(= 토큰이 실제로 무효) 세션을 정리한다.
      // 그 외 에러(네트워크 등)는 세션을 건드리지 않는다.
      set({ accessToken, refreshToken, status: 'authenticated' })
      try {
        const user = await apiClient.auth.me()
        set({ user })
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          apiClient.setAccessToken(null)
          persistTokens(null, null)
          set({
            accessToken: null,
            refreshToken: null,
            user: null,
            status: 'unauthenticated',
          })
        }
      }
    },
  }
})
