import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AttachmentsMessage from '../AttachmentsMessage.vue'
import { i18n } from '../../composables/useI18n'
import { useSessionFileList } from '../../composables/useSessionFileList'

describe('attachment card file-list context', () => {
  const state = useSessionFileList()
  afterEach(() => state.hideSessionFileList())

  it.each([false, true])('opens the matching private/shared list (shared=%s)', async (shared) => {
    const wrapper = mount(AttachmentsMessage, {
      global: { plugins: [i18n] },
      props: {
        content: {
          timestamp: 1,
          role: 'assistant',
          attachments: [{ file_id: 'file-1', filename: 'report.txt', upload_date: '', shared_session_id: shared ? 'public-session' : undefined }],
        },
      },
    })
    await wrapper.get('button').trigger('click')
    expect(state.visible.value).toBe(true)
    expect(state.shared.value).toBe(shared)
    wrapper.unmount()
  })
})
