import type { DecisionOption, Role } from '../api/types';

/** Option of a meeting decision as text; Option X = the meeting chose its own ERP actions. */
export function decisionOptionLabel(option: DecisionOption | null | undefined): string {
  if (!option) return 'No option';
  return option === 'X' ? 'Custom ERP actions (Option X)' : `Option ${option}`;
}

// Label UI enum `role` (API_CONTRACT A.4).
export const ROLE_LABELS: Record<Role, string> = {
  BUYER: 'Buyer',
  PPIC: 'PPIC',
  SALES: 'Sales',
  SCM_MANAGER: 'SCM Manager',
};
