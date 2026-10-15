import { describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { useCapabilities } from '../useCapabilities'
import { getCachedClientConfig, type ClientConfigResponse } from '../../api/config'

vi.mock('../../api/config', () => ({ getCachedClientConfig: vi.fn() }))
const Probe = defineComponent({
  setup: useCapabilities,
  template: '<button v-if="browserEnabled">Browser</button>',
})

describe('browser runtime capability', () => {
  it.each([
    [false, false], [true, true], [undefined, true],
  ])('uses browser_enabled=%s, including older server compatibility', async (flag, visible) => {
    vi.mocked(getCachedClientConfig).mockResolvedValue({ browser_enabled: flag } as ClientConfigResponse)
    const wrapper = mount(Probe)
    expect(wrapper.find('button').exists()).toBe(false)
    await flushPromises()
    expect(wrapper.find('button').exists()).toBe(visible)
    wrapper.unmount()
  })

  it('keeps browser actions hidden when configuration could not be loaded', async () => {
    vi.mocked(getCachedClientConfig).mockResolvedValue(null)
    const wrapper = mount(Probe)
    await flushPromises()
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })
})
