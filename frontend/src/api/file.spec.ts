import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getFileDownloadUrl, getFileInfo, type FileInfo } from './file'
import { getSharedSession, getSharedSessionFiles } from './agent'
import { apiClient, API_CONFIG } from './client'

vi.mock('./client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
  API_CONFIG: { host: '' },
  createSSEConnection: vi.fn(),
}))

const file: FileInfo = {
  file_id: 'original-file',
  filename: 'report.txt',
  upload_date: '2026-11-17T00:00:00Z',
  file_url: '/api/v1/files/original-file?signature=old&expires=1',
}
const result = (data: unknown) => ({ data: { code: 0, data } })
const history = (attachments: FileInfo[]) => ({
  session_id: 'shared-session',
  events: [
    { event: 'done', data: { event_id: 'done' } },
    { event: 'message', data: { attachments } },
  ],
})

describe('file links after a page has outlived its signed URLs', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    API_CONFIG.host = ''
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-17T02:00:00Z'))
  })
  afterEach(() => vi.useRealTimers())

  it('replaces an expired attachment URL with an authorized fresh signature', async () => {
    vi.mocked(apiClient.post).mockResolvedValue(result({ signed_url: '/api/v1/files/original-file?signature=fresh' }))
    expect(await getFileDownloadUrl(file)).toBe('/api/v1/files/original-file?signature=fresh')
    expect(apiClient.post).toHaveBeenCalledWith('/files/original-file/signed-url', { expire_minutes: 15 })
    expect(file.file_url).toContain('signature=old') // Do not persist temporary URLs on history objects.
  })

  it('obtains another signature for a later download from an already-open preview', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce(result({ signed_url: '/api/v1/files/original-file?signature=preview' }))
      .mockResolvedValueOnce(result({ signed_url: '/api/v1/files/original-file?signature=later-download' }))
    expect(await getFileDownloadUrl(file)).toContain('signature=preview')
    vi.advanceTimersByTime(60 * 60 * 1000)
    expect(await getFileDownloadUrl(file)).toContain('signature=later-download')
    expect(apiClient.post).toHaveBeenCalledTimes(2)
  })

  it('rechecks ownership even when the cached URL appears unexpired', async () => {
    const denied = new Error('File no longer belongs to this user')
    vi.mocked(apiClient.post).mockRejectedValue(denied)
    await expect(getFileDownloadUrl({ ...file, file_url: '/api/v1/files/original-file?expires=9999999999' })).rejects.toBe(denied)
    expect(apiClient.get).not.toHaveBeenCalled()
  })

  it('supports lazy history attachments with no URL and a separate API origin', async () => {
    API_CONFIG.host = 'https://api.example.com/'
    vi.mocked(apiClient.post).mockResolvedValue(result({ signed_url: '/api/v1/files/original-file?signature=fresh' }))
    expect(await getFileDownloadUrl({ ...file, file_url: undefined })).toBe('https://api.example.com/api/v1/files/original-file?signature=fresh')
  })

  it('does not duplicate an origin in an absolute URL returned by the signer', async () => {
    API_CONFIG.host = 'https://api.example.com'
    vi.mocked(apiClient.post).mockResolvedValue(result({ signed_url: 'https://api.example.com/api/v1/files/original-file?signature=fresh' }))
    expect(await getFileDownloadUrl(file)).toBe('https://api.example.com/api/v1/files/original-file?signature=fresh')
  })

  it('renews a public attachment through the shared conversation, without owner signing', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(result([{ ...file, file_url: '/api/v1/files/original-file?signature=public-fresh' }]))
    expect(await getFileDownloadUrl({ ...file, shared_session_id: 'shared-session' })).toContain('signature=public-fresh')
    expect(apiClient.get).toHaveBeenCalledWith('/sessions/shared-session/share/files')
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('renews the original version from shared history if its path was later replaced', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(result([{ ...file, file_id: 'replacement-file', file_url: '/wrong-version' }]))
      .mockResolvedValueOnce(result(history([{ ...file, file_url: '/api/v1/files/original-file?signature=history-fresh' }])))
    expect(await getFileDownloadUrl({ ...file, shared_session_id: 'shared-session' })).toContain('signature=history-fresh')
    expect(apiClient.get).toHaveBeenLastCalledWith('/sessions/shared/shared-session')
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('does not return an old URL or try owner signing after sharing is revoked', async () => {
    const revoked = new Error('Session not shared')
    vi.mocked(apiClient.get).mockRejectedValue(revoked)
    await expect(getFileDownloadUrl({ ...file, shared_session_id: 'shared-session' })).rejects.toBe(revoked)
    expect(apiClient.get).toHaveBeenCalledTimes(1)
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('does not download a different file with the same name when the attachment is gone', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(result([{ ...file, file_id: 'different-file', file_url: '/wrong-file' }]))
      .mockResolvedValueOnce(result(history([])))
    await expect(getFileDownloadUrl({ ...file, shared_session_id: 'shared-session' })).rejects.toThrow('File is no longer available')
  })

  it('preserves public scope for both shared history attachments and file-list entries', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(result(history([file])))
      .mockResolvedValueOnce(result([file]))
    const session = await getSharedSession('shared-session')
    const event = session.events[1]
    expect('attachments' in event.data && event.data.attachments[0].shared_session_id).toBe('shared-session')
    expect(session.events[0].event).toBe('done')
    expect((await getSharedSessionFiles('shared-session'))[0].shared_session_id).toBe('shared-session')
    expect(file.shared_session_id).toBeUndefined()
  })

  it('reads file metadata from the info endpoint, not the signed download route', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(result(file))
    expect(await getFileInfo(file.file_id)).toEqual(file)
    expect(apiClient.get).toHaveBeenCalledWith('/files/original-file/info')
  })
})
