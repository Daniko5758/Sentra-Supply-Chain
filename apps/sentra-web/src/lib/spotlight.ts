import type { MouseEvent } from 'react';

/** Set --mx/--my pada elemen agar cahaya `.spotlight` mengikuti kursor (animation.md §4.9). Tanpa state React. */
export function trackSpotlight(e: MouseEvent<HTMLElement>): void {
  const el = e.currentTarget;
  const rect = el.getBoundingClientRect();
  el.style.setProperty('--mx', `${e.clientX - rect.left}px`);
  el.style.setProperty('--my', `${e.clientY - rect.top}px`);
}
