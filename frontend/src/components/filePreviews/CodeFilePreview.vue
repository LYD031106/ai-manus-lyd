<template>
  <pre
    class="h-full w-full min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[13px] leading-5 text-[var(--text-primary)] bg-[var(--background-gray-main)]"
  >{{ content }}</pre>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { FileInfo } from '../../api/file'
import { getFileDownloadUrl } from '../../api/file'

const content = ref('')

const props = defineProps<{
  file: FileInfo
}>()

watch(() => props.file, async (file, _, onCleanup) => {
  content.value = ''
  if (!file?.file_id) return
  const controller = new AbortController()
  onCleanup(() => controller.abort())
  try {
    const url = await getFileDownloadUrl(file)
    if (controller.signal.aborted) return
    const response = await fetch(url, { signal: controller.signal })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const text = await response.text()
    if (!controller.signal.aborted) content.value = text
  } catch (error) {
    if (controller.signal.aborted) return
    console.error('Failed to load file content:', error)
    content.value = '(Failed to load file content)'
  }
}, { immediate: true, deep: false })
</script>
