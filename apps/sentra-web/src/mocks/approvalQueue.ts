// C-51: antrean Approvals di mode mock mengikuti keputusan sesi ini, seperti EP-22 asli:
// aksi EXECUTED/REJECTED keluar dari GET /api/approvals; FAILED tetap dengan erp_result dan error.
// Respons disalin dari fixture yang ada (approve-all dan submit); tidak ada angka atau teks pesan baru.
import type { ActionResult, ApprovalItem } from '../api/types';
import approvalsFixture from './approvals.json';

const approveAllFiles = import.meta.glob<{ default: unknown }>('./cases_*_options_*_approve-all.json', { eager: true });
const submitFiles = import.meta.glob<{ default: unknown }>('./cases_*_recommendations_*_submit.json', { eager: true });

/** action_id → respons terakhir di sesi ini. Hilang saat reload, seperti cache browser. */
const decided = new Map<string, ActionResult>();

export function resetMockApprovals(): void {
  decided.clear();
}

function executedFixtures(): ActionResult[] {
  return Object.values(approveAllFiles).flatMap((module) => module.default as ActionResult[]);
}

function submittedFixtures(): ActionResult[] {
  return Object.values(submitFiles).flatMap((module) => (module.default as { actions: ActionResult[] }).actions);
}

/** GET /api/approvals: fixture dikurangi aksi yang sudah EXECUTED/REJECTED; status FAILED membawa erp_result dan error. */
export function mockApprovalQueue(queue: ApprovalItem[]): ApprovalItem[] {
  return queue.flatMap((item) => {
    const result = decided.get(item.action_id);
    if (!result) return [item];
    if (result.status === 'EXECUTED' || result.status === 'REJECTED') return [];
    return [{ ...item, status: result.status, erp_result: result.erp_result ?? null, error: result.error ?? null }];
  });
}

/** POST /api/actions/{id}/{approve|retry|reject}. `null` = tidak ada padanan; client memakai fixture per aksi seperti dulu. */
export function mockDecision(actionId: string, decision: 'approve' | 'reject' | 'retry'): ActionResult | null {
  const found = decision === 'reject'
    ? submittedFixtures().find((action) => action.action_id === actionId)
    : executedFixtures().find((action) => action.action_id === actionId);
  if (!found) return null;
  const result: ActionResult = decision === 'reject'
    ? { ...structuredClone(found), status: 'REJECTED', erp_result: null, error: null } // ERP tidak dipanggil saat reject
    : structuredClone(found);
  decided.set(actionId, result);
  return result;
}

/** POST /api/cases/{id}/options/{option}/approve-all: seperti backend, hanya aksi yang masih PROPOSED. `null` = tanpa fixture. */
export function mockApproveAll(caseId: string, option: string): ActionResult[] | null {
  const module = approveAllFiles[`./cases_${caseId}_options_${option}_approve-all.json`];
  if (!module) return null;
  const waiting = new Set(
    mockApprovalQueue(structuredClone(approvalsFixture) as unknown as ApprovalItem[])
      .filter((item) => item.case_id === caseId && item.option === option && item.status === 'PROPOSED')
      .map((item) => item.action_id),
  );
  const results = (structuredClone(module.default) as ActionResult[]).filter((action) => waiting.has(action.action_id));
  for (const result of results) decided.set(result.action_id, result);
  return results;
}
