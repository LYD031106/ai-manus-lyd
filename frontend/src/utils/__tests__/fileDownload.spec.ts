import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getFileDownloadUrl, type FileInfo } from '../../api/file'
import { showErrorToast } from '../toast'
import { openFileDownload } from '../fileDownload'

vi.mock('../../api/file', () => ({ getFileDownloadUrl: vi.fn() }))
vi.mock('../toast', () => ({ showErrorToast: vi.fn() }))

const file: FileInfo = {
  file_id: 'id-42', filename: 'report.txt', upload_date: '',
  file_url: '/download?signature=expired',
}
const fresh = '/download?signature=new'

function pendingSignature() {
  let resolve!: (value: string) => void
  vi.mocked(getFileDownloadUrl).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  return (value: string) => resolve(value)
}

function reservePopup() {
  const popup = { closed: false, opener: window as Window | null, close: vi.fn(), location: { replace: vi.fn() } }
  const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
  return { popup, open }
}

beforeEach(() => { vi.clearAllMocks() })
afterEach(() => { vi.restoreAllMocks() })

describe('openFileDownload', () => {
  it('reserves a tab during the click, detaches its opener and waits for the new signature', async () => {
    const { popup, open } = reservePopup()
    const resolve = pendingSignature()
    const done = openFileDownload(file)
    expect(open).toHaveBeenCalledExactlyOnceWith('about:blank', '_blank')
    expect(open.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(getFileDownloadUrl).mock.invocationCallOrder[0])
    expect(popup.opener).toBeNull()
    expect(popup.location.replace).not.toHaveBeenCalled()
    expect(getFileDownloadUrl).toHaveBeenCalledExactlyOnceWith(file)
    resolve(fresh)
    await done
    expect(popup.location.replace).toHaveBeenCalledExactlyOnceWith(fresh)
    expect(popup.close).not.toHaveBeenCalled()
  })

  it('closes an empty tab and reports an error if signing fails', async () => {
    const { popup } = reservePopup()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    vi.mocked(getFileDownloadUrl).mockRejectedValueOnce(new Error('Access denied'))
    await openFileDownload(file)
    expect(popup.close).toHaveBeenCalledTimes(1)
    expect(popup.location.replace).not.toHaveBeenCalled()
    expect(click).not.toHaveBeenCalled()
    expect(showErrorToast).toHaveBeenCalledTimes(1)
  })

  it('only uses a download link if the popup was blocked; removes it after clicking', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null)
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function(this: HTMLAnchorElement) {
      expect(this.isConnected).toBe(true)
      expect(this.getAttribute('href')).toBe(fresh)
      expect(this.download).toBe(file.filename)
    })
    const resolve = pendingSignature()
    const done = openFileDownload(file)
    expect(click).not.toHaveBeenCalled()
    resolve(fresh)
    await done
    expect(click).toHaveBeenCalledTimes(1)
    expect(document.querySelector('a[download]')).toBeNull()
    expect(showErrorToast).not.toHaveBeenCalled()
  })

  it('leaves the original page alone when the user closes the tab before signing completes', async () => {
    const { popup, open } = reservePopup()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const resolve = pendingSignature()
    const done = openFileDownload(file)
    popup.closed = true
    resolve(fresh)
    await done
    expect(popup.location.replace).not.toHaveBeenCalled()
    expect(click).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledTimes(1)
  })
})
