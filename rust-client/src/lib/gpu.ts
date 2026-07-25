// One-time probe for *usable* WebGPU. `navigator.gpu` can exist while
// requestAdapter() returns null (GPU disabled / blocklisted / hardware
// acceleration off). Both the CLIP backend and the viewer key off this so a dead
// GPU degrades to the CPU path cleanly — instead of half-initialising a wgpu
// surface on the canvas and then leaving it blank when the device request fails.

let probe: Promise<boolean> | null = null

export function webgpuUsable(): Promise<boolean> {
  if (!probe) {
    probe = (async () => {
      const gpu = (navigator as unknown as { gpu?: { requestAdapter(): Promise<unknown> } }).gpu
      if (!gpu) return false
      try {
        return (await gpu.requestAdapter()) !== null
      } catch {
        return false
      }
    })()
  }
  return probe
}
