import { describe, expect, it, vi } from 'vitest'
import { ref, watch } from 'vue'
import { useAgentEvents, type AgentEventState } from '../useAgentEvents'
import type { AgentSSEEvent } from '../../types/event'
import type { Message, StepContent } from '../../types/message'

const makeState = (): AgentEventState => ({
  messages: ref<Message[]>([]), title: ref('New Chat'), plan: ref(), isLoading: ref(true),
  lastEventId: ref(), lastTool: ref(), lastNoMessageTool: ref(),
})
const step = (status: 'running' | 'failed'): AgentSSEEvent => ({
  event: 'step', data: { event_id: status, timestamp: 1, id: 'step', description: 'Read file', status },
})
const tool = (status: 'calling' | 'called'): AgentSSEEvent => ({
  event: 'tool', data: { event_id: status, timestamp: 2, tool_call_id: 'call', name: 'file', function: 'file_read', args: {}, status },
})

describe('agent history batch replay', () => {
  it('publishes one message batch and does not open tool panels for history', () => {
    const state = makeState()
    const onToolActivity = vi.fn()
    const publish = vi.fn()
    const stop = watch(state.messages, publish, { deep: true, flush: 'sync' })
    const events = useAgentEvents(state, { onToolActivity })
    events.replayEvents([step('running'), tool('calling'), tool('called'), step('failed')])
    expect(publish).toHaveBeenCalledTimes(1)
    expect(onToolActivity).not.toHaveBeenCalled()
    const content = state.messages.value[0].content as StepContent
    expect(content.status).toBe('failed')
    expect(content.tools).toHaveLength(1)
    expect(content.tools[0].status).toBe('called')
    expect(state.isLoading.value).toBe(false)
    expect(state.lastEventId.value).toBe('failed')
    events.handleEvent(tool('called'))
    expect(onToolActivity).toHaveBeenCalledTimes(1)
    stop()
  })

  it('replaces old tool, plan, and cursor state without mutating prior objects', () => {
    const state = makeState()
    const events = useAgentEvents(state)
    events.handleEvent(tool('calling'))
    const previousTool = state.lastTool.value
    state.plan.value = { event_id: 'plan', timestamp: 1, steps: [] }
    events.replayEvents([tool('called')])
    expect(previousTool?.status).toBe('calling')
    expect(state.lastTool.value?.status).toBe('called')
    expect(state.plan.value).toBeUndefined()
    events.replayEvents([])
    expect(state.messages.value).toEqual([])
    expect(state.lastTool.value).toBeUndefined()
    expect(state.lastNoMessageTool.value).toBeUndefined()
    expect(state.lastEventId.value).toBeUndefined()
  })

  it('retains source attachment messages and SSE loading semantics', () => {
    const state = makeState()
    const file = { file_id: 'file', filename: 'data.txt', size: 1, content_type: 'text/plain', upload_date: '' }
    useAgentEvents(state).replayEvents([
      { event: 'message', data: { event_id: 'user', timestamp: 1, role: 'user', content: 'Read file', attachments: [file] } },
      { event: 'done', data: { event_id: 'done', timestamp: 2 } },
    ])
    expect(state.messages.value.map(message => message.type)).toEqual(['user', 'attachments'])
    // The SSE close callback, not a replayed done event, clears loading.
    expect(state.isLoading.value).toBe(true)
  })
})
