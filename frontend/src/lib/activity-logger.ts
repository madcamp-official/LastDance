import type { ActivityEvent, EventsBatch } from '../types/api'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

function isAbsoluteUrl(url: string) {
  return /^https?:\/\//.test(url)
}

function buildWsUrl(path: string): string {
  if (isAbsoluteUrl(API_BASE)) {
    return `${API_BASE.replace(/^http/, 'ws')}${path}`
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}${API_BASE}${path}`
}

function buildHttpUrl(path: string): string {
  return `${API_BASE}${path}`
}

export interface ActivityLoggerOptions {
  sessionId: string
  accessToken: string
  flushIntervalMs?: number
}

/**
 * 세션 동안의 행동 이벤트를 배치로 모아 WS(/ws/events)로 전송하고,
 * 연결 실패나 탭 종료/숨김 시 POST /events/beacon(sendBeacon)으로 폴백한다.
 * event.type/payload는 docs/api-spec.md 기준 자유 스키마 — 여기서 필드를 확정하지 않는다.
 */
export class ActivityLogger {
  private readonly sessionId: string
  private readonly accessToken: string
  private readonly flushIntervalMs: number
  private queue: ActivityEvent[] = []
  private socket: WebSocket | null = null
  private flushTimer: number | null = null
  private destroyed = false

  constructor(options: ActivityLoggerOptions) {
    this.sessionId = options.sessionId
    this.accessToken = options.accessToken
    this.flushIntervalMs = options.flushIntervalMs ?? 3000

    this.connectSocket()
    this.flushTimer = window.setInterval(() => this.flush(), this.flushIntervalMs)
    window.addEventListener('beforeunload', this.handleUnload)
    document.addEventListener('visibilitychange', this.handleVisibilityChange)
  }

  log(type: string, payload: Record<string, unknown> = {}) {
    if (this.destroyed) return
    this.queue.push({ type, payload, ts: Date.now() })
  }

  destroy() {
    if (this.destroyed) return
    this.destroyed = true
    this.flush(true)
    if (this.flushTimer !== null) window.clearInterval(this.flushTimer)
    this.socket?.close()
    window.removeEventListener('beforeunload', this.handleUnload)
    document.removeEventListener('visibilitychange', this.handleVisibilityChange)
  }

  private connectSocket() {
    try {
      const url = buildWsUrl(
        `/ws/events?session_id=${encodeURIComponent(this.sessionId)}&token=${encodeURIComponent(this.accessToken)}`,
      )
      const socket = new WebSocket(url)
      socket.addEventListener('error', () => {
        // 연결 실패는 flush()의 beacon 폴백으로 자연스럽게 처리된다.
      })
      this.socket = socket
    } catch {
      this.socket = null
    }
  }

  private flush(forceBeacon = false) {
    if (this.queue.length === 0) return
    const events = this.queue
    this.queue = []
    const batch: EventsBatch = { session_id: this.sessionId, events }

    if (!forceBeacon && this.socket?.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(JSON.stringify(batch))
        return
      } catch {
        // fallthrough: beacon 폴백
      }
    }

    const url = buildHttpUrl('/events/beacon')
    const body = JSON.stringify(batch)
    const sent =
      typeof navigator.sendBeacon === 'function' &&
      navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))
    if (!sent) {
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
      }).catch(() => {
        // 탭 종료 등으로 이미 실패한 전송은 재시도하지 않는다 — 유실 허용.
      })
    }
  }

  private handleUnload = () => {
    this.flush(true)
  }

  private handleVisibilityChange = () => {
    if (document.visibilityState === 'hidden') {
      this.flush(true)
    }
  }
}
