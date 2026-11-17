import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { getFileDownloadUrl, type FileInfo } from '../../../api/file'
import { i18n } from '../../../composables/useI18n'
import { showErrorToast } from '../../../utils/toast'
import CodePreview from '../CodeFilePreview.vue'
import MarkdownPreview from '../MarkdownFilePreview.vue'
import ImagePreview from '../ImageFilePreview.vue'

vi.mock('../../../api/file', () => ({ getFileDownloadUrl: vi.fn() }))
vi.mock('../../../utils/toast', () => ({ showErrorToast: vi.fn() }))

const file = (id: string): FileInfo => ({ file_id: id, filename: id, upload_date: '' })
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.mocked(getFileDownloadUrl).mockReset()
  vi.mocked(showErrorToast).mockClear()
})
afterEach(() => vi.unstubAllGlobals())

describe('file preview request lifecycle', () => {
  it.each([['code', CodePreview], ['markdown', MarkdownPreview]] as const)(
    '%s prevents a late response body from overwriting a newer file',
    async (_, component) => {
      vi.mocked(getFileDownloadUrl).mockImplementation(async info => '/signed/' + info.file_id)
      const slowBody = deferred<string>()
      const fetchMock = vi.fn<typeof fetch>().mockImplementation(async url =>
        url === '/signed/A'
          ? { ok: true, text: () => slowBody.promise } as Response
          : new Response('Preview of ' + url.toString().split('/').pop()))
      vi.stubGlobal('fetch', fetchMock)
      const wrapper = mount(component, { props: { file: file('initial') } })
      await flushPromises()
      expect(wrapper.text()).toBe('Preview of initial')
      await wrapper.setProps({ file: file('A') })
      await flushPromises()
      expect(wrapper.text()).toBe('')
      const signal = fetchMock.mock.calls[1][1]?.signal
      expect(signal?.aborted).toBe(false)
      await wrapper.setProps({ file: file('B') })
      await flushPromises()
      expect(signal?.aborted).toBe(true)
      expect(wrapper.text()).toBe('Preview of B')
      slowBody.resolve('Preview of A')
      await flushPromises()
      expect(wrapper.text()).toBe('Preview of B')
    },
  )

  it('avoids starting the content fetch for a signature that arrives after unmount', async () => {
    const slowSign = deferred<string>()
    vi.mocked(getFileDownloadUrl).mockReturnValueOnce(slowSign.promise)
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(CodePreview, { props: { file: file('A') } })
    wrapper.unmount()
    slowSign.resolve('/signed/A')
    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps image B when image A signs later, and hides B immediately when C starts', async () => {
    const slowA = deferred<string>()
    const failingC = deferred<string>()
    vi.mocked(getFileDownloadUrl)
      .mockReturnValueOnce(slowA.promise)
      .mockResolvedValueOnce('/signed/B')
      .mockReturnValueOnce(failingC.promise)
    const wrapper = mount(ImagePreview, { props: { file: file('A') }, global: { plugins: [i18n] } })
    await wrapper.setProps({ file: file('B') })
    await flushPromises()
    expect(wrapper.get('img').attributes('src')).toBe('/signed/B')
    slowA.resolve('/signed/A')
    await flushPromises()
    expect(wrapper.get('img').attributes('src')).toBe('/signed/B')
    await wrapper.setProps({ file: file('C') })
    expect(wrapper.find('img').exists()).toBe(false)
    failingC.reject(new Error('Access denied'))
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
    expect(showErrorToast).toHaveBeenCalledTimes(1)
  })
})
