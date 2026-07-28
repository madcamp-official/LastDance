import type {
  ClientIngestMessage,
  EditBatchMessage,
  EditOp,
  ServerIngestMessage,
  SessionEndReason,
} from '../types/api'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

// dev-plan §2.2 / api-spec.md "실시간 이벤트 수집" 절.
const FLUSH_INTERVAL_MS = 1000
const MAX_BUFFERED_OPS = 200
const HEARTBEAT_INTERVAL_MS = 5000
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 8000]

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

export interface KeystrokeLoggerOptions {
  sessionId: string
  problemId: number
  language: string
  /** 매 (재)연결 시점에 호출 — access token이 그 사이 갱신됐을 수 있어 캐시하지 않는다. */
  getAccessToken: () => string | null
  initialCode?: string
  editorVersion?: string
}

/**
 * Ingest Gateway(`/ws/events`) 클라이언트 — docs/api-spec.md "실시간 이벤트 수집 (Ingest Gateway)" 절
 * (keystroke-analysis-dev-plan.md §2~3) 구현. Monaco 편집 이벤트를 EditOp로 변환해 배치 전송하고,
 * WS 불가/탭 종료 시 `/events/beacon`으로 폴백한다. (sid, seq) 멱등성 계약을 지키기 위해 ack되지
 * 않은 배치는 버퍼에 남겨 재연결 시(`resume`) 재전송한다.
 */
export class KeystrokeLogger {
  private readonly sessionId: string
  private readonly problemId: number
  private readonly language: string
  private readonly getAccessToken: () => string | null
  private readonly editorVersion: string
  private readonly initialCode: string

  private readonly sessionStartedAtMs = performance.now()
  private socket: WebSocket | null = null
  private destroyed = false
  private reconnectAttempt = 0
  private reconnectTimer: number | null = null

  private opBuffer: EditOp[] = []
  private nextSeq = 0
  // seq -> 아직 ack 못 받은 배치. 재연결 시 seq 오름차순으로 재전송한다.
  private readonly unacked = new Map<number, EditBatchMessage>()

  private flushTimer: number | null = null
  private heartbeatTimer: number | null = null
  private cursorOffset = 0
  private sessionStartSent = false
  private ended = false

  constructor(options: KeystrokeLoggerOptions) {
    this.sessionId = options.sessionId
    this.problemId = options.problemId
    this.language = options.language
    this.getAccessToken = options.getAccessToken
    this.editorVersion = options.editorVersion ?? 'monaco@unknown'
    this.initialCode = options.initialCode ?? ''

    this.connect()
    this.flushTimer = window.setInterval(() => this.flush(), FLUSH_INTERVAL_MS)
    this.heartbeatTimer = window.setInterval(() => this.sendHeartbeat(), HEARTBEAT_INTERVAL_MS)
    window.addEventListener('beforeunload', this.handleUnload)
    document.addEventListener('visibilitychange', this.handleVisibilityChange)
  }

  /** 세션 시작 기준 상대 ms. */
  private now(): number {
    return Math.round(performance.now() - this.sessionStartedAtMs)
  }

  /** Monaco 편집 결과 하나를 EditOp로 큐잉한다. IME 조합 중간 상태는 호출하지 않을 것. */
  pushOp(op: Omit<EditOp, 't'>) {
    if (this.destroyed || this.ended) return
    this.opBuffer.push({ ...op, t: this.now() })
    if (this.opBuffer.length >= MAX_BUFFERED_OPS) this.flush()
  }

  updateCursor(offset: number) {
    this.cursorOffset = offset
  }

  /** 제출 시각 마킹. 순서 보장을 위해 밀린 편집 배치를 먼저 흘려보낸다. */
  markSubmission(submissionId: string) {
    if (this.destroyed || this.ended) return
    this.flush()
    this.sendRaw({
      type: 'submission.mark',
      sid: this.sessionId,
      t: this.now(),
      submission_id: submissionId,
    })
  }

  /** AC 제출 등으로 세션이 명시적으로 끝났음을 서버에 알리고 소켓을 닫는다. */
  end(reason: SessionEndReason) {
    if (this.destroyed || this.ended) return
    this.ended = true
    // 소켓이 살아있으면 그대로 써서 마지막 edit.batch와 session.end가 같은 연결(→ 같은 Kafka
    // 발행 순서)로 나가게 한다. forceBeacon(true)로 강제하면 beacon POST와 WS 메시지가
    // 서로 다른 비동기 경로로 각각 Kafka에 발행돼 순서가 뒤바뀔 수 있다(마지막 편집 유실 위험).
    this.flush()
    this.sendRaw({ type: 'session.end', sid: this.sessionId, t: this.now(), reason })
    this.teardown()
    this.socket?.close(1000)
  }

  /**
   * 사용자가 수동으로 포기/종료하거나 컴포넌트가 언마운트될 때 호출.
   * 명시적 session.end를 보내지 않아도, 서버는 WebSocketDisconnect를 reason="closed"로
   * 합성해 처리한다(backend/app/api/ingest.py) — 여기선 그냥 플러시 후 연결만 끊으면 된다.
   * end()와 마찬가지로 소켓이 살아있으면 beacon으로 강제하지 않는다.
   */
  destroy() {
    if (this.destroyed) return
    this.destroyed = true
    this.flush()
    this.teardown()
    this.socket?.close(1000)
  }

  private teardown() {
    if (this.flushTimer !== null) window.clearInterval(this.flushTimer)
    if (this.heartbeatTimer !== null) window.clearInterval(this.heartbeatTimer)
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    this.flushTimer = null
    this.heartbeatTimer = null
    this.reconnectTimer = null
    window.removeEventListener('beforeunload', this.handleUnload)
    document.removeEventListener('visibilitychange', this.handleVisibilityChange)
  }

  private connect() {
    const token = this.getAccessToken()
    if (!token) return
    try {
      const url = buildWsUrl(
        `/ws/events?session_id=${encodeURIComponent(this.sessionId)}&token=${encodeURIComponent(token)}`,
      )
      const socket = new WebSocket(url)
      socket.addEventListener('open', () => {
        this.reconnectAttempt = 0
        this.sessionStartSent = false // resume 메시지 수신 후 필요 시 다시 보낸다
      })
      socket.addEventListener('message', (event) => this.handleMessage(event))
      socket.addEventListener('close', () => this.handleClose())
      socket.addEventListener('error', () => {
        // 연결 실패는 flush()의 beacon 폴백 + close 핸들러의 재연결로 자연스럽게 처리된다.
      })
      this.socket = socket
    } catch {
      this.socket = null
      this.scheduleReconnect()
    }
  }

  private handleMessage(event: MessageEvent) {
    let msg: ServerIngestMessage
    try {
      msg = JSON.parse(event.data)
    } catch {
      return
    }

    if (msg.type === 'resume') {
      // 재연결 응답: last_seq 이후로 ack 못 받은 배치를 seq 순으로 재전송.
      if (!this.sessionStartSent) {
        this.sendSessionStart()
      }
      const pending = [...this.unacked.entries()]
        .filter(([seq]) => seq > msg.last_seq)
        .sort(([a], [b]) => a - b)
      for (const [, batch] of pending) this.sendRaw(batch)
    } else if (msg.type === 'ack') {
      for (const seq of this.unacked.keys()) {
        if (seq <= msg.seq) this.unacked.delete(seq)
      }
    } else if (msg.type === 'error') {
      // SCHEMA_INVALID 등 — 워커/게이트웨이단 검증 실패. 클라 재시도로 복구되지 않으므로 로깅만.
      console.warn('[ingest] server rejected message', msg.code, 'seq=', msg.seq)
    }
  }

  private handleClose() {
    this.socket = null
    if (this.destroyed || this.ended) return
    this.scheduleReconnect()
  }

  private scheduleReconnect() {
    if (this.destroyed || this.ended || this.reconnectTimer !== null) return
    const delay =
      RECONNECT_DELAYS_MS[Math.min(this.reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)]
    this.reconnectAttempt += 1
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private sendSessionStart() {
    if (this.socket?.readyState !== WebSocket.OPEN) return
    this.sessionStartSent = true
    this.sendRaw({
      type: 'session.start',
      sid: this.sessionId,
      problem_id: this.problemId,
      lang: this.language,
      client_ts: Date.now(),
      editor: this.editorVersion,
      initial_code: this.initialCode,
    })
  }

  private sendRaw(msg: ClientIngestMessage) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(JSON.stringify(msg))
      } catch {
        // 다음 flush/heartbeat 주기에서 자연스럽게 재시도된다.
      }
    }
  }

  private sendHeartbeat() {
    if (this.destroyed || this.ended) return
    this.sendRaw({
      type: 'session.heartbeat',
      sid: this.sessionId,
      t: this.now(),
      cursor: this.cursorOffset,
    })
  }

  private flush(forceBeacon = false) {
    if (this.opBuffer.length === 0) return
    const ops = this.opBuffer
    this.opBuffer = []
    const seq = this.nextSeq++
    const batch: EditBatchMessage = {
      type: 'edit.batch',
      sid: this.sessionId,
      seq,
      base_t: ops[0].t,
      ops,
    }
    this.unacked.set(seq, batch)

    if (!forceBeacon && this.socket?.readyState === WebSocket.OPEN) {
      if (!this.sessionStartSent) this.sendSessionStart()
      this.sendRaw(batch)
      return
    }

    this.sendBeacon(batch)
  }

  private sendBeacon(batch: EditBatchMessage) {
    const token = this.getAccessToken()
    if (!token) return
    const url = buildHttpUrl(
      `/events/beacon?session_id=${encodeURIComponent(this.sessionId)}&token=${encodeURIComponent(token)}`,
    )
    const body = JSON.stringify({ seq: batch.seq, base_t: batch.base_t, ops: batch.ops })
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
        // 탭 종료 등으로 이미 실패한 전송은 재시도하지 않는다 — 유실 허용(unacked엔 남아있어
        // 소켓이 살아있는 동안엔 재시도되지만, beacon 경로 자체의 재시도는 하지 않음).
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
