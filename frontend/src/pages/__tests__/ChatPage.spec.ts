import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick } from 'vue'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import ChatPage from '../ChatPage.vue'
import ChatMessage from '../../components/ChatMessage.vue'
import ChatBox from '../../components/ChatBox.vue'
import * as agentApi from '../../api/agent'
import { i18n } from '../../composables/useI18n'
import { SessionStatus, type GetSessionResponse } from '../../types/response'
import type { AttachmentsContent, MessageContent } from '../../types/message'
import { showErrorToast } from '../../utils/toast'

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
  clearUnreadMessageCount: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('../../utils/toast', () => ({ showErrorToast: vi.fn(), showSuccessToast: vi.fn() }))

const session = (id: string, status = SessionStatus.COMPLETED): GetSessionResponse => ({
  session_id: id, title: id, status, is_shared: false, task_mode: 'agent',
  events: [{ event: 'message', data: { event_id: id, timestamp: 1, role: 'assistant', content: id, attachments: [] } }],
})
const scrollToBottom = vi.fn()
const panelStub = defineComponent({
  setup(_, { expose }) {
    expose({ hideComputerPanel: vi.fn(), showComputerPanel: vi.fn(), scrollToBottom })
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
const messageTexts = (page: VueWrapper) => page.findAllComponents(ChatMessage).map(message =>
  (message.props('message').content as MessageContent).content)
const navigate = (sessionId: string) => {
  navigation.route.params.sessionId = sessionId
  navigation.update!({ params: { sessionId } }, {}, vi.fn())
}
const callbacksAt = (index: number) => vi.mocked(agentApi.chatWithSession).mock.calls[index][4]!

beforeEach(() => {
  navigation.route.params.sessionId = 'old'
  history.replaceState({}, '')
  scrollToBottom.mockClear()
  vi.mocked(showErrorToast).mockClear()
  vi.mocked(agentApi.clearUnreadMessageCount).mockClear()
  vi.mocked(agentApi.getSession).mockReset().mockImplementation(id => Promise.resolve(session(id)))
  vi.mocked(agentApi.chatWithSession).mockReset().mockResolvedValue(vi.fn())
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks() })

describe('ChatPage SSE and history lifecycle', () => {
  it('discards late history after switching sessions', async () => {
    let resolveOld!: (value: GetSessionResponse) => void
    vi.mocked(agentApi.getSession).mockImplementation(id => id === 'old'
      ? new Promise(resolve => { resolveOld = resolve })
      : Promise.resolve(session(id)))
    const page = mountPage()
    navigate('new')
    await flushPromises()
    resolveOld(session('old', SessionStatus.RUNNING))
    await flushPromises()
    expect(messageTexts(page)).toEqual(['new'])
    expect(agentApi.chatWithSession).not.toHaveBeenCalled()
    expect(agentApi.clearUnreadMessageCount).toHaveBeenCalledExactlyOnceWith('new')
  })

  it('publishes restored history with one scroll and resumes only active tasks', async () => {
    vi.mocked(agentApi.getSession).mockResolvedValue(session('old', SessionStatus.RUNNING))
    const page = mountPage()
    await flushPromises()
    expect(scrollToBottom).toHaveBeenCalledTimes(1)
    expect(agentApi.chatWithSession).toHaveBeenCalledExactlyOnceWith('old', '', 'old', [], expect.any(Object))
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(true)
    callbacksAt(0).onClose?.()
    await nextTick()
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(false)
  })

  it('ignores stale stream callbacks and cancels a late connection handle', async () => {
    history.replaceState({ message: 'initial' }, '')
    let resolveOld!: (cancel: () => void) => void
    vi.mocked(agentApi.chatWithSession).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    const page = mountPage()
    const oldCallbacks = callbacksAt(0)
    navigate('new')
    await flushPromises()
    const cancelOld = vi.fn()
    resolveOld(cancelOld)
    await flushPromises()
    oldCallbacks.onOpen?.()
    oldCallbacks.onMessage?.({ event: 'message', data: { event_id: 'stale', timestamp: 2, role: 'assistant', content: 'stale', attachments: [] } })
    oldCallbacks.onError?.(new Error('stale error'))
    oldCallbacks.onClose?.()
    await nextTick()
    expect(cancelOld).toHaveBeenCalledTimes(1)
    expect(messageTexts(page)).toEqual(['new'])
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(false)
    expect(showErrorToast).not.toHaveBeenCalled()
  })

  it('does not let a previous turn close the newer SSE connection', async () => {
    history.replaceState({ message: 'initial' }, '')
    const cancelFirst = vi.fn()
    const cancelSecond = vi.fn()
    vi.mocked(agentApi.chatWithSession).mockResolvedValueOnce(cancelFirst).mockResolvedValueOnce(cancelSecond)
    const page = mountPage()
    await flushPromises()
    page.getComponent(ChatBox).vm.$emit('update:modelValue', 'continue')
    page.getComponent(ChatBox).vm.$emit('submit')
    await flushPromises()
    callbacksAt(0).onClose?.()
    await nextTick()
    expect(cancelFirst).toHaveBeenCalledTimes(1)
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(true)
    page.unmount()
    wrapper = undefined
    expect(cancelSecond).toHaveBeenCalledTimes(1)
  })

  it('invalidates history restoration on unmount', async () => {
    let resolveHistory!: (value: GetSessionResponse) => void
    vi.mocked(agentApi.getSession).mockImplementation(() => new Promise(resolve => { resolveHistory = resolve }))
    mountPage().unmount()
    wrapper = undefined
    resolveHistory(session('old', SessionStatus.RUNNING))
    await flushPromises()
    expect(agentApi.chatWithSession).not.toHaveBeenCalled()
    expect(agentApi.clearUnreadMessageCount).not.toHaveBeenCalled()
  })

  it('reports the same transport failure only once', async () => {
    history.replaceState({ message: 'initial' }, '')
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const failure = new Error('network unavailable')
    vi.mocked(agentApi.chatWithSession).mockImplementation(async (_id, _message, _eventId, _files, callbacks) => {
      callbacks?.onError?.(failure)
      throw failure
    })
    const page = mountPage()
    await flushPromises()
    expect(showErrorToast).toHaveBeenCalledTimes(1)
    expect(messageTexts(page)).toHaveLength(2)
    expect(messageTexts(page)[1]).toContain('network unavailable')
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(false)
  })

  it('cancels handles returned after the stream has already closed', async () => {
    history.replaceState({ message: 'initial' }, '')
    const cancel = vi.fn()
    vi.mocked(agentApi.chatWithSession).mockImplementation(async (_id, _message, _eventId, _files, callbacks) => {
      callbacks?.onClose?.()
      return cancel
    })
    const page = mountPage()
    await flushPromises()
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(page.getComponent(ChatBox).props('isRunning')).toBe(false)
    page.unmount()
    wrapper = undefined
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('sends attachment-only initial input with the source prompt and separate attachment message', async () => {
    const file = { file_id: 'f', filename: 'data.txt', content_type: 'text/plain', size: 10, upload_date: '' }
    history.replaceState({ message: '', files: [file] }, '')
    const page = mountPage()
    await flushPromises()
    const request = vi.mocked(agentApi.chatWithSession).mock.calls[0]
    expect(request[0]).toBe('old')
    expect(request[1]).toBeTruthy()
    expect(request[3]).toEqual([{ file_id: 'f', filename: 'data.txt' }])
    const messages = page.findAllComponents(ChatMessage).map(message => message.props('message'))
    expect(messages.map(message => message.type)).toEqual(['user', 'attachments'])
    expect((messages[0].content as MessageContent).content).toBe(request[1])
    expect((messages[1].content as AttachmentsContent).attachments).toEqual([file])
    expect(agentApi.getSession).not.toHaveBeenCalled()
  })
})
