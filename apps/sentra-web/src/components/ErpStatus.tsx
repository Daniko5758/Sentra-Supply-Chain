import type { ActionStatus } from '../api/types';

export type ErpTone = 'success' | 'warning' | 'error' | 'information' | 'neutral';

// C-51 (P7): status aksi ERP → nada semantik gaya Fiori ObjectStatus. Selalu dengan teks, warna tidak pernah sendirian.
const STATUS: Record<ActionStatus, { label: string; tone: ErpTone }> = {
  PROPOSED: { label: 'Proposed', tone: 'warning' },
  APPROVED: { label: 'Approved', tone: 'information' },
  EXECUTED: { label: 'Executed', tone: 'success' },
  FAILED: { label: 'Failed', tone: 'error' },
  REJECTED: { label: 'Rejected', tone: 'neutral' },
};

export function erpStatusTone(status: string): ErpTone {
  return STATUS[status as ActionStatus]?.tone ?? 'neutral';
}

/** Hanya untuk ERP log, Approvals, dan hasil eksekusi (di dalam ErpShell; gaya di erp-shell.css). Status lain tampil apa adanya. */
export function ErpStatus({ status }: { status: string }) {
  const known = STATUS[status as ActionStatus];
  return <span className={`erp-status erp-status--${known?.tone ?? 'neutral'}`}>{known?.label ?? status}</span>;
}
