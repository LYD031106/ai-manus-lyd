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

watch(() => props.file, async (file) => {
  if (!file?.file_id) return
  try {
    const url = await getFileDownloadUrl(file)
    const response = await fetch(url)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    content.value = await response.text()
  } catch (error) {
    console.error('Failed to load file content:', error)
    content.value = '(Failed to load file content)'
  }
}, { immediate: true, deep: false })
</script>
