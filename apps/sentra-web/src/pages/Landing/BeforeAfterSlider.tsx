import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from 'react';
import { animate, m, useMotionValue, useReducedMotion, useSpring, useTransform, type MotionValue } from 'motion/react';

type Point = { x: number; y: number };
type ChipSpec = { label: string; row: number; col: number; scatter: Point & { rotate: number } };

// Tiga kolom (sinyal → dampak → aksi) × empat baris. Isi generik dan ilustratif, bukan data seed.
const COLUMNS = ['SIGNAL', 'IMPACT', 'ACTION'] as const;
const COLUMN_X = [17, 50, 83];
const ROW_Y = [33, 51, 69, 87];

const CHIPS: ChipSpec[] = [
  { label: 'Port congestion', row: 0, col: 0, scatter: { x: 22, y: 64, rotate: -6 } },
  { label: 'Plant fire report', row: 1, col: 0, scatter: { x: 70, y: 36, rotate: 5 } },
  { label: 'Export policy change', row: 2, col: 0, scatter: { x: 40, y: 82, rotate: -3 } },
  { label: 'Supplier delay notice', row: 3, col: 0, scatter: { x: 86, y: 72, rotate: 7 } },
  { label: 'Tier-2 supplier', row: 0, col: 1, scatter: { x: 58, y: 58, rotate: 4 } },
  { label: 'Tier-1 supplier', row: 1, col: 1, scatter: { x: 14, y: 36, rotate: -5 } },
  { label: 'Key part', row: 2, col: 1, scatter: { x: 76, y: 88, rotate: -4 } },
  { label: 'Production line', row: 3, col: 1, scatter: { x: 31, y: 46, rotate: 6 } },
  { label: 'Open sales orders', row: 0, col: 2, scatter: { x: 88, y: 46, rotate: -7 } },
  { label: 'Recovery options', row: 1, col: 2, scatter: { x: 49, y: 32, rotate: 3 } },
  { label: 'Stakeholder meeting', row: 2, col: 2, scatter: { x: 12, y: 84, rotate: 5 } },
  { label: 'Approval trail', row: 3, col: 2, scatter: { x: 62, y: 76, rotate: -5 } },
];

const clamp = (value: number) => Math.min(1, Math.max(0, value));

/** True setelah elemen pertama kali terlihat. Tanpa IntersectionObserver (mis. jsdom) tetap false. */
function useSeenOnce(ref: RefObject<HTMLElement | null>): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { threshold: 0.55 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return seen;
}

function Chip({ chip, index, progress }: { chip: ChipSpec; index: number; progress: MotionValue<number> }) {
  // Setiap chip mulai bergerak sedikit berbeda waktunya, jadi tatanan muncul bertahap, bukan serentak.
  const delay = (index % 4) * 0.07;
  const range: [number, number] = [delay, delay + 0.65];
  const left = useTransform(progress, range, [`${chip.scatter.x}%`, `${COLUMN_X[chip.col]}%`], { clamp: true });
  const top = useTransform(progress, range, [`${chip.scatter.y}%`, `${ROW_Y[chip.row]}%`], { clamp: true });
  const rotate = useTransform(progress, range, [chip.scatter.rotate, 0], { clamp: true });
  const borderColor = useTransform(progress, range, ['rgba(244, 164, 84, 0.7)', 'rgba(71, 212, 223, 0.75)'], { clamp: true });
  const color = useTransform(progress, range, ['#ffd9b0', '#d9f8fc'], { clamp: true });
  const background = useTransform(progress, range, ['#3b3a3f', '#124a5e'], { clamp: true });

  return (
    <m.span className="landing-contrast__chip" style={{ left, top, rotate, borderColor, color, background, x: '-50%', y: '-50%' }}>
      {chip.label}
    </m.span>
  );
}

function Connector({ row, from, progress }: { row: number; from: number; progress: MotionValue<number> }) {
  const scaleX = useTransform(progress, [0.5, 0.85], [0, 1], { clamp: true });
  const opacity = useTransform(progress, [0.45, 0.7], [0, 1], { clamp: true });
  return (
    <m.i
      className="landing-contrast__connector"
      aria-hidden="true"
      style={{ left: `${COLUMN_X[from]}%`, top: `${ROW_Y[row]}%`, width: `${COLUMN_X[from + 1] - COLUMN_X[from]}%`, scaleX, opacity }}
    />
  );
}

/**
 * Slider "tanpa SENTRA ↔ dengan SENTRA": geser untuk melihat alert yang berserakan tertata menjadi
 * rantai sinyal → dampak → aksi. Posisi tiap chip dihitung dari satu nilai (0–1), jadi bisa diseret,
 * diklik, atau dipakai lewat keyboard.
 */
export function BeforeAfterSlider() {
  const reduceMotion = useReducedMotion();
  const raw = useMotionValue(0);
  const spring = useSpring(raw, { stiffness: 150, damping: 24, mass: 0.7 });
  const progress = reduceMotion ? raw : spring;

  const rootRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const touched = useRef(false);
  const hintControls = useRef<ReturnType<typeof animate> | null>(null);
  const [value, setValue] = useState(0);
  const inView = useSeenOnce(rootRef);

  const beforeOpacity = useTransform(progress, [0, 0.45], [1, 0], { clamp: true });
  const afterOpacity = useTransform(progress, [0.5, 0.85], [0, 1], { clamp: true });
  const headersOpacity = useTransform(progress, [0.6, 0.85], [0, 1], { clamp: true });
  const knobLeft = useTransform(progress, (v) => `${clamp(v) * 100}%`);
  const fillScale = useTransform(progress, (v) => clamp(v));

  // Satu dorongan kecil saat bagian ini pertama terlihat, agar pengunjung tahu slider bisa digeser.
  useEffect(() => {
    if (!inView || reduceMotion || touched.current) return;
    const timer = window.setTimeout(() => {
      if (touched.current) return;
      hintControls.current = animate(raw, [0, 0.32, 0], { duration: 1.8, ease: 'easeInOut' });
    }, 500);
    return () => {
      window.clearTimeout(timer);
      hintControls.current?.stop();
    };
  }, [inView, reduceMotion, raw]);

  const commit = (next: number) => {
    const target = clamp(next);
    touched.current = true;
    hintControls.current?.stop();
    raw.set(target);
    setValue(target);
  };

  const fromClientX = (clientX: number) => {
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    commit((clientX - rect.left) / rect.width);
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    fromClientX(event.clientX);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (dragging.current) fromClientX(event.clientX);
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = 0.1;
    if (event.key === 'ArrowRight' || event.key === 'ArrowUp') commit(value + step);
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') commit(value - step);
    else if (event.key === 'Home') commit(0);
    else if (event.key === 'End') commit(1);
    else return;
    event.preventDefault();
  };

  return (
    <div className="landing-contrast" ref={rootRef}>
      <div className="landing-contrast__stage" role="img" aria-label="Illustration: scattered alerts become one connected chain from signal to impact to action as the slider moves toward With SENTRA">
        <m.span className="landing-contrast__state landing-contrast__state--before" style={{ opacity: beforeOpacity }}>
          WITHOUT SENTRA <small>Scattered, unlinked alerts</small>
        </m.span>
        <m.span className="landing-contrast__state landing-contrast__state--after" style={{ opacity: afterOpacity }}>
          WITH SENTRA <small>One connected picture</small>
        </m.span>

        {COLUMNS.map((name, col) => (
          <m.span
            className="landing-contrast__column"
            key={name}
            aria-hidden="true"
            style={{ left: `${COLUMN_X[col]}%`, opacity: headersOpacity, x: '-50%' }}
          >
            {name}
          </m.span>
        ))}

        {ROW_Y.flatMap((_, row) => [0, 1].map((from) => <Connector key={`${row}-${from}`} row={row} from={from} progress={progress} />))}
        {CHIPS.map((chip, index) => <Chip chip={chip} index={index} key={chip.label} progress={progress} />)}

        <span className="landing-contrast__badge">ILLUSTRATIVE</span>
      </div>

      <div className="landing-contrast__control">
        <span className="landing-contrast__end">Without SENTRA</span>
        <div
          className="landing-contrast__track"
          ref={trackRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <m.i className="landing-contrast__fill" style={{ scaleX: fillScale }} />
          <m.button
            type="button"
            className="landing-contrast__knob"
            role="slider"
            aria-label="Compare without and with SENTRA"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(value * 100)}
            aria-valuetext={value >= 0.5 ? 'With SENTRA' : 'Without SENTRA'}
            style={{ left: knobLeft }}
            onKeyDown={onKeyDown}
          >
            <span aria-hidden="true">⇆</span>
          </m.button>
        </div>
        <span className="landing-contrast__end landing-contrast__end--after">With SENTRA</span>
      </div>
    </div>
  );
}
