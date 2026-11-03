import type { Mode, SystemModeStatus } from '../api/types';
import liveScanRun from './runs_RUN-20261031-0003.json';

const MODE_KEY = 'sentra.mock.application-mode';
const SCAN_INTERVAL_MIN = 120;

function storage(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage;
}

function readState(): Pick<SystemModeStatus, 'mode' | 'live_since' | 'changed_by'> {
  const stored = storage()?.getItem(MODE_KEY);
  if (!stored) return { mode: 'DEMO', live_since: null, changed_by: null };

  try {
    const value: unknown = JSON.parse(stored);
    if (!value || typeof value !== 'object') throw new TypeError('Invalid mock mode state.');
    const state = value as { mode?: unknown; live_since?: unknown; changed_by?: unknown };
    if (state.mode !== 'DEMO' && state.mode !== 'LIVE') throw new TypeError('Invalid mock mode.');
    return {
      mode: state.mode,
      live_since: typeof state.live_since === 'string' ? state.live_since : null,
      changed_by: typeof state.changed_by === 'string' ? state.changed_by : null,
    };
  } catch {
    storage()?.removeItem(MODE_KEY);
    return { mode: 'DEMO', live_since: null, changed_by: null };
  }
}

export function getMockMode(): Mode {
  return readState().mode;
}

export function getMockSystemMode(): SystemModeStatus {
  return { ...readState(), scan_interval_min: SCAN_INTERVAL_MIN, next_scan_at: null };
}

export function setMockMode(mode: Mode, username: string): SystemModeStatus {
  const state = mode === 'LIVE'
    ? { mode, live_since: new Date().toISOString(), changed_by: username }
    : { mode, live_since: null, changed_by: null };
  storage()?.setItem(MODE_KEY, JSON.stringify(state));
  const status = getMockSystemMode();
  return mode === 'LIVE' ? { ...status, scan_run_id: liveScanRun.run_id } : status;
}
