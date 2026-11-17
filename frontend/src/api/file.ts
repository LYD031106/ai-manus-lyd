// File API service
import { apiClient, ApiResponse, API_CONFIG } from './client';
import { SignedUrlResponse } from '../types/response';

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
 * Get file download URL
 * @param file File info
 * @returns Promise resolving to file download URL string
 */
export async function getFileDownloadUrl(
  fileInfo: FileInfo,
): Promise<string> {
  if (fileInfo.file_url) {
    const url = new URL(fileInfo.file_url, API_CONFIG.host || window.location.origin);
    const expires = Number(url.searchParams.get('expires'));
    if (Number.isFinite(expires) && expires > Date.now() / 1000 + 60) {
      return url.href;
    }
  }
  if (fileInfo.shared_session_id) {
    const response = await apiClient.get<ApiResponse<FileInfo[]>>(
      `/sessions/${fileInfo.shared_session_id}/share/files`,
    );
    const refreshed = response.data.data.find(file => file.file_id === fileInfo.file_id);
    if (!refreshed?.file_url) {
      throw new Error('File is no longer available in this shared session');
    }
    fileInfo.file_url = refreshed.file_url;
  } else {
    const signedUrlResponse = await createFileSignedUrl(fileInfo.file_id);
    fileInfo.file_url = signedUrlResponse.signed_url;
  }
  return new URL(fileInfo.file_url, API_CONFIG.host || window.location.origin).href;
}
