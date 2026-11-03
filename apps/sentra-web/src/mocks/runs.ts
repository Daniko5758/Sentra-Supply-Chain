// Simulasi perkembangan run agen di mode mock (C-07: "klik Scan now → status berjalan lalu selesai").
// Run yang dimulai lewat POST /api/risk/scan melewati QUEUED → RUNNING → hasil akhir dari fixture, satu tahap per
// polling (GET /api/runs/{id}). Run yang tidak dimulai di sesi ini (mis. run demo lain) tetap statis.
import type { AgentRun } from '../api/types';

const MOCK_RUN_STAGES = ['QUEUED', 'RUNNING'] as const;

/** Berapa kali tiap run sudah di-poll sejak dimulai. */
const polls = new Map<string, number>();

export function startMockRun(runId: string): void {
  polls.set(runId, 0);
}

export function resetMockRuns(): void {
  polls.clear();
}

/** Mengembalikan status tahap berikutnya untuk run yang sedang disimulasikan; selain itu fixture apa adanya. */
export function progressMockRun(run: AgentRun): AgentRun {
  const polled = polls.get(run.run_id);
  if (polled === undefined) return run;

  polls.set(run.run_id, polled + 1);
  const stage = MOCK_RUN_STAGES[polled];
  if (!stage) return run;

  return {
    ...run,
    status: stage,
    started_at: stage === 'QUEUED' ? null : run.started_at,
    finished_at: null,
    summary: null,
    error: null,
  };
}
