// Hook TanStack Query per endpoint EP-xx. Komponen hanya mengakses data lewat file ini.
import { QueryClient, useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api, ApiError, getCustomActionCatalog, getScenarios, postCustomDecision, postDecision, resendMeetingRoomEmail,
} from './client';
import type {
  Alert,
  AgentRun,
  AgentTrace,
  ActionResult,
  ApprovalItem,
  CaseDetail,
  CaseReport,
  CaseSummary,
  ChatRequest,
  ChatResponse,
  CustomDecisionRequest,
  CustomDecisionResult,
  Dashboard,
  Decision,
  DecisionRequest,
  DemoInjectRequest,
  DemoInjectResponse,
  DemoResetResponse,
  HeatmapCountries,
  ErpChangeDocument,
  Me,
  MeetingNotification,
  MeetingRoom,
  Network,
  Recommendations,
  RecommendationOptionCode,
  RejectActionRequest,
  RunQueuedResponse,
  RiskScanResponse,
  Schedule,
  Signal,
  SupplierProfile,
  SystemModeStatus,
  SystemStatus,
  SupplierMessages,
  SubmittedRecommendation,
  WhatIfResult,
} from './types';

/** Interval polling halaman (AGENTS.md sentra-web bagian 2.5). Berhenti otomatis saat tab tidak aktif. */
export const POLL_MS = 10_000;
export const RUN_POLL_MS = 3_000;

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Error 4xx (401, 404 FEATURE_DISABLED, ...) tidak diulang.
        retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      },
    },
  });
}

// EP-03
export function useMe() {
  return useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/me'), staleTime: 60_000 });
}

// EP-26
export function useSystemStatus() {
  return useQuery({
    queryKey: ['system', 'status'],
    queryFn: () => api.get<SystemStatus>('/system/status'),
    refetchInterval: POLL_MS,
  });
}

// EP-29
export function useSystemMode() {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ['system', 'mode'],
    queryFn: async () => {
      const status = await api.get<SystemModeStatus>('/system/mode');
      const previous = queryClient.getQueryData<SystemModeStatus>(['system', 'mode']);
      // EP-29 returns scan_run_id on the LIVE transition response, but not on GET.
      // Keep that run visible while the shared mode query continues polling.
      if (status.mode === 'LIVE' && !status.scan_run_id && previous?.mode === 'LIVE') {
        return { ...status, scan_run_id: previous.scan_run_id ?? null };
      }
      return status;
    },
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  });
}

export function useSetSystemMode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (mode: SystemModeStatus['mode']) => api.put<SystemModeStatus>('/system/mode', { mode }),
    onSuccess: async (modeStatus) => {
      queryClient.setQueryData(['system', 'mode'], modeStatus);
      queryClient.setQueryData<Me>(['me'], (current) =>
        current ? { ...current, mode: modeStatus.mode } : current,
      );
      queryClient.setQueryData<SystemStatus>(['system', 'status'], (current) =>
        current ? { ...current, mode: modeStatus.mode } : current,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['system', 'status'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      ]);
    },
  });
}

// EP-26 — Run checks is restricted to SCM_MANAGER by the API.
export const SYSTEM_CHECKS_TIMEOUT_MS = 180_000;

export function useRunSystemChecks() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SystemStatus>('/system/checks', undefined, { timeoutMs: SYSTEM_CHECKS_TIMEOUT_MS }),
    onSuccess: (status) => {
      queryClient.setQueryData(['system', 'status'], status);
    },
  });
}

// EP-28
export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get<Dashboard>('/dashboard'),
    refetchInterval: POLL_MS,
  });
}

// EP-04
export function useSchedule() {
  return useQuery({
    queryKey: ['schedule'],
    queryFn: () => api.get<Schedule>('/schedule'),
  });
}

// EP-07
export function useSignals() {
  return useQuery({
    queryKey: ['signals'],
    queryFn: () => api.get<Signal[]>('/signals?limit=50'),
  });
}

// EP-09 — keep checking only while the scan is queued or running.
export function useAgentRun(runId: string | null) {
  return useQuery({
    queryKey: ['runs', runId],
    queryFn: () => api.get<AgentRun>(`/runs/${encodeURIComponent(runId!)}`),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'QUEUED' || status === 'RUNNING' ? RUN_POLL_MS : false;
    },
    refetchIntervalInBackground: false,
  });
}

// 3 s while a step runs; otherwise keep checking every 10 s, because a run that has not started yet, the gap between two
// steps and a later `resume_case` all have no RUNNING step to wake the faster polling.
export function tracePollInterval(entries: AgentTrace[] | undefined): number {
  return entries?.some((entry) => entry.status === 'RUNNING') ? RUN_POLL_MS : POLL_MS;
}

// EP-13 — trace for one case or the latest global activity.
export function useAgentTrace(caseId?: string) {
  const path = caseId
    ? `/cases/${encodeURIComponent(caseId)}/trace`
    : '/trace?limit=100';

  return useQuery({
    queryKey: ['trace', caseId ?? 'global'],
    queryFn: () => api.get<AgentTrace[]>(path),
    refetchInterval: (query) => tracePollInterval(query.state.data),
    refetchIntervalInBackground: false,
  });
}

// EP-10
export function useAlerts() {
  return useQuery({
    queryKey: ['alerts'],
    queryFn: () => api.get<Alert[]>('/alerts'),
    refetchInterval: POLL_MS,
  });
}

// EP-10 — `read` is per user on the server; the feed and the bell count are updated at once, then read again.
export function useMarkAlertRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (alertId: string) => api.post<void>(`/alerts/${encodeURIComponent(alertId)}/read`),
    onMutate: (alertId) => {
      const wasUnread = queryClient.getQueryData<Alert[]>(['alerts'])?.some((alert) => alert.alert_id === alertId && !alert.read);
      if (!wasUnread) return;
      queryClient.setQueryData<Alert[]>(['alerts'], (current) => current?.map((alert) => (
        alert.alert_id === alertId ? { ...alert, read: true } : alert
      )));
      queryClient.setQueryData<Dashboard>(['dashboard'], (current) => current
        ? { ...current, unread_alerts: Math.max(0, current.unread_alerts - 1) }
        : current);
    },
    onSettled: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ['alerts'] }),
      queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
    ]),
  });
}

// EP-11 — `poll` keeps the list current on pages that wait for new cases (Supplier portal).
export function useCases(status: 'open' | 'all' = 'open', poll = false) {
  return useQuery({
    queryKey: ['cases', 'list', status],
    queryFn: () => api.get<CaseSummary[]>(`/cases?status=${status}`),
    refetchInterval: poll ? POLL_MS : false,
    refetchIntervalInBackground: false,
  });
}

// EP-14 — message history for each case shown in the simulated supplier portal. Polled, so scripted replies that arrive
// after SIM_REPLY_DELAY_S appear without leaving the page.
export function useSupplierMessages(caseIds: string[]) {
  return useQueries({
    queries: caseIds.map((caseId) => ({
      queryKey: ['cases', caseId, 'messages'],
      queryFn: () => api.get<SupplierMessages>(`/cases/${encodeURIComponent(caseId)}/messages`),
      refetchInterval: POLL_MS,
      refetchIntervalInBackground: false,
    })),
  });
}

// EP-15 — recommendations follow the case snapshot selected in the detail view.
export function useRecommendations(caseId: string, version?: number) {
  const queryClient = useQueryClient();
  const versionQuery = version === undefined ? '' : `?version=${encodeURIComponent(String(version))}`;
  return useQuery({
    queryKey: ['cases', caseId, 'recommendations', version ?? 'latest'],
    queryFn: async () => {
      const recommendations = await api.get<Recommendations>(`/cases/${encodeURIComponent(caseId)}/recommendations${versionQuery}`);
      const savedDecision = queryClient.getQueryData<Decision>(['cases', caseId, 'decision']);
      return savedDecision
        ? { ...recommendations, executive_decision_required: false, decision: savedDecision }
        : recommendations;
    },
  });
}

// EP-20 v2 — meeting room and escalation data for a case.
export function useMeetingRoom(caseId: string, enabled = true) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ['cases', caseId, 'meeting-room'],
    queryFn: async () => {
      const meetingRoom = await api.get<MeetingRoom>(`/cases/${encodeURIComponent(caseId)}/meeting-room`);
      const savedDecision = queryClient.getQueryData<Decision>(['cases', caseId, 'decision']);
      const resentNotification = queryClient.getQueryData<MeetingNotification>(['cases', caseId, 'meeting-notification']);
      return {
        ...meetingRoom,
        notification: resentNotification ?? meetingRoom.notification,
        escalation: savedDecision
          ? { ...meetingRoom.escalation, decision: savedDecision, decision_required: false }
          : meetingRoom.escalation,
      };
    },
    enabled,
    refetchInterval: POLL_MS,
  });
}

// EP-21 — submit a recommendation option for SCM Manager approval.
export function useSubmitRecommendation(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (option: RecommendationOptionCode) => api.post<SubmittedRecommendation>(
      `/cases/${encodeURIComponent(caseId)}/recommendations/${option}/submit`,
    ),
    onSuccess: () => {
      // The queue is read again from EP-22: a new submission also rejects the actions it replaces (server side), and the
      // queue may hold actions of other cases, so a list assembled in the browser would be incomplete or out of date.
      void queryClient.invalidateQueries({ queryKey: ['approvals'] });
      void queryClient.invalidateQueries({ queryKey: ['cases', caseId] });
    },
  });
}

export type ApprovalActionRequest =
  | { actionId: string; action: 'approve' }
  | { actionId: string; action: 'reject'; body: RejectActionRequest }
  | { actionId: string; action: 'retry' };

// EP-22 — approve, reject, or retry one proposed ERP action.
export function useApprovalAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ actionId, action, ...rest }: ApprovalActionRequest) => {
      const body = 'body' in rest ? rest.body : undefined;
      return api.post<ActionResult>(`/actions/${encodeURIComponent(actionId)}/${action}`, body);
    },
    onSuccess: (result, request) => {
      queryClient.setQueryData<ApprovalItem[]>(['approvals'], (current) => {
        if (!current) return current;
        return current.map((item) => item.action_id === request.actionId
          ? { ...item, status: result.status, erp_result: result.erp_result ?? null, error: result.error ?? null }
          : item);
      });
      queryClient.invalidateQueries({ queryKey: ['erp-log'] });
      queryClient.invalidateQueries({ queryKey: ['cases', result.idempotency_key.split(':')[0]] });
    },
  });
}

// EP-22 — approve every proposed action in a case option.
export function useApproveAllInOption() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ caseId, option }: { caseId: string; option: RecommendationOptionCode }) =>
      api.post<ActionResult[]>(`/cases/${encodeURIComponent(caseId)}/options/${option}/approve-all`),
    onSuccess: (results, { caseId, option }) => {
      const resultsById = new Map(results.map((result) => [result.action_id, result]));
      queryClient.setQueryData<ApprovalItem[]>(['approvals'], (current) => current?.map((item) => {
        if (item.case_id !== caseId || item.option !== option) return item;
        const result = resultsById.get(item.action_id);
        return result
          ? { ...item, status: result.status, erp_result: result.erp_result ?? null, error: result.error ?? null }
          : item;
      }));
      queryClient.invalidateQueries({ queryKey: ['erp-log'] });
      queryClient.invalidateQueries({ queryKey: ['cases', caseId] });
    },
  });
}

// EP-22 — pending and failed approvals for the SCM Manager queue.
export function useApprovals(enabled = true) {
  return useQuery({
    queryKey: ['approvals'],
    queryFn: () => api.get<ApprovalItem[]>('/approvals'),
    staleTime: POLL_MS,
    enabled,
  });
}

export const ACTION_RESULTS_KEY = ['action-results'] as const;

// C-51 (P5): hasil eksekusi ERP sesi ini (respons EP-22) di cache query, supaya tidak hilang saat pindah halaman.
// Tidak pernah di-fetch (tidak ada endpoint baru); diisi lewat `remember`, dikosongkan saat Reset demo data.
// Hilang saat browser dimuat ulang; aksi FAILED tetap datang dari GET /api/approvals (erp_result + error).
// Kunci sengaja bukan ['approvals', …] supaya invalidateQueries(['approvals']) tidak menyentuhnya.
export function useActionResults() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ACTION_RESULTS_KEY,
    queryFn: () => queryClient.getQueryData<ActionResult[]>(ACTION_RESULTS_KEY) ?? [],
    initialData: [] as ActionResult[],
    staleTime: Infinity,
    gcTime: Infinity,
  });
  function remember(results: ActionResult[]) {
    queryClient.setQueryData<ActionResult[]>(ACTION_RESULTS_KEY, (current = []) => {
      const ids = new Set(results.map((result) => result.action_id));
      return [...results, ...current.filter((result) => !ids.has(result.action_id))];
    });
  }
  return { results: query.data, remember };
}

// EP-23 — ERP changes created by SENTRA (always labelled SIMULATED in the UI).
export function useErpLog() {
  return useQuery({
    queryKey: ['erp-log'],
    queryFn: () => api.get<ErpChangeDocument[]>('/erp-log'),
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  });
}

// EP-30 — record the outcome of an executive meeting.
export function usePostDecision(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (decision: DecisionRequest) => postDecision(caseId, decision),
    onSuccess: (decision: Decision) => {
      queryClient.setQueryData(['cases', caseId, 'decision'], decision);
      queryClient.setQueryData<MeetingRoom>(['cases', caseId, 'meeting-room'], (current) => current
        ? { ...current, escalation: { ...current.escalation, decision, decision_required: false } }
        : current);
      queryClient.setQueriesData<Recommendations>(
        { queryKey: ['cases', caseId, 'recommendations'] },
        (current) => current ? { ...current, executive_decision_required: false, decision } : current,
      );
      queryClient.setQueryData<CaseDetail>(['cases', caseId, 'latest'], (current) => current?.escalation
        ? { ...current, escalation: { ...current.escalation, decision, decision_required: false } }
        : current);
    },
  });
}

// Option X — ERP objects a custom meeting action can change; read only while the custom form is open.
export function useCustomActionCatalog(caseId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['cases', caseId, 'custom-action-catalog'],
    queryFn: () => getCustomActionCatalog(caseId),
    enabled,
  });
}

// Option X — custom ERP actions of a Meeting Room (tier 1) or a meeting decision (tier 2); the server executes them at once.
export function usePostCustomDecision(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CustomDecisionRequest) => postCustomDecision(caseId, request),
    onSuccess: ({ decision }: CustomDecisionResult) => {
      if (decision) {
        queryClient.setQueryData(['cases', caseId, 'decision'], decision);
        queryClient.setQueryData<MeetingRoom>(['cases', caseId, 'meeting-room'], (current) => current
          ? { ...current, escalation: { ...current.escalation, decision, decision_required: false } }
          : current);
        queryClient.setQueriesData<Recommendations>(
          { queryKey: ['cases', caseId, 'recommendations'] },
          (current) => current ? { ...current, executive_decision_required: false, decision } : current,
        );
      } else {
        // Tier 1: no decision note; the case status and its options changed on the server.
        void queryClient.invalidateQueries({ queryKey: ['cases', caseId, 'meeting-room'] });
        void queryClient.invalidateQueries({ queryKey: ['cases', caseId, 'recommendations'] });
      }
      void queryClient.invalidateQueries({ queryKey: ['approvals'] });
      void queryClient.invalidateQueries({ queryKey: ['erp-log'] });
      void queryClient.invalidateQueries({ queryKey: ['cases', caseId, 'latest'] });
      void queryClient.invalidateQueries({ queryKey: ['cases', caseId, 'custom-action-catalog'] });
    },
  });
}

// EP-20: resend the latest notification and refresh the visible email log.
export function useResendMeetingRoomEmail(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => resendMeetingRoomEmail(caseId),
    onSuccess: (notification: MeetingNotification) => {
      queryClient.setQueryData(['cases', caseId, 'meeting-notification'], notification);
      queryClient.setQueryData<MeetingRoom>(['cases', caseId, 'meeting-room'], (current) => current
        ? { ...current, notification }
        : current);
    },
  });
}

// EP-31 — active seed scenarios, returned in seed order.
export function useScenarios() {
  return useQuery({
    queryKey: ['demo', 'scenarios'],
    queryFn: getScenarios,
    staleTime: 60_000,
  });
}

// EP-25 — locations and impact-only what-if simulations.
export function useWhatIfLocations() {
  return useQuery({
    queryKey: ['whatif', 'locations'],
    queryFn: () => api.get<string[]>('/whatif/locations'),
    staleTime: 60_000,
  });
}

export function useRunWhatIf() {
  return useMutation({
    mutationFn: (location: string) => api.post<WhatIfResult>('/whatif', { location }),
  });
}

// EP-12 — supplies verified source URLs for live alerts in the dashboard feed.
export function useCaseDetail(caseId: string, enabled = true, version?: number, poll = false) {
  const versionQuery = version === undefined ? '' : `?version=${encodeURIComponent(String(version))}`;
  return useQuery({
    queryKey: ['cases', caseId, version ?? 'latest'],
    queryFn: () => api.get<CaseDetail>(`/cases/${encodeURIComponent(caseId)}${versionQuery}`),
    enabled,
    refetchInterval: poll ? POLL_MS : false,
    refetchIntervalInBackground: false,
  });
}

// EP-17 — latest case report and report-generation run.
export function useCaseReport(caseId: string) {
  return useQuery({
    queryKey: ['cases', caseId, 'report'],
    queryFn: () => api.get<CaseReport>(`/cases/${encodeURIComponent(caseId)}/report`),
  });
}

export function useGenerateCaseReport(caseId: string) {
  return useMutation({
    mutationFn: () => api.post<RunQueuedResponse>(`/cases/${encodeURIComponent(caseId)}/report/generate`),
  });
}

// EP-18 — synchronous, the server waits up to 150 seconds for the agent.
export const CHAT_TIMEOUT_MS = 160_000;

export function useSendChat() {
  return useMutation({
    mutationFn: (request: ChatRequest) => api.post<ChatResponse>('/chat', request, { timeoutMs: CHAT_TIMEOUT_MS }),
  });
}

// EP-05
export function useNetwork(caseId?: string, version?: number) {
  const params = new URLSearchParams();
  if (caseId) params.set('case_id', caseId);
  if (version !== undefined) params.set('version', String(version));
  const query = params.toString();

  return useQuery({
    queryKey: ['network', caseId ?? 'all', version ?? 'latest'],
    queryFn: () => api.get<Network>(`/network${query ? `?${query}` : ''}`),
  });
}

// EP-06
export function useSupplierProfile(siteId: string | null) {
  return useQuery({
    queryKey: ['suppliers', siteId],
    queryFn: () => api.get<SupplierProfile>(`/suppliers/${encodeURIComponent(siteId!)}`),
    enabled: Boolean(siteId),
  });
}

// EP-16
export function useHeatmapCountries() {
  return useQuery({
    queryKey: ['heatmap', 'countries'],
    queryFn: () => api.get<HeatmapCountries>('/heatmap/countries'),
    refetchInterval: POLL_MS,
  });
}

async function refreshDashboardData(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
    queryClient.invalidateQueries({ queryKey: ['alerts'] }),
    queryClient.invalidateQueries({ queryKey: ['heatmap', 'countries'] }),
    queryClient.invalidateQueries({ queryKey: ['system', 'status'] }),
  ]);
}

// EP-24
export function useResetDemo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<DemoResetResponse>('/demo/reset'),
    onSuccess: () => {
      // C-51: reset menghapus tabel action (DATA_MODEL bagian 2); hasil eksekusi sesi ini dan antrean ikut dikosongkan.
      queryClient.setQueryData(ACTION_RESULTS_KEY, []);
      // Nomor kasus mulai lagi dari CASE-0001 setelah reset. Keputusan rapat dan email yang hanya disimpan di cache
      // (tidak pernah di-fetch) dihapus dulu, supaya tidak menempel ke kasus baru bernomor sama; lalu data kasus dibaca ulang.
      queryClient.removeQueries({ predicate: (query) => isLocalCaseState(query.queryKey) });
      void queryClient.invalidateQueries({ queryKey: ['cases'] });
      void queryClient.invalidateQueries({ queryKey: ['signals'] });
      void queryClient.invalidateQueries({ queryKey: ['trace'] });
      void queryClient.invalidateQueries({ queryKey: ['approvals'] });
      void queryClient.invalidateQueries({ queryKey: ['erp-log'] });
      return refreshDashboardData(queryClient);
    },
  });
}

/** Cache keys written only by the browser (EP-30 and EP-20 responses), never fetched from the API. */
function isLocalCaseState(queryKey: readonly unknown[]): boolean {
  return queryKey[0] === 'cases' && (queryKey[2] === 'decision' || queryKey[2] === 'meeting-notification');
}

// EP-19 — the SCM Manager closes a case (comment optional) or dismisses it (comment required).
export function useCaseDisposition(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ action, comment }: { action: 'close' | 'dismiss'; comment: string }) => api.post<CaseSummary>(
      `/cases/${encodeURIComponent(caseId)}/${action}`,
      comment ? { comment } : {},
    ),
    onSuccess: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ['cases'] }),
      refreshDashboardData(queryClient),
    ]),
  });
}

export function useInjectDemo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: DemoInjectRequest) => api.post<DemoInjectResponse>('/demo/inject', request),
    onSuccess: () => refreshDashboardData(queryClient),
  });
}

// EP-08
export function useRunLiveScan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<RiskScanResponse>('/risk/scan'),
    onSuccess: () => refreshDashboardData(queryClient),
  });
}
