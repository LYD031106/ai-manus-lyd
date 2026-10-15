import { onMounted, ref } from 'vue'
import { getCachedClientConfig } from '../api/config'

/** Hide optional applications until their runtime capability is known. */
export function useCapabilities() {
  const browserEnabled = ref(false)
  onMounted(async () => {
    const config = await getCachedClientConfig()
    // Missing field supports servers predating the capability flag.
    browserEnabled.value = config !== null && config.browser_enabled !== false
  })
  return { browserEnabled }
}
