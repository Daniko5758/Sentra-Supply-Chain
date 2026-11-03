// Tombol "?" di KPI Priority score: menjelaskan rumus R-07b dengan angka supplier yang skornya menjadi skor kasus.
// Semua angka (komponen, skor) dari API (G.5 PriorityRow); UI hanya menampilkan bobot dan batas rumus, tidak menghitung skor.
import { useEffect, useId, useRef, useState } from 'react';
import type { PriorityRow } from '../../api/types';
import { formatIDR, formatScore } from '../../lib/format';

// R-07b (RULES_AND_GOLDEN section 3): bobot dan batas normalisasi, sama dengan sentra_api/priority.py.
const WEIGHTS = { rar: '0.40', ttr: '0.25', bcp: '0.20', tier: '0.15' } as const;

/** Baris supplier yang skornya menjadi skor kasus (skor tertinggi; seri: site_id), atau null bila tidak ada kandidat. */
export function topPriorityRow(rows: PriorityRow[]): PriorityRow | null {
  return rows.reduce<PriorityRow | null>(
    (best, row) => (best === null || row.score > best.score || (row.score === best.score && row.site_id < best.site_id) ? row : best),
    null,
  );
}

export function ScoreHelp({ priority }: { priority: PriorityRow[] }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const top = topPriorityRow(priority);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    const onClick = (event: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [open]);

  return (
    <span className="case-score-help__wrap" ref={wrapperRef}>
      <button
        className="case-score-help"
        type="button"
        title="How is this score calculated?"
        aria-label="How is this score calculated?"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >?
      </button>
      {open && (
        <span className="case-score-help__panel" id={panelId} role="dialog" aria-label="How the priority score is calculated">
          <span className="case-score-help__title">How the priority score is calculated</span>
          <span className="case-score-help__formula">
            Score = {WEIGHTS.rar} × Revenue at risk + {WEIGHTS.ttr} × Recovery time + {WEIGHTS.bcp} × BCP gap
            + {WEIGHTS.tier} × Tier
          </span>
          <span className="case-score-help__note">
            Each part is scaled to 0–100. The case score is the highest score of its supplier sites.
          </span>
          {top && (
            <span className="case-score-help__table" role="table" aria-label={`Score parts for ${top.site_id}`}>
              <span className="case-score-help__site" role="caption">
                {top.site_id} · {top.supplier_name} · score {formatScore(top.score)}
              </span>
              <ScorePart label="Revenue at risk" detail={`${formatIDR(top.rar)} (IDR 1B or more = 100)`} weight={WEIGHTS.rar}
                points={top.components.n_rar} />
              <ScorePart label="Recovery time" detail={`${top.recovery_days} days (30 days or more = 100)`} weight={WEIGHTS.ttr}
                points={top.components.n_ttr} />
              <ScorePart label="BCP gap" detail={`100 − BCP score ${formatScore(top.bcp_score)}`} weight={WEIGHTS.bcp}
                points={top.components.n_bcp} />
              <ScorePart label="Tier" detail={`Tier ${top.tier} (Tier 1 = 100, Tier 2 = 60)`} weight={WEIGHTS.tier}
                points={top.components.n_tier} />
            </span>
          )}
          <span className="case-score-help__note">Level: HIGH from 70 · MEDIUM from 40 · LOW below 40.</span>
        </span>
      )}
    </span>
  );
}

function ScorePart({ label, detail, weight, points }: { label: string; detail: string; weight: string; points: number }) {
  return (
    <span className="case-score-help__row" role="row">
      <span role="cell"><strong>{label}</strong><small>{detail}</small></span>
      <span role="cell" className="tabular">{formatScore(points)} × {weight}</span>
    </span>
  );
}
