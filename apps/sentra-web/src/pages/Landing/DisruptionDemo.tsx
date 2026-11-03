import { useCallback, useEffect, useRef, useState } from 'react';
import { m, useReducedMotion } from 'motion/react';

// Rantai dampak generik (tier-2 → tier-1 → part → produk → sales order). Tidak ada nama atau angka dari seed.
const NODES = ['Tier-2 supplier', 'Tier-1 supplier', 'Part', 'Product', 'Sales order'] as const;
const OPTIONS = [
  { label: 'Reroute supply', tag: 'REVIEW' },
  { label: 'Expedite order', tag: 'READY' },
] as const;

const SPREAD_STEP_MS = 420;
const RESTORE_STEP_MS = 300;

type Phase = 'idle' | 'spreading' | 'exposed' | 'recovering' | 'resolved';

type StatusCopy = { tone: 'calm' | 'alert' | 'ok'; icon: string; kicker: string; title: string; chip: string };

function statusCopy(phase: Phase, reached: number): StatusCopy {
  if (phase === 'idle') {
    return { tone: 'calm', icon: '•', kicker: 'Network monitoring', title: 'No active risk signal', chip: 'Calm' };
  }
  if (phase === 'spreading') {
    return {
      tone: 'alert',
      icon: '!',
      kicker: 'Risk signal detected',
      title: reached >= 2 ? 'Source verified' : 'Checking source…',
      chip: 'New',
    };
  }
  if (phase === 'exposed') {
    return { tone: 'alert', icon: '!', kicker: 'Risk signal detected', title: 'Exposure reached orders', chip: 'Exposed' };
  }
  return { tone: 'ok', icon: '✓', kicker: 'Recovery options', title: 'Ready for human review', chip: 'Review' };
}

/**
 * Peragaan interaktif di kartu hero: pengunjung memicu gangguan di supplier, dampaknya merambat
 * sampai sales order, lalu opsi pemulihan muncul. Murni ilustrasi: tidak ada angka bisnis,
 * tidak memanggil API, dan urutan state dijalankan timer biasa (bukan animationend).
 */
export function DisruptionDemo() {
  const reduceMotion = useReducedMotion();
  const [phase, setPhase] = useState<Phase>('idle');
  const [reached, setReached] = useState(0);
  const [restored, setRestored] = useState(0);
  const timers = useRef<number[]>([]);

  const clearTimers = useCallback(() => {
    timers.current.forEach((id) => window.clearTimeout(id));
    timers.current = [];
  }, []);
  useEffect(() => clearTimers, [clearTimers]);

  const schedule = (fn: () => void, ms: number) => {
    timers.current.push(window.setTimeout(fn, ms));
  };

  const spreadStep = reduceMotion ? 0 : SPREAD_STEP_MS;
  const restoreStep = reduceMotion ? 0 : RESTORE_STEP_MS;

  const trigger = () => {
    if (phase !== 'idle') return;
    clearTimers();
    setPhase('spreading');
    setReached(1);
    for (let count = 2; count <= NODES.length; count += 1) {
      schedule(() => setReached(count), (count - 1) * spreadStep);
    }
    schedule(() => setPhase('exposed'), (NODES.length - 1) * spreadStep + (reduceMotion ? 0 : 360));
  };

  const recover = () => {
    if (phase !== 'exposed') return;
    clearTimers();
    setPhase('recovering');
    for (let count = 1; count <= NODES.length; count += 1) {
      schedule(() => setRestored(count), (count - 1) * restoreStep);
    }
    schedule(() => setPhase('resolved'), (NODES.length - 1) * restoreStep + (reduceMotion ? 0 : 320));
  };

  const reset = () => {
    clearTimers();
    setPhase('idle');
    setReached(0);
    setRestored(0);
  };

  const status = statusCopy(phase, reached);

  return (
    <>
      <article className="landing-signal-card landing-signal-card--demo" data-tone={status.tone} aria-live="polite">
        <span className="landing-signal-card__icon" aria-hidden="true">{status.icon}</span>
        <div>
          <p>{status.kicker}</p>
          <strong>{status.title}</strong>
        </div>
        <span className="landing-signal-card__status">{status.chip}</span>
      </article>

      <article className="landing-impact-card landing-impact-card--demo" data-phase={phase} aria-label="Illustrative impact path simulation">
        <div className="landing-chain__head">
          <p>Impact path</p>
          <span className="landing-chain__tag">ILLUSTRATIVE</span>
        </div>

        <ol className="landing-chain__nodes">
          {NODES.map((label, index) => {
            const hit = reached > index;
            const safe = restored > index;
            const state = safe ? 'safe' : hit ? 'hit' : 'idle';
            const isTrigger = index === 0;
            const dot = (
              <span className="landing-chain__dot" data-state={state}>
                {isTrigger && phase === 'idle' && (
                  <m.i
                    className="landing-chain__hint-ring"
                    aria-hidden="true"
                    initial={{ scale: 1, opacity: reduceMotion ? 0.5 : 0.6 }}
                    animate={reduceMotion ? undefined : { scale: 2.6, opacity: 0 }}
                    transition={{ duration: 1.7, repeat: 4, ease: 'easeOut' }}
                  />
                )}
                {hit && !safe && (
                  <m.i
                    key="ripple"
                    className="landing-chain__ripple"
                    aria-hidden="true"
                    initial={{ scale: 1, opacity: 0.7 }}
                    animate={{ scale: 3, opacity: 0 }}
                    transition={{ duration: 0.8, ease: 'easeOut' }}
                  />
                )}
                <m.b
                  className="landing-chain__core"
                  animate={{ scale: hit && !safe ? [1, 1.45, 1] : 1 }}
                  transition={{ duration: 0.45, ease: 'easeOut' }}
                />
              </span>
            );

            return (
              <li className="landing-chain__node" data-state={state} key={label}>
                {index > 0 && (
                  <span className="landing-chain__link" aria-hidden="true">
                    <m.i
                      className="landing-chain__link-fill landing-chain__link-fill--hit"
                      initial={false}
                      animate={{ scaleX: reached > index ? 1 : 0 }}
                      transition={{ duration: 0.38, ease: 'easeOut' }}
                    />
                    <m.i
                      className="landing-chain__link-fill landing-chain__link-fill--safe"
                      initial={false}
                      animate={{ scaleX: restored > index ? 1 : 0 }}
                      transition={{ duration: 0.28, ease: 'easeOut' }}
                    />
                  </span>
                )}
                {isTrigger ? (
                  <button
                    type="button"
                    className="landing-chain__trigger"
                    onClick={trigger}
                    disabled={phase !== 'idle'}
                    aria-label="Simulate a supplier disruption"
                  >
                    {dot}
                    <span className="landing-chain__label">{label}</span>
                  </button>
                ) : (
                  <div className="landing-chain__static">
                    {dot}
                    <span className="landing-chain__label">{label}</span>
                  </div>
                )}
              </li>
            );
          })}
        </ol>

        <div className="landing-chain__actions">
          {phase === 'idle' && <span className="landing-chain__hint">Select the first supplier to simulate a disruption.</span>}
          {phase === 'spreading' && <span className="landing-chain__hint">Mapping exposure through the network…</span>}
          {phase === 'exposed' && (
            <>
              <span className="landing-chain__hint">The signal reached open orders.</span>
              <button type="button" className="landing-chain__button" onClick={recover}>Recover <span aria-hidden="true">→</span></button>
            </>
          )}
          {phase === 'recovering' && <span className="landing-chain__hint">Reconnecting the path…</span>}
          {phase === 'resolved' && (
            <>
              <ul className="landing-chain__options" aria-label="Recovery options">
                {OPTIONS.map((option, index) => (
                  <m.li
                    key={option.label}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.35, delay: reduceMotion ? 0 : index * 0.14, ease: 'easeOut' }}
                  >
                    {option.label} <b>{option.tag}</b>
                  </m.li>
                ))}
              </ul>
              <span className="landing-chain__note">Proposal only — not executed</span>
              <button type="button" className="landing-chain__reset" onClick={reset}>Replay</button>
            </>
          )}
        </div>
      </article>
    </>
  );
}
