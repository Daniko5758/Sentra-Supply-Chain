import { useEffect, type RefObject } from 'react';

/**
 * Animasi muncul saat di-scroll (animation.md §4.15): setiap elemen `[data-reveal]` di dalam `root`
 * mendapat `data-revealed="true"` saat masuk layar. Gaya ada di animation.css.
 * Opsi repeat membuat elemen bersembunyi kembali saat keluar agar reveal mengikuti scroll naik/turun.
 * Tanpa IntersectionObserver (mis. jsdom) semua langsung ditampilkan.
 */
export function useScrollReveal(
  root: RefObject<HTMLElement | null>,
  deps: unknown[] = [],
  options: { repeat?: boolean } = {},
): void {
  const repeat = options.repeat ?? false;

  useEffect(() => {
    const container = root.current;
    if (!container) return;
    const selector = repeat ? '[data-reveal]' : '[data-reveal]:not([data-revealed])';
    const pending = [...container.querySelectorAll<HTMLElement>(selector)];
    if (pending.length === 0) return;

    if (typeof IntersectionObserver === 'undefined') {
      pending.forEach((el) => el.setAttribute('data-revealed', 'true'));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.setAttribute('data-revealed', 'true');
            if (!repeat) observer.unobserve(entry.target);
          } else if (repeat) {
            entry.target.removeAttribute('data-revealed');
          }
        }
      },
      { threshold: 0.12, rootMargin: repeat ? '-12% 0px -12% 0px' : '0px 0px -8% 0px' },
    );
    pending.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
    // deps: elemen baru (mis. setelah data dimuat) ikut diamati
  }, [...deps, repeat]);
}
