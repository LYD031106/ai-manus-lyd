import { describe, expect, it, vi } from 'vitest'
import { ref, watch } from 'vue'
import { useAgentEvents, type AgentEventState } from '../useAgentEvents'
import type { AgentEvent } from '../../types/event'
import type { MessageContent, StepContent } from '../../types/message'

const createState = (): AgentEventState => ({
  messages: ref([]), title: ref('New chat'), plan: ref(), lastEventId: ref(),
  lastTool: ref(), lastNoMessageTool: ref(),
})
const events: AgentEvent[] = [
  { event: 'message', data: { event_id: '1', timestamp: 1, role: 'user', content: '', attachments: [
    { file_id: 'f', filename: 'data.txt', size: 10, content_type: 'text/plain', upload_date: '' },
  ] } },
  { event: 'step', data: { event_id: '2', timestamp: 2, id: 'step', status: 'running', description: 'Read file' } },
  { event: 'tool', data: { event_id: '3', timestamp: 3, tool_call_id: 'call', name: 'file', function: 'file_read', args: { file: 'data.txt' }, status: 'calling' } },
  { event: 'tool', data: { event_id: '4', timestamp: 4, tool_call_id: 'call', name: 'file', function: 'file_read', args: { file: 'data.txt' }, status: 'called', content: { content: 'hello' } } },
  { event: 'step', data: { event_id: '5', timestamp: 5, id: 'step', status: 'failed', description: 'Read file' } },
  { event: 'error', data: { event_id: '6', timestamp: 6, error: 'Earlier error' } },
  { event: 'title', data: { event_id: '7', timestamp: 7, title: 'Attached file' } },
]

describe('history batch replay', () => {
  it('matches live reduction, groups user attachments and publishes messages once', () => {
    const live = createState()
    const replay = createState()
    const liveReducer = useAgentEvents(live)
    events.forEach(liveReducer.handleEvent)
    const publish = vi.fn()
    const stop = watch(replay.messages, publish, { deep: true, flush: 'sync' })
    const onToolActivity = vi.fn()
    const onStreamError = vi.fn()
    const reducer = useAgentEvents(replay, { onToolActivity, onStreamError })
    reducer.replayEvents(events)
    expect(replay.messages.value).toEqual(live.messages.value)
    expect(publish).toHaveBeenCalledTimes(1)
    expect(onToolActivity).not.toHaveBeenCalled()
    expect(onStreamError).not.toHaveBeenCalled()
    expect((replay.messages.value[0].content as MessageContent).attachments).toHaveLength(1)
    expect((replay.messages.value[1].content as StepContent).tools).toHaveLength(1)
    expect((replay.messages.value[1].content as StepContent).status).toBe('failed')
    expect(replay.lastTool.value?.status).toBe('called')
    expect(replay.lastEventId.value).toBe('7')
    expect(replay.title.value).toBe('Attached file')
    reducer.handleEvent(events[2])
    expect(onToolActivity).toHaveBeenCalledTimes(1)
    stop()
  })

  it('replaces old history without stale tools and ignores live computer control events', () => {
    const state = createState()
    const reducer = useAgentEvents(state)
    reducer.replayEvents(events)
    reducer.replayEvents([events[0]])
    expect(state.messages.value).toHaveLength(1)
    expect(state.lastTool.value).toBeUndefined()
    expect(state.lastNoMessageTool.value).toBeUndefined()
    reducer.handleEvent({ event: 'terminal_update', data: { event_id: 'ignored', timestamp: 9, shell_id: 's', output: 'new' } })
    expect(state.messages.value).toHaveLength(1)
    expect(state.lastEventId.value).toBe('1')
  })
})
