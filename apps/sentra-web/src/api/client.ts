// Satu-satunya tempat fetch. Saklar mock:
// - `npm run dev:mock` (vite --mode mock) atau VITE_USE_MOCKS=true → baca src/mocks/*.json
// - selain itu → fetch ke {VITE_API_BASE}/api dengan cookie sesi.
import { markMockAlertRead, mockAlerts, mockDashboard, resetMockAlertReads } from '../mocks/alertReads';
import { mockApprovalQueue, mockApproveAll, mockDecision, resetMockApprovals } from '../mocks/approvalQueue';
import { mockCustomDecision } from '../mocks/customDecision';
import { clearMockSession, createMockSession, getMockSession } from '../mocks/session';
import { getMockMode, getMockSystemMode, setMockMode } from '../mocks/mode';
import { progressMockRun, startMockRun } from '../mocks/runs';
import type {
  AgentRun,
  Alert,
  ApiErrorBody,
  ApprovalItem,
  CustomActionCatalog,
  CustomDecisionRequest,
  CustomDecisionResult,
  Dashboard,
  Decision,
  DecisionRequest,
  FeatureFlags,
  MeetingNotification,
  Mode,
  ScenarioList,
  SystemModeStatus,
  SystemStatus,
} from './types';

export const USE_MOCKS =
  import.meta.env.MODE === 'mock' || import.meta.env.VITE_USE_MOCKS === 'true';

const API_BASE = `${import.meta.env.VITE_API_BASE ?? ''}/api`;

let unauthorizedHandler: (() => void) | undefined;

/** Lets the authentication provider react when any API request loses its session. */
export function setUnauthorizedHandler(handler: (() => void) | undefined): () => void {
  unauthorizedHandler = handler;
  return () => {
    if (unauthorizedHandler === handler) {
      unauthorizedHandler = undefined;
    }
  };
}

function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | undefined;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const mockFiles = import.meta.glob<{ default: unknown }>('../mocks/*.json', { eager: true });

/** Path API → nama file mock; version snapshots and demo injections select fixture variants. */
export function mockKey(path: string, body?: unknown): string {
  const [pathname, query = ''] = path.split('?');
  const normalized = pathname.replace(/^\/+/, '').replace(/\/+$/, '').replace(/\//g, '_');
  const queryParams = new URLSearchParams(query);

  if (normalized === 'demo_inject') {
    const scenario = (body as { scenario?: string } | undefined)?.scenario;
    if (scenario === 'custom') return 'demo_inject_custom';
    return 'demo_inject_scenario';
  }

  if (normalized === 'network') {
    const caseId = queryParams.get('case_id');
    if (!caseId) return 'network_all';
    const caseNetworkKey = caseId ? `${normalized}_${caseId}` : null;
    if (caseNetworkKey && mockFiles[`../mocks/${caseNetworkKey}.json`]) {
      return caseNetworkKey;
    }
  }

  if (normalized === 'whatif') {
    const location = (body as { location?: unknown } | undefined)?.location;
    if (typeof location === 'string' && location.trim()) {
      const fixtureLocation = location.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
      return `whatif_${fixtureLocation}`;
    }
  }

  const version = queryParams.get('version');
  return version === '2' ? `${normalized}_v2` : normalized;
}

function requiredMockFeature(path: string): keyof FeatureFlags | null {
  const pathname = path.split('?')[0];
  if (/\/meeting-room(?:\/resend)?$/.test(pathname)) return 'meeting_room';
  if (
    /\/decision\/custom-actions(?:\/catalog)?$/.test(pathname) ||
    pathname === '/approvals' ||
    pathname === '/erp-log' ||
    /\/recommendations\/[^/]+\/submit$/.test(pathname) ||
    /\/actions\/[^/]+\/(?:approve|reject|retry)$/.test(pathname) ||
    /\/options\/[^/]+\/approve-all$/.test(pathname)
  ) {
    return 'erp_execution';
  }
  if (/^\/whatif(?:\/locations)?$/.test(pathname)) return 'whatif';
  return null;
}

function unauthenticatedError(): ApiError {
  notifyUnauthorized();
  return new ApiError(401, 'UNAUTHENTICATED', 'Please sign in to continue.');
}

type HttpMethod = 'GET' | 'POST' | 'PUT';
interface RequestOptions {
  timeoutMs?: number;
}

async function mockRequest<T>(method: HttpMethod, path: string, body?: unknown): Promise<T> {
  const pathname = path.split('?')[0];

  if (pathname === '/auth/login') {
    const profile = createMockSession(body);
    if (!profile) {
      throw unauthenticatedError();
    }
    return { username: profile.username, role: profile.role } as T;
  }

  if (pathname === '/auth/logout') {
    clearMockSession();
    return undefined as T;
  }

  const profile = getMockSession();
  if (pathname !== '/health' && !profile) {
    throw unauthenticatedError();
  }

  if (pathname === '/me') {
    return structuredClone(profile) as T;
  }

  if (pathname === '/system/mode') {
    if (method === 'GET') return getMockSystemMode() as T;
    if (method === 'PUT') {
      if (profile?.role !== 'SCM_MANAGER') {
        throw new ApiError(403, 'FORBIDDEN', 'Only the SCM Manager can change application mode.');
      }
      const requestedMode = (body as { mode?: unknown } | undefined)?.mode;
      if (requestedMode !== 'LIVE' && requestedMode !== 'DEMO') {
        throw new ApiError(400, 'VALIDATION_FAILED', 'Mode must be LIVE or DEMO.');
      }
      const currentMode = getMockMode();
      const result: SystemModeStatus = requestedMode === currentMode
        ? getMockSystemMode()
        : setMockMode(requestedMode as Mode, profile.username);
      return {
        ...result,
      } as T;
    }
  }

  if (pathname === '/system/checks' && method === 'POST' && profile?.role !== 'SCM_MANAGER') {
    throw new ApiError(403, 'FORBIDDEN', 'Only the SCM Manager can run system checks.');
  }

  const feature = requiredMockFeature(path);
  if (feature) {
    if (!profile?.feature_flags[feature]) {
      throw new ApiError(404, 'FEATURE_DISABLED', 'This feature is disabled.');
    }
  }

  // C-51: antrean Approvals mock mengikuti keputusan sesi ini (mocks/approvalQueue.ts); EP-10 read juga (mocks/alertReads.ts).
  if (method === 'POST' && pathname === '/demo/reset') {
    resetMockApprovals();
    resetMockAlertReads();
  }
  const alertReadPath = /^\/alerts\/([^/]+)\/read$/.exec(pathname);
  if (method === 'POST' && alertReadPath) {
    markMockAlertRead(decodeURIComponent(alertReadPath[1]));
    return undefined as T; // 204
  }
  const decisionPath = /^\/actions\/([^/]+)\/(approve|reject|retry)$/.exec(pathname);
  if (method === 'POST' && decisionPath) {
    const decided = mockDecision(decodeURIComponent(decisionPath[1]), decisionPath[2] as 'approve' | 'reject' | 'retry');
    if (decided) return decided as T;
  }
  const approveAllPath = /^\/cases\/([^/]+)\/options\/([^/]+)\/approve-all$/.exec(pathname);
  if (method === 'POST' && approveAllPath) {
    const approved = mockApproveAll(decodeURIComponent(approveAllPath[1]), approveAllPath[2]);
    if (!approved) throw new ApiError(404, 'NOT_FOUND', `No mock data for ${path}`);
    return approved as T;
  }

  // Option X: the result follows the actions of the form (mocks/customDecision.ts).
  if (method === 'POST' && /^\/cases\/[^/]+\/decision\/custom-actions$/.test(pathname)) {
    const fixture = mockFiles[`../mocks/${mockKey(path)}.json`];
    if (!fixture) throw new ApiError(404, 'NOT_FOUND', `No mock data for ${path}`);
    const catalog = mockFiles[`../mocks/${mockKey(path)}_catalog.json`];
    return mockCustomDecision(
      body as CustomDecisionRequest,
      structuredClone(fixture.default) as CustomDecisionResult,
      catalog ? structuredClone(catalog.default) as CustomActionCatalog : undefined,
    ) as T;
  }

  const key = pathname === '/demo/scenarios' && profile?.seed.dataset === 'TEST'
    ? 'demo_scenarios_test'
    : mockKey(path, body);
  const isLocationSpecificWhatIf = method === 'POST' && pathname === '/whatif'
    && typeof (body as { location?: unknown } | undefined)?.location === 'string';
  const mod = mockFiles[`../mocks/${key}.json`]
    ?? (isLocationSpecificWhatIf ? undefined : mockFiles[`../mocks/${mockKey(path)}.json`]);
  if (!mod) {
    throw new ApiError(404, 'NOT_FOUND', `No mock data for ${path}`);
  }
  const value = structuredClone(mod.default);
  if ((pathname === '/system/status' || pathname === '/system/checks') && value && typeof value === 'object') {
    const status = value as SystemStatus;
    status.mode = getMockMode();
    if (profile) {
      status.seed = structuredClone(profile.seed);
      status.feature_flags = structuredClone(profile.feature_flags);

      const optionalIntegrations = ['zoom', 'gmail'] as const;
      const currentNames = new Set(status.integrations.map((integration) => integration.name));
      if (profile.feature_flags.meeting_room) {
        for (const name of optionalIntegrations) {
          if (!currentNames.has(name)) {
            status.integrations.push({ name, status: 'OK', last_call_at: null, duration_ms: null, message: null });
          }
        }
      } else {
        status.integrations = status.integrations.filter((integration) => integration.name !== 'zoom' && integration.name !== 'gmail');
      }
    }
  }
  // C-07: run yang dimulai Scan now tampil berjalan dulu (QUEUED → RUNNING) sebelum selesai.
  if (method === 'POST' && pathname === '/risk/scan') startMockRun((value as { run_id: string }).run_id);
  if (method === 'GET' && pathname.startsWith('/runs/')) return progressMockRun(value as AgentRun) as T;
  if (method === 'GET' && pathname === '/approvals') return mockApprovalQueue(value as ApprovalItem[]) as T;
  if (method === 'GET' && pathname === '/alerts') return mockAlerts(value as Alert[]) as T;
  if (method === 'GET' && pathname === '/dashboard') return mockDashboard(value as Dashboard) as T;
  return (value === null ? undefined : value) as T;
}

async function httpRequest<T>(method: HttpMethod, path: string, body?: unknown, options?: RequestOptions): Promise<T> {
  const controller = options?.timeoutMs ? new AbortController() : undefined;
  const timeoutHandle = options?.timeoutMs
    ? setTimeout(() => controller?.abort(), options.timeoutMs)
    : undefined;

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      credentials: 'include',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller?.signal,
    });

    if (res.status === 204) {
      return undefined as T;
    }
    if (!res.ok) {
      if (res.status === 401) {
        notifyUnauthorized();
      }
      let code = 'INTERNAL_ERROR';
      let message = `Request failed (${res.status})`;
      let details: Record<string, unknown> | undefined;
      try {
        const err = (await res.json()) as ApiErrorBody;
        code = err.error?.code ?? code;
        message = err.error?.message ?? message;
        details = err.error?.details;
      } catch {
        // badan error bukan JSON; pakai pesan bawaan
      }
      throw new ApiError(res.status, code, message, details);
    }
    return (await res.json()) as T;
  } catch (error) {
    if (controller?.signal.aborted) {
      throw new Error(`Request timed out after ${Math.ceil((options?.timeoutMs ?? 0) / 1000)} seconds.`);
    }
    throw error;
  } finally {
    if (timeoutHandle !== undefined) clearTimeout(timeoutHandle);
  }
}

export const api = {
  get<T>(path: string): Promise<T> {
    return USE_MOCKS ? mockRequest<T>('GET', path) : httpRequest<T>('GET', path);
  },
  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return USE_MOCKS ? mockRequest<T>('POST', path, body) : httpRequest<T>('POST', path, body, options);
  },
  put<T>(path: string, body?: unknown): Promise<T> {
    return USE_MOCKS ? mockRequest<T>('PUT', path, body) : httpRequest<T>('PUT', path, body);
  },
};

/** EP-30: records the SCM Manager's meeting decision for the selected case. */
export function postDecision(caseId: string, decision: DecisionRequest): Promise<Decision> {
  return api.post<Decision>(`/cases/${encodeURIComponent(caseId)}/decision`, decision);
}

/** Option X: ERP objects (open PO lines, production and sales orders, approved alternate sources) a custom action can change. */
export function getCustomActionCatalog(caseId: string): Promise<CustomActionCatalog> {
  return api.get<CustomActionCatalog>(`/cases/${encodeURIComponent(caseId)}/decision/custom-actions/catalog`);
}

/** Option X: records the meeting decision with custom ERP actions; the server checks and executes them at once. */
export function postCustomDecision(caseId: string, decision: CustomDecisionRequest): Promise<CustomDecisionResult> {
  return api.post<CustomDecisionResult>(`/cases/${encodeURIComponent(caseId)}/decision/custom-actions`, decision);
}

/** EP-20: retries the latest meeting email without creating another meeting. */
export function resendMeetingRoomEmail(caseId: string): Promise<MeetingNotification> {
  return api.post<MeetingNotification>(`/cases/${encodeURIComponent(caseId)}/meeting-room/resend`);
}

/** EP-31: loads the active seed's scenario list in seed order. */
export function getScenarios(): Promise<ScenarioList> {
  return api.get<ScenarioList>('/demo/scenarios');
}
