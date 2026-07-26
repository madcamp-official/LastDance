import type { IApiClient } from './api-client'
import { createMockApiClient } from './mock-api-client'
import { createHttpApiClient } from './http-api-client'
import type { RefreshedToken } from '../types/api'

interface AuthBridge {
  getRefreshToken: () => string | null
  onTokenRefreshed: (tokens: RefreshedToken) => void
  onRefreshFailed: () => void
}

// authStore가 초기화되며 configureAuthBridge()로 실제 콜백을 채워 넣는다.
// authStore.ts -> lib/api.ts 순방향 의존만 두어 순환 참조를 피한다.
let authBridge: AuthBridge = {
  getRefreshToken: () => null,
  onTokenRefreshed: () => {},
  onRefreshFailed: () => {},
}

export function configureAuthBridge(bridge: AuthBridge) {
  authBridge = bridge
}

const useMockApi = import.meta.env.VITE_USE_MOCK_API !== 'false'

export const apiClient: IApiClient = useMockApi
  ? createMockApiClient()
  : createHttpApiClient({
      baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
      getRefreshToken: () => authBridge.getRefreshToken(),
      onTokenRefreshed: (tokens) => authBridge.onTokenRefreshed(tokens),
      onRefreshFailed: () => authBridge.onRefreshFailed(),
    })
