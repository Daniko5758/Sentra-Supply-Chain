import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  useActionResults,
  useApprovalAction,
  useApproveAllInOption,
  useApprovals,
  useCaseDetail,
  type ApprovalActionRequest,
} from '../../api/hooks';
import type { ActionResult, ApprovalItem, CaseDetail, ErpMessage, RecommendationOptionCode } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { ErpShell } from '../../components/ErpShell';
import { ErpStatus, erpStatusTone } from '../../components/ErpStatus';
import { actionChangeRows, actionObjectLabel, erpMessageText } from '../../lib/erpLabels';
import { formatDateTime, formatScore } from '../../lib/format';
import { RejectDialog } from './RejectDialog';
import './approvals.css';

interface ApprovalGroupData {
  key: string;
  case_id: string;
  case_title: string;
  option: RecommendationOptionCode;
  items: ApprovalItem[];
}

function groupApprovals(items: ApprovalItem[]): ApprovalGroupData[] {
  const groups = new Map<string, ApprovalGroupData>();
  for (const item of items) {
    const key = `${item.case_id}:${item.option}`;
    const group = groups.get(key) ?? {
      key,
      case_id: item.case_id,
      case_title: item.case_title,
      option: item.option,
      items: [],
    };
    group.items.push(item);
    groups.set(key, group);
  }
  return Array.from(groups.values());
}

function actionLabel(type: ApprovalItem['type']): string {
  switch (type) {
    case 'CHANGE_PO_DATE': return 'Change purchase order date';
    case 'CREATE_PR_PO': return 'Create purchase requisition';
    case 'REDUCE_OR_CANCEL_PO': return 'Reduce purchase order quantity';
    case 'RESCHEDULE_PRODUCTION_ORDER': return 'Reschedule production order';
    case 'RESCHEDULE_SALES_ORDER': return 'Reschedule sales order';
    case 'EXPEDITE_SHIPMENT': return 'Expedite shipment';
  }
}

// C-51 (P3): satu panggilan ERP pada satu waktu di halaman ini, sampai respons server kembali (berhasil atau gagal).
type ApprovalBusy = { kind: 'action'; actionId: string } | { kind: 'option'; groupKey: string } | null;
type RunExclusive = (next: NonNullable<ApprovalBusy>, task: () => Promise<void>) => Promise<void>;
type RememberResults = (results: ActionResult[]) => void;
type ExecutionResult = Pick<ActionResult, 'action_id' | 'status' | 'erp_result' | 'error'>;

function isActionBusy(busy: ApprovalBusy, actionId: string): boolean {
  return busy?.kind === 'action' && busy.actionId === actionId;
}

// C-51 (P2): isi payload aksi sebagai label–nilai.
function ActionChanges({ item }: { item: ApprovalItem }) {
  const rows = actionChangeRows(item.type, item.payload);
  if (rows.length === 0) return <span className="approval-table__secondary">—</span>;
  return (
    <dl className="approval-changes">
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ApprovalGroup({
  group,
  busy,
  runExclusive,
  remember,
  onReject,
}: {
  group: ApprovalGroupData;
  busy: ApprovalBusy;
  runExclusive: RunExclusive;
  remember: RememberResults;
  onReject: (item: ApprovalItem, trigger: HTMLElement) => void;
}) {
  const approvalAction = useApprovalAction();
  const approveAllMutation = useApproveAllInOption();
  const [error, setError] = useState('');
  const [pollFromVersion, setPollFromVersion] = useState<number | null>(null);
  const [executionCase, setExecutionCase] = useState<CaseDetail | undefined>(undefined);
  const caseDetail = useCaseDetail(group.case_id, true, undefined, pollFromVersion !== null);
  const hasProposed = group.items.some((item) => item.status === 'PROPOSED');
  const locked = busy !== null;
  const optionBusy = busy?.kind === 'option' && busy.groupKey === group.key;

  useEffect(() => {
    if (pollFromVersion === null || !caseDetail.data) return;
    const executionVersion = caseDetail.data.versions.find(
      (version) => version.version > pollFromVersion && version.trigger === 'erp_execution',
    );
    if (executionVersion) {
      setExecutionCase(caseDetail.data);
      setPollFromVersion(null);
    }
  }, [caseDetail.data, pollFromVersion]);

  function runAction(request: ApprovalActionRequest) {
    void runExclusive({ kind: 'action', actionId: request.actionId }, async () => {
      setError('');
      try {
        const result = await approvalAction.mutateAsync(request);
        remember([result]);
      } catch (actionError) {
        setError(actionError instanceof Error ? actionError.message : 'The ERP action could not be updated.');
      }
    });
  }

  function handleApproveAll() {
    void runExclusive({ kind: 'option', groupKey: group.key }, async () => {
      setError('');
      try {
        let beforeVersion = caseDetail.data?.version ?? null;
        if (beforeVersion === null) {
          const refreshed = await caseDetail.refetch();
          beforeVersion = refreshed.data?.version ?? null;
        }
        const results = await approveAllMutation.mutateAsync({ caseId: group.case_id, option: group.option });
        remember(results);
        if (beforeVersion !== null && results.some((result) => result.status === 'EXECUTED')) {
          setExecutionCase(undefined);
          setPollFromVersion(beforeVersion);
        }
      } catch (actionError) {
        setError(actionError instanceof Error ? actionError.message : 'The option could not be approved.');
      }
    });
  }

  return (
    <section className="approval-group card" aria-labelledby={`approval-group-${group.key}`}>
      <header className="approval-group__header">
        <div>
          <h2 id={`approval-group-${group.key}`}>
            <Link to={`/cases/${encodeURIComponent(group.case_id)}`}>{group.case_title}</Link>
          </h2>
          <p><span className="mono">{group.case_id}</span> · Option {group.option} · {group.items.length} {group.items.length === 1 ? 'action' : 'actions'}</p>
        </div>
        <Button
          variant="primary"
          onClick={handleApproveAll}
          loading={optionBusy}
          disabled={!hasProposed || caseDetail.isPending || locked}
        >
          Approve all in option
        </Button>
      </header>

      {error && <p className="approval-message approval-message--error" role="alert">{error}</p>}

      <div className="approval-table-scroll" role="region" aria-label={`${group.case_title} option ${group.option} ERP actions`} tabIndex={0}>
        <table className="approval-table">
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Object</th>
              <th scope="col">Changes</th>
              <th scope="col">Status</th>
              <th scope="col">Proposed by</th>
              <th scope="col">Created</th>
              <th scope="col">Decision</th>
            </tr>
          </thead>
          <tbody>
            {group.items.map((item) => {
              const object = actionObjectLabel(item.type, item.object);
              const rowBusy = isActionBusy(busy, item.action_id);
              return (
                <tr key={item.action_id}>
                  <td>
                    <span className="approval-table__primary">{item.seq}. {actionLabel(item.type)}</span>
                    <span className="approval-table__secondary mono">{item.action_id}</span>
                  </td>
                  <td>
                    <span className="approval-table__primary">{object.title}</span>
                    {object.detail && <span className="approval-table__secondary mono">{object.detail}</span>}
                  </td>
                  <td><ActionChanges item={item} /></td>
                  <td><ErpStatus status={item.status} /></td>
                  <td>{item.proposed_by}</td>
                  <td><time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time></td>
                  <td>
                    {item.status === 'PROPOSED' ? (
                      <div className="approval-row-actions">
                        <Button
                          variant="primary"
                          loading={rowBusy || optionBusy}
                          disabled={locked}
                          onClick={() => runAction({ actionId: item.action_id, action: 'approve' })}
                        >Approve</Button>
                        <Button
                          variant="danger"
                          disabled={locked}
                          onClick={(event) => onReject(item, event.currentTarget)}
                        >Reject…</Button>
                      </div>
                    ) : item.status === 'FAILED' ? (
                      <div className="approval-row-actions">
                        <Button
                          variant="secondary"
                          loading={rowBusy}
                          disabled={locked}
                          onClick={() => runAction({ actionId: item.action_id, action: 'retry' })}
                        >Retry</Button>
                        {item.error && <span className="approval-table__error">{item.error}</span>}
                      </div>
                    ) : (
                      <span className="approval-table__secondary">No action available</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {pollFromVersion !== null && (
        <p className="approval-message" role="status">ERP actions executed. Waiting for the case to recalculate…</p>
      )}
      {executionCase && (
        <div className="approval-execution-summary" role="status">
          <strong>Case recalculated after ERP execution</strong>
          <Badge variant={executionCase.kpi.level}>{executionCase.kpi.level}</Badge>
          <span>Score {formatScore(executionCase.kpi.priority_score)}</span>
          <span>{executionCase.kpi.units_at_risk.toLocaleString('en-US')} units at risk</span>
          <span>Version {executionCase.version}</span>
        </div>
      )}
    </section>
  );
}

// The icon is drawn by CSS (.erp-message--{type}::before); the label is always in the text.
function ErpMessages({ messages }: { messages: ErpMessage[] }) {
  return (
    <div className="erp-messages">
      <h3>ERP messages</h3>
      <ul className="erp-messages__list">
        {messages.map((message, index) => (
          <li key={`${message.id}-${message.number}-${index}`} className={`erp-message erp-message--${message.type}`}>
            {erpMessageText(message)}
          </li>
        ))}
      </ul>
    </div>
  );
}

// C-51 (P5): hasil sesi ini (respons EP-22 di cache) + aksi FAILED dari GET /api/approvals yang membawa erp_result dan error
// (tetap ada setelah reload). Aksi EXECUTED tidak dikirim EP-22 (executor.py WAITING), jadi setelah reload hanya ada di ERP log.
function executionResults(session: ActionResult[], queue: ApprovalItem[]): ExecutionResult[] {
  const known = new Set(session.map((result) => result.action_id));
  const failed = queue
    .filter((item) => item.status === 'FAILED' && !known.has(item.action_id))
    .map((item): ExecutionResult => ({
      action_id: item.action_id,
      status: item.status,
      erp_result: item.erp_result ?? null,
      error: item.error ?? null,
    }));
  return [...session, ...failed];
}

// C-51 (P8): satu strip per aksi; teks document, pesan, dan error dari API apa adanya.
function ActionResults({
  results,
  busy,
  error,
  onRetry,
}: {
  results: ExecutionResult[];
  busy: ApprovalBusy;
  error: string;
  onRetry: (actionId: string) => void;
}) {
  if (results.length === 0) return null;
  return (
    <section className="approval-results card" aria-labelledby="approval-results-title" aria-live="polite">
      <header className="approval-results__header">
        <h2 id="approval-results-title">ERP execution results</h2>
        <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>
      </header>
      {error && <p className="approval-message approval-message--error" role="alert">{error}</p>}
      <ul className="approval-results__list">
        {results.map((result) => {
          const messages = result.erp_result?.messages ?? [];
          return (
            <li key={result.action_id} className={`approval-result approval-result--${erpStatusTone(result.status)}`}>
              <div className="approval-results__summary">
                <span className="mono">{result.action_id}</span>
                <ErpStatus status={result.status} />
                {result.erp_result?.document && (
                  <strong className="approval-results__document">{result.erp_result.document}</strong>
                )}
              </div>
              {messages.length > 0 && <ErpMessages messages={messages} />}
              {result.error && <span className="approval-table__error">{result.error}</span>}
              {result.status === 'FAILED' && (
                <Button
                  variant="secondary"
                  aria-label={`Retry ${result.action_id}`}
                  loading={isActionBusy(busy, result.action_id)}
                  disabled={busy !== null}
                  onClick={() => onRetry(result.action_id)}
                >Retry</Button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ApprovalsError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="approval-state approval-state--error" role="alert">
      <p>Approvals could not be loaded.</p>
      <Button variant="secondary" onClick={onRetry}>Try again</Button>
    </div>
  );
}

export function ApprovalsPage() {
  const approvals = useApprovals();
  const approvalAction = useApprovalAction();
  const { results, remember } = useActionResults();
  const [busy, setBusy] = useState<ApprovalBusy>(null);
  const lock = useRef(false);
  const [rejecting, setRejecting] = useState<{ item: ApprovalItem; trigger: HTMLElement } | null>(null);
  const [resultsError, setResultsError] = useState('');

  // Klik ganda, atau klik kedua sebelum render berikutnya, diabaikan: kunci dipasang sinkron, dilepas setelah respons.
  const runExclusive: RunExclusive = async (next, task) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(next);
    try {
      await task();
    } finally {
      lock.current = false;
      setBusy(null);
    }
  };

  async function rejectAction(item: ApprovalItem, comment: string): Promise<void> {
    let failure: unknown;
    await runExclusive({ kind: 'action', actionId: item.action_id }, async () => {
      try {
        remember([await approvalAction.mutateAsync({ actionId: item.action_id, action: 'reject', body: { comment } })]);
        setRejecting(null);
      } catch (rejectError) {
        failure = rejectError;
      }
    });
    if (failure !== undefined) throw failure;
  }

  function retryFromResults(actionId: string) {
    void runExclusive({ kind: 'action', actionId }, async () => {
      setResultsError('');
      try {
        remember([await approvalAction.mutateAsync({ actionId, action: 'retry' })]);
      } catch (retryError) {
        setResultsError(retryError instanceof Error ? retryError.message : 'The ERP action could not be updated.');
      }
    });
  }

  const waiting = (approvals.data ?? [])
    .filter((item) => item.status === 'PROPOSED' || item.status === 'APPROVED' || item.status === 'FAILED').length;

  return (
    <section className="approvals-page">
      <div className="page-header"><h1>Approvals</h1></div>
      <p className="page-description">Review proposed ERP actions, record a decision, and track execution results.</p>

      {approvals.isPending ? (
        <p className="approval-state" role="status">Loading approvals…</p>
      ) : approvals.isError || !approvals.data ? (
        <ApprovalsError onRetry={() => void approvals.refetch()} />
      ) : (
        <ErpShell
          title="Approve ERP actions"
          toolbar={<span>{waiting} {waiting === 1 ? 'action' : 'actions'} waiting</span>}
        >
          {approvals.data.length === 0 ? (
            <section className="approval-empty card" role="status">
              <h2>No actions waiting for approval</h2>
              <p>Submit an available recommendation from its case page to add actions to this queue.</p>
            </section>
          ) : groupApprovals(approvals.data).map((group) => (
            <ApprovalGroup
              key={group.key}
              group={group}
              busy={busy}
              runExclusive={runExclusive}
              remember={remember}
              onReject={(item, trigger) => setRejecting({ item, trigger })}
            />
          ))}
          <ActionResults
            results={executionResults(results, approvals.data)}
            busy={busy}
            error={resultsError}
            onRetry={retryFromResults}
          />
        </ErpShell>
      )}

      {rejecting && (
        <RejectDialog
          actionLabel={`${rejecting.item.seq}. ${actionLabel(rejecting.item.type)} · ${actionObjectLabel(rejecting.item.type, rejecting.item.object).title}`}
          returnFocusTo={rejecting.trigger}
          busy={isActionBusy(busy, rejecting.item.action_id)}
          onCancel={() => setRejecting(null)}
          onConfirm={(comment) => rejectAction(rejecting.item, comment)}
        />
      )}
    </section>
  );
}
