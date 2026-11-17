// @vitest-environment node
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { getFileDownloadUrl, getFileInfo, type FileInfo } from './file';
import { getSharedSession, getSharedSessionFiles } from './agent';
import { apiClient } from './client';

vi.mock('./client', () => ({
  API_CONFIG: { host: '' },
  apiClient: { get: vi.fn(), post: vi.fn() },
  createSSEConnection: vi.fn(),
}));

const file = (file_url?: string): FileInfo => ({
  file_id: 'file-1', filename: 'result.gml', upload_date: '', file_url,
});
const fresh = '/api/v1/files/file-1?signature=new&expires=2000000900';
const expired = '/api/v1/files/file-1?signature=old&expires=1999999999';

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(Date, 'now').mockReturnValue(2000000000000);
  vi.stubGlobal('window', { location: { origin: 'http://app.test' } });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('download URL renewal', () => {
  it('reuses a valid link without an extra request', async () => {
    expect(await getFileDownloadUrl(file(fresh))).toBe('http://app.test' + fresh);
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('renews expired and nearly expired links and updates the in-memory file', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { data: { signed_url: fresh } } });
    for (const old of [expired, '/api/v1/files/file-1?expires=2000000020', undefined]) {
      const info = file(old);
      expect(await getFileDownloadUrl(info)).toBe('http://app.test' + fresh);
      expect(info.file_url).toBe(fresh);
    }
    expect(apiClient.post).toHaveBeenCalledTimes(3);
    expect(apiClient.post).toHaveBeenCalledWith('/files/file-1/signed-url', { expire_minutes: 15 });
  });

  it('refreshes shared files without requesting owner credentials', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { data: [file(fresh)] } });
    expect(await getFileDownloadUrl({ ...file(expired), shared_session_id: 'shared-1' }))
      .toBe('http://app.test' + fresh);
    expect(apiClient.get).toHaveBeenCalledWith('/sessions/shared-1/share/files');
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('does not return an expired link when the shared file has been removed', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { data: [] } });
    await expect(getFileDownloadUrl({ ...file(expired), shared_session_id: 'shared-1' })).rejects.toThrow();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('marks shared history attachments and file-list entries for public renewal', async () => {
    const attached = file(expired);
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { data: { events: [{ event: 'message', data: { attachments: [attached] } }] } },
    });
    await getSharedSession('shared-1');
    expect(attached.shared_session_id).toBe('shared-1');
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { data: [file(fresh)] } });
    expect((await getSharedSessionFiles('shared-1'))[0].shared_session_id).toBe('shared-1');
  });

  it('loads metadata from the JSON info endpoint', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { data: file() } });
    expect((await getFileInfo('file-1'))?.file_id).toBe('file-1');
    expect(apiClient.get).toHaveBeenCalledWith('/files/file-1/info');
  });
});
