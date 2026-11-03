/** True bila browser bisa membuat konteks WebGL. Dicek sebelum chunk three.js diunduh. */
export function webglAvailable(): boolean {
  if (typeof window === 'undefined' || !('WebGLRenderingContext' in window)) return false;
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}
