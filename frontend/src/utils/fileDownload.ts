import { getFileDownloadUrl, type FileInfo } from '../api/file'
import { i18n } from '../composables/useI18n'
import { showErrorToast } from './toast'

/** Call directly from the click handler while the browser still permits a new tab. */
export async function openFileDownload(file: FileInfo): Promise<void> {
  const popup = window.open('about:blank', '_blank')
  try {
    if (popup) popup.opener = null
    const url = await getFileDownloadUrl(file)
    if (popup) {
      if (!popup.closed) popup.location.replace(url)
      return
    }

    // A blocked popup leaves no window handle. The endpoint serves an attachment.
    const link = document.createElement('a')
    link.href = url
    link.download = file.filename
    link.rel = 'noreferrer'
    document.body.appendChild(link)
    try {
      link.click()
    } finally {
      link.remove()
    }
  } catch {
    if (popup && !popup.closed) popup.close()
    showErrorToast(i18n.global.t('Failed to load file content'))
  }
}
