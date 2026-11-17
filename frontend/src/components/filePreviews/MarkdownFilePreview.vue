<template>
    <div class="relative overflow-auto flex-1 min-h-0 p-5">
        <div class="relative w-full max-w-[768px] mx-auto" style="min-height: calc(-200px + 100vh);">
            <div class="prose prose-gray max-w-none dark:prose-invert" 
                 v-html="renderedContent">
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import type { FileInfo } from '../../api/file';
import { getFileDownloadUrl } from '../../api/file';

const content = ref('');

const props = defineProps<{
    file: FileInfo;
}>();

// Configure marked options
marked.setOptions({
    breaks: true,
    gfm: true,
});

// Compute rendered HTML content
const renderedContent = computed(() => {
    if (!content.value) return '';
    try {
        const html = marked.parse(content.value);
        return DOMPurify.sanitize(html as string);
    } catch (error) {
        console.error('Failed to render markdown:', error);
        return `<pre class="text-sm text-red-500">Failed to render markdown content</pre>`;
    }
});

watch(() => props.file, async (file, _, onCleanup) => {
    content.value = '';
    if (!file?.file_id) return;
    const controller = new AbortController();
    onCleanup(() => controller.abort());
    try {
        const url = await getFileDownloadUrl(file);
        if (controller.signal.aborted) return;
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const text = await response.text();
        if (!controller.signal.aborted) content.value = text;
    } catch (error) {
        if (controller.signal.aborted) return;
        console.error('Failed to load file content:', error);
        content.value = '(Failed to load file content)';
    }
}, { immediate: true, deep: false });
</script>
