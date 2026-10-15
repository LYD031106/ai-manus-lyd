import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import ChatBox from '../ChatBox.vue'
import type { ExtendedFileInfo } from '../ChatBoxFiles.vue'
import { i18n } from '../../composables/useI18n'

const file: ExtendedFileInfo = {
  file_id: 'uploaded', filename: 'data.txt', content_type: 'text/plain', size: 10, upload_date: '',
}
let wrapper: VueWrapper | undefined
const mountBox = async (attachments: ExtendedFileInfo[], modelValue = '', allowSendFilesOnly = true) => {
  wrapper = mount(ChatBox, {
    props: { modelValue, attachments, rows: 1, isRunning: false, allowSendFilesOnly },
    global: { plugins: [i18n] },
  })
  await nextTick()
  return wrapper
}
afterEach(() => wrapper?.unmount())

describe('textarea submission', () => {
  it('allows uploaded attachments without text by default', async () => {
    const box = await mountBox([file])
    const send = box.get('[data-testid="send-message"]')
    expect(send.attributes('disabled')).toBeUndefined()
    await send.trigger('click')
    expect(box.emitted('submit')).toHaveLength(1)
  })

  it.each(['uploading', 'failed'] as const)('blocks text and click submission while a file is %s', async status => {
    const box = await mountBox([{ ...file, status }], 'Read this file')
    expect(box.get('[data-testid="send-message"]').attributes('disabled')).toBeDefined()
    await box.get('textarea').trigger('keydown', { key: 'Enter' })
    await box.get('[data-testid="send-message"]').trigger('click')
    expect(box.emitted('submit')).toBeUndefined()
    await box.setProps({ attachments: [{ ...file, status: 'success' }] })
    await box.get('textarea').trigger('keydown', { key: 'Enter' })
    expect(box.emitted('submit')).toHaveLength(1)
  })

  it('respects the text-required option and composition input', async () => {
    const box = await mountBox([file], '', false)
    expect(box.get('[data-testid="send-message"]').attributes('disabled')).toBeDefined()
    await box.setProps({ modelValue: '分析文件' })
    await box.get('textarea').trigger('compositionstart')
    await box.get('textarea').trigger('keydown', { key: 'Enter' })
    expect(box.emitted('submit')).toBeUndefined()
    await box.get('textarea').trigger('compositionend')
    await box.get('textarea').trigger('keydown', { key: 'Enter' })
    expect(box.emitted('submit')).toHaveLength(1)
  })
})
