import type { HeatmapCountry, Level } from '../../api/types';
import { formatScore } from '../../lib/format';

// Warna sama dengan halaman Risk heatmap (C-10); selalu disertai teks di label dan legenda.
export const GLOBE_COLORS = {
  HIGH: '#c83e4d',
  MEDIUM: '#e28a2b',
  LOW: '#626c7a',
  noCase: '#7fb5e8',
  noSupplier: '#c3cdd9',
} as const;

export function levelColor(level: Level | null): string {
  return level ? GLOBE_COLORS[level] : GLOBE_COLORS.noCase;
}

/** Skor kasus terbuka tertinggi pada level tertinggi negara itu (dari API, hanya dipilih untuk tampilan). */
export function highestScore(country: HeatmapCountry): number | null {
  const scores = country.open_cases
    .filter((openCase) => openCase.level === country.highest_level)
    .map((openCase) => openCase.score);
  return scores.length > 0 ? Math.max(...scores) : null;
}

/** Teks status negara: "HIGH 79.7" atau "No open case". */
export function riskText(country: HeatmapCountry): string {
  if (!country.highest_level) return 'No open case';
  const score = highestScore(country);
  return score === null ? country.highest_level : `${country.highest_level} ${formatScore(score)}`;
}
