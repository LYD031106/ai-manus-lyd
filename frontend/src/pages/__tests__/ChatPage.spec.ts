import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick } from 'vue'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import ChatPage from '../ChatPage.vue'
import ChatMessage from '../../components/ChatMessage.vue'
import ChatBox from '../../components/ChatBox.vue'
import * as agentApi from '../../api/agent'
import { i18n } from '../../composables/useI18n'
import { SessionStatus, type GetSessionResponse } from '../../types/response'
import type { MessageContent } from '../../types/message'
import { eventBus } from '../../utils/eventBus'

const navigation = vi.hoisted(() => ({
  route: { params: { sessionId: 'old' } },
  update: undefined as undefined | ((to: { params: { sessionId: string } }, from: unknown, next: () => void) => void),
}))
vi.mock('vue-router', async original => ({
  ...await original<typeof import('vue-router')>(),
  useRouter: () => ({ currentRoute: { value: navigation.route }, push: vi.fn() }),
  onBeforeRouteUpdate: (callback: typeof navigation.update) => { navigation.update = callback },
}))
vi.mock('../../api/agent', async original => ({
  ...await original<typeof import('../../api/agent')>(),
  getSession: vi.fn(), chatWithSession: vi.fn(),
  leaveChatSession: vi.fn().mockResolvedValue(undefined),
  clearUnreadMessageCount: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('../../api/config', () => ({ getCachedClientConfig: vi.fn().mockResolvedValue({ browser_enabled: false }) }))

const session = (id: string): GetSessionResponse => ({
  session_id: id, title: id, status: SessionStatus.COMPLETED,
  is_shared: false, is_favorite: false, is_pinned: true, project_id: null, task_mode: 'chat',
  events: [{ event: 'message', data: { event_id: id, timestamp: 1, role: 'assistant', content: id, attachments: [] } }],
})
const panelStub = defineComponent({
  setup(_, { expose }) {
    expose({ hideComputerPanel: vi.fn(), showComputerPanel: vi.fn(), scrollToBottom: vi.fn() })
  },
  template: '<div><slot /></div>',
})
let wrapper: VueWrapper | undefined
const mountPage = () => (wrapper = shallowMount(ChatPage, {
  global: {
    plugins: [i18n], renderStubDefaultSlot: true,
    stubs: { SimpleBar: panelStub, ComputerPanel: panelStub },
  },
}))

beforeEach(() => {
  navigation.route.params.sessionId = 'old'
  history.replaceState({}, '')
  vi.mocked(agentApi.getSession).mockReset()
  vi.mocked(agentApi.chatWithSession).mockReset().mockResolvedValue(vi.fn())
})
afterEach(() => { wrapper?.unmount(); vi.restoreAllMocks() })

describe('ChatPage history integration', () => {
  it('discards a late history response after switching sessions', async () => {
    let resolveOld!: (value: GetSessionResponse) => void
    vi.mocked(agentApi.getSession).mockImplementation(id => id === 'old'
      ? new Promise(resolve => { resolveOld = resolve })
      : Promise.resolve(session(id)))
    const page = mountPage()
    navigation.update!({ params: { sessionId: 'new' } }, {}, vi.fn())
    await flushPromises()
    resolveOld(session('old'))
    await flushPromises()
    const messages = page.findAllComponents(ChatMessage).map(message =>
      (message.props('message').content as MessageContent).content)
    expect(messages).toEqual(['new'])
    expect(agentApi.chatWithSession).toHaveBeenCalledTimes(1)
    expect(vi.mocked(agentApi.chatWithSession).mock.calls[0][0]).toBe('new')
  })

  it('keeps status and live terminal/file updates after batch restoration', async () => {
    vi.mocked(agentApi.getSession).mockResolvedValue(session('old'))
    const page = mountPage()
    await flushPromises()
    const callbacks = vi.mocked(agentApi.chatWithSession).mock.calls[0][4]!
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(false)
    callbacks.onStatusUpdate?.('running')
    await nextTick()
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(true)
    const emit = vi.spyOn(eventBus, 'emit')
    callbacks.onMessage?.({ event: 'terminal_update', data: { event_id: 't', timestamp: 2, shell_id: 'shell', output: 'hello' } })
    callbacks.onMessage?.({ event: 'file_update', data: { event_id: 'f', timestamp: 3, path: '/tmp/data.txt', content: 'updated' } })
    expect(emit).toHaveBeenCalledWith('tool:terminal_update', { sessionId: 'old', shellId: 'shell', output: 'hello' })
    expect(emit).toHaveBeenCalledWith('tool:file_update', expect.objectContaining({ sessionId: 'old', path: '/tmp/data.txt', content: 'updated' }))
    callbacks.onStatusUpdate?.('waiting')
    await nextTick()
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(false)
  })

  it('sends an attachment-only initial turn with a prompt and grouped attachment', async () => {
    const file = { file_id: 'f', filename: 'data.txt', content_type: 'text/plain', size: 10, upload_date: '' }
    history.replaceState({ message: '', files: [file] }, '')
    vi.mocked(agentApi.getSession).mockResolvedValue(session('old'))
    const page = mountPage()
    await flushPromises()
    const request = vi.mocked(agentApi.chatWithSession).mock.calls[0]
    expect(request[0]).toBe('old')
    expect(request[1]).toBeTruthy()
    expect(request[3]).toEqual([{ file_id: 'f', filename: 'data.txt' }])
    const content = page.getComponent(ChatMessage).props('message').content as MessageContent
    expect(content.attachments).toEqual([file])
    expect(content.content).toBe(request[1])
  })
})
