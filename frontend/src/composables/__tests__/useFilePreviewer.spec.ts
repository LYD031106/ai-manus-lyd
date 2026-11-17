import { describe, expect, it } from 'vitest'
import { nextTick, watch } from 'vue'
import { useFilePreviewer } from '../useFilePreviewer'

describe('opening an attachment again', () => {
  it('requests a new preview even if the same history object is clicked twice', async () => {
    const { fileInfo, showFilePreviewer, hideFilePreviewer } = useFilePreviewer()
    const file = { file_id: 'file-1', filename: 'report.txt', upload_date: '' }
    let loads = 0
    const stop = watch(fileInfo, () => loads++)
    showFilePreviewer(file)
    await nextTick()
    showFilePreviewer(file)
    await nextTick()
    expect(loads).toBe(2)
    expect(fileInfo.value).toEqual(file)
    expect(fileInfo.value).not.toBe(file)
    stop()
    hideFilePreviewer()
  })
})
