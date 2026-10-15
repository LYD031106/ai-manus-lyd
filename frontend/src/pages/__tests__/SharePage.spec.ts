import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import SharePage from '../SharePage.vue'
import ChatMessage from '../../components/ChatMessage.vue'
import { i18n } from '../../composables/useI18n'
import type { AgentSSEEvent } from '../../types/event'
import { SessionStatus } from '../../types/response'

const spies = vi.hoisted(() => ({ replay: vi.fn(), handle: vi.fn(), showTool: vi.fn() }))
vi.mock('vue-router', async original => ({
  ...await original<typeof import('vue-router')>(),
  useRouter: () => ({ currentRoute: { value: { params: { sessionId: 'shared' } } } }),
}))
vi.mock('../../api/agent', () => ({ getSharedSession: vi.fn() }))
vi.mock('../../composables/useAgentEvents', async original => {
  const module = await original<typeof import('../../composables/useAgentEvents')>()
  return {
    ...module,
    useAgentEvents: (...args: Parameters<typeof module.useAgentEvents>) => {
      const events = module.useAgentEvents(...args)
      return {
        replayEvents: (batch: AgentSSEEvent[]) => { spies.replay(batch); events.replayEvents(batch) },
        handleEvent: (event: AgentSSEEvent) => { spies.handle(event); events.handleEvent(event) },
      }
    },
  }
})
import { getSharedSession } from '../../api/agent'

const events: AgentSSEEvent[] = [
  { event: 'tool', data: { event_id: 'tool', timestamp: 1, tool_call_id: 'call', name: 'file', function: 'file_read', args: {}, status: 'called' } },
  { event: 'message', data: { event_id: 'message', timestamp: 2, role: 'assistant', content: 'done', attachments: [] } },
]
const panel = defineComponent({
  setup(_, { expose }) {
    expose({ showComputerPanel: spies.showTool, hideComputerPanel: vi.fn(), scrollToBottom: vi.fn() })
  },
  template: '<div><slot /></div>',
})
let wrapper: VueWrapper | undefined
beforeEach(() => {
  vi.useFakeTimers()
  spies.replay.mockClear(); spies.handle.mockClear(); spies.showTool.mockClear()
  vi.mocked(getSharedSession).mockResolvedValue({
    session_id: 'shared', title: 'Shared', events, status: SessionStatus.COMPLETED, is_shared: true,
  })
})
afterEach(() => { wrapper?.unmount(); vi.useRealTimers() })

describe('shared task replay', () => {
  it('restores in one batch while keeping the timed replay interactive', async () => {
    wrapper = shallowMount(SharePage, {
      global: { plugins: [i18n], renderStubDefaultSlot: true, stubs: { SimpleBar: panel, ComputerPanel: panel } },
    })
    await flushPromises()
    expect(spies.replay).toHaveBeenCalledExactlyOnceWith(events)
    expect(spies.handle).not.toHaveBeenCalled()
    expect(spies.showTool).not.toHaveBeenCalled()
    expect(wrapper.findAllComponents(ChatMessage)).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(3700)
    expect(spies.handle).toHaveBeenCalledTimes(2)
    expect(spies.showTool).toHaveBeenCalledTimes(1)
    expect(wrapper.findAllComponents(ChatMessage)).toHaveLength(2)
  })
})
