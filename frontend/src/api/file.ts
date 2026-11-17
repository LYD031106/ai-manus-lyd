// File API service
import { apiClient, ApiResponse, API_CONFIG } from './client';
import type { SharedSessionResponse, SignedUrlResponse } from '../types/response';

/**
 * File info type
 */
export interface FileInfo {
  file_id: string;
  filename: string;
  content_type?: string;
  size?: number;
  upload_date: string;
  metadata?: Record<string, any>;
  file_url?: string;
  /** Client-side context for files opened from a public shared conversation. */
  shared_session_id?: string;
}



/**
 * Upload file
 * @param file File to upload
 * @param metadata Optional metadata
 * @returns Upload result
 */
export async function uploadFile(file: File, metadata?: Record<string, any>): Promise<FileInfo> {
  const formData = new FormData();
  formData.append('file', file);
  
  if (metadata) {
    formData.append('metadata', JSON.stringify(metadata));
  }

  const response = await apiClient.post<ApiResponse<FileInfo>>('/files', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return response.data.data;
}

/**
 * Download file
 * @param fileId File ID
 * @returns File download result
 */
export async function downloadFile(fileId: string): Promise<Blob> {
  const response = await apiClient.get(`/files/${fileId}/download`, {
    responseType: 'blob',
  });
  
  return response.data;
}

/**
 * Delete file
 * @param fileId File ID
 * @returns Success status
 */
export async function deleteFile(fileId: string): Promise<boolean> {
  try {
    await apiClient.delete<ApiResponse<void>>(`/files/${fileId}`);
    return true;
  } catch (error) {
    console.error('Failed to delete file:', error);
    return false;
  }
}

/**
 * Get file information
 * @param fileId File ID
 * @returns File information or null if not found
 */
export async function getFileInfo(fileId: string): Promise<FileInfo | null> {
  try {
    const response = await apiClient.get<ApiResponse<FileInfo>>(`/files/${fileId}/info`);
    return response.data.data;
  } catch (error) {
    console.error('Failed to get file info:', error);
    return null;
  }
}

/**
 * Create file signed URL
 * @param fileId File ID to create signed URL for
 * @param expireMinutes URL expiration time in minutes (default: 15)
 * @returns Signed URL response for file download
 */
export async function createFileSignedUrl(fileId: string, expireMinutes: number = 15): Promise<SignedUrlResponse> {
  const response = await apiClient.post<ApiResponse<SignedUrlResponse>>(`/files/${fileId}/signed-url`, {
    expire_minutes: expireMinutes
  });
  return response.data.data;
}

/**
 * Get a fresh file download URL at the time of preview/download.
 * file_url is a temporary capability, not a permanent address. In particular,
 * a page or attachment may outlive the signature it was originally sent with.
 * @param file File info
 * @returns Promise resolving to file download URL string
 */
export async function getFileDownloadUrl(
  fileInfo: FileInfo,
): Promise<string> {
  let url: string | undefined;
  if (fileInfo.shared_session_id) {
    // Public viewers cannot use the owner-only signing endpoint. Recheck that
    // the conversation is still shared, and select this exact file version.
    const sessionId = encodeURIComponent(fileInfo.shared_session_id);
    const response = await apiClient.get<ApiResponse<FileInfo[]>>(`/sessions/${sessionId}/share/files`);
    url = response.data.data.find(file => file.file_id === fileInfo.file_id)?.file_url;
    if (!url) {
      // A newer file may have replaced the same sandbox path in session.files;
      // the earlier attachment remains downloadable from the shared history.
      const history = await apiClient.get<ApiResponse<SharedSessionResponse>>(`/sessions/shared/${sessionId}`);
      for (const event of history.data.data.events) {
        if ('attachments' in event.data) {
          url = event.data.attachments?.find(file => file.file_id === fileInfo.file_id)?.file_url;
          if (url) break;
        }
      }
    }
  } else {
    const signedUrlResponse = await createFileSignedUrl(fileInfo.file_id);
    url = signedUrlResponse.signed_url;
  }
  if (!url) throw new Error('File is no longer available');
  // The API normally returns a relative path; do not prefix absolute URLs twice.
  return /^https?:\/\//i.test(url)
    ? url
    : `${API_CONFIG.host.replace(/\/$/, '')}/${url.replace(/^\//, '')}`;
}
