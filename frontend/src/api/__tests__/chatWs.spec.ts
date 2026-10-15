import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../client', () => ({ BASE_URL: 'http://localhost/api/v1' }))

class FakeWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  static latest: FakeWebSocket
  readyState = 0
  onopen?: () => void
  onmessage?: (event: { data: string }) => void
  onclose?: () => void
  onerror?: () => void
  constructor() { FakeWebSocket.latest = this }
  close() {}
  send() {}
}

afterEach(() => vi.unstubAllGlobals())

describe('chat subscription cleanup', () => {
  it('keeps replacement handlers when an earlier subscription finishes late', async () => {
    vi.resetModules()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const { getChatWebSocket } = await import('../chatWs')
    const client = getChatWebSocket()
    const oldStatus = vi.fn()
    const currentStatus = vi.fn()
    const cancelOld = client.setHandlers('session', { onStatusUpdate: oldStatus })
    const cancelCurrent = client.setHandlers('session', { onStatusUpdate: currentStatus })
    cancelOld()
    const pushStatus = () => FakeWebSocket.latest.onmessage?.({ data: JSON.stringify({
      type: 'event', session_id: 'session', event: 'status_update', data: { agent_status: 'waiting' },
    }) })
    pushStatus()
    expect(oldStatus).not.toHaveBeenCalled()
    expect(currentStatus).toHaveBeenCalledWith('waiting')
    cancelCurrent()
    pushStatus()
    expect(currentStatus).toHaveBeenCalledTimes(1)
    client.destroy()
  })
})
