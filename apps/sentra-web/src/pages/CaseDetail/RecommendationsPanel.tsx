import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { useApprovals, useRecommendations, useSubmitRecommendation } from '../../api/hooks';
import type {
  ApprovalItem, CaseDetail, CaseStatus, Metrics, RecommendationAction, RecommendationOption, Recommendations,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { requestTypeLabel } from '../../lib/c42';
import { formatDate, formatDemoOffset, formatIDR } from '../../lib/format';
import { decisionOptionLabel } from '../../lib/labels';
import './recommendations.css';

const optionTitles: Record<RecommendationOption['option'], string> = {
  A: 'Option A — Protect production',
  B: 'Option B — Reschedule',
  C: 'Option C — Activate supplier backup site',
  D: 'Option D — Expedite shipment',
  E: 'Option E — Pull in before effective date',
  R: 'Option R — Update ERP dates only',
  S: 'Option S — Switch source outside tariff country',
};

function actionTypeLabel(type: RecommendationAction['type']): string {
  switch (type) {
    case 'CHANGE_PO_DATE': return 'Change purchase order date';
    case 'CREATE_PR_PO': return 'Create purchase requisition';
    case 'REDUCE_OR_CANCEL_PO': return 'Reduce purchase order quantity';
    case 'RESCHEDULE_PRODUCTION_ORDER': return 'Reschedule production order';
    case 'RESCHEDULE_SALES_ORDER': return 'Reschedule sales order';
    case 'EXPEDITE_SHIPMENT': return 'Expedite shipment';
  }
}

function actionDate(value: string, data: CaseDetail): string {
  const date = formatDate(value);
  return data.mode === 'DEMO' ? `${date} (${formatDemoOffset(value, data.d0_date)})` : date;
}

function actionChange(action: RecommendationAction, data: CaseDetail): string {
  const { payload } = action;
  if ('old_shipping_condition' in payload) {
    return `Expedite shipment ${payload.po_id}/${payload.item_no}: ${payload.old_shipping_condition} → AIR, ${payload.old_incoterms} → FCA (${formatIDR(payload.freight_cost)})`;
  }
  if ('old_date' in payload) {
    return `${actionDate(payload.old_date, data)} → ${actionDate(payload.new_date, data)}`;
  }
  if ('old_qty' in payload) {
    return `${payload.old_qty.toLocaleString('en-US')} → ${payload.new_qty.toLocaleString('en-US')} units`;
  }
  if ('qty' in payload) {
    const shipping = 'shipping_condition' in payload ? ` · ${payload.shipping_condition}` : '';
    const freight = 'freight_per_unit' in payload && payload.freight_per_unit > 0
      ? ` · air freight ${formatIDR(payload.freight_per_unit)} per unit`
      : '';
    return `${payload.qty.toLocaleString('en-US')} units · ${formatIDR(payload.price)} per unit${shipping}${freight} · delivery ${actionDate(payload.delivery_date, data)}`;
  }
  return '—';
}

function actionText(action: RecommendationAction, data: CaseDetail): string {
  return `${action.seq}. ${actionTypeLabel(action.type)} — ${action.object}: ${actionChange(action, data)}`;
}

function metricValue(metric: keyof Metrics, value: Metrics[keyof Metrics]): string {
  if (metric === 'output_value' || metric === 'po_value' || metric === 'rar' || metric === 'premium') {
    return formatIDR(value as number);
  }
  return (value as number).toLocaleString('en-US');
}

const metricRows: Array<{ key: keyof Metrics; label: string }> = [
  { key: 'units_blocked', label: 'Units blocked' },
  { key: 'output_value', label: 'Delayed output value' },
  { key: 'po_value', label: 'Affected PO value' },
  { key: 'rar', label: 'Revenue at risk' },
  { key: 'so_delayed', label: 'SO delayed' },
  { key: 'premium', label: 'Premium' },
];

function supplierConfirmationStatus(site: NonNullable<RecommendationOption['confirmation']>['sites'][number]): string {
  if (site.status === 'REQUESTED') return 'Requested';
  if (site.status === 'NO_RESPONSE') return 'No response';
  if (site.status === 'ANSWERED' && site.answer) return `Answered: ${site.answer}`;
  return 'Not requested yet';
}

// EP-21 moves the case with OPTION_SUBMITTED (DATA_MODEL 3.3): only from READY_FOR_DECISION or EXECUTED (DEC-043);
// in AWAITING_APPROVAL a new option replaces the waiting one. Any other status answers 409 CONFLICT.
const submittableStatuses: ReadonlyArray<CaseStatus> = ['READY_FOR_DECISION', 'EXECUTED', 'AWAITING_APPROVAL'];

function caseStatusReason(status: CaseStatus): string | null {
  if (submittableStatuses.includes(status)) return null;
  if (status === 'AWAITING_SUPPLIER') return 'Waiting for supplier replies before an option can be submitted';
  return 'The case analysis must finish before an option can be submitted';
}

// EP-22 keeps PROPOSED and FAILED actions in the queue until a decision (EXECUTED and REJECTED drop out).
const WAITING_ACTION_STATUSES = new Set(['PROPOSED', 'FAILED']);

/** The option of the case whose actions wait in the Approvals queue (EP-22), or null. */
export function waitingOption(queue: ApprovalItem[] | undefined, caseId: string): RecommendationOption['option'] | null {
  return queue?.find((item) => item.case_id === caseId && WAITING_ACTION_STATUSES.has(item.status))?.option ?? null;
}

function submitDisabledReason(
  option: RecommendationOption,
  data: Recommendations,
  caseData: CaseDetail,
  latestVersion: number,
  waiting: RecommendationOption['option'] | null,
): string | null {
  // EP-21 always submits from the latest recommendation, so an older version on screen would not match the actions sent.
  if (data.version < latestVersion) return `Open Version ${latestVersion} to submit; this is an older version`;
  // Submitting the waiting option again only re-proposes the same actions (and adds trace rows); another option replaces it.
  if (waiting === option.option) return `Option ${option.option} is waiting for approval on the Approvals page`;
  if (!option.available) return `Option ${option.option} is not available`;
  if (option.pending_confirmation) return `Option ${option.option} is waiting for supplier confirmation`;
  if (option.actions.length === 0) return `Option ${option.option} has no ERP actions`;
  if (data.executive_decision_required) return 'Meeting decision required before submitting';
  if (data.decision && data.decision.option !== option.option) {
    return `Submitted option differs from the meeting decision (Option ${data.decision.option})`;
  }
  return caseStatusReason(caseData.status);
}

function RecommendationOptionCard({
  option,
  data,
  caseData,
  latestVersion,
  onSubmit,
  submitting,
  waiting,
}: {
  option: RecommendationOption;
  data: Recommendations;
  caseData: CaseDetail;
  latestVersion: number;
  onSubmit: (option: RecommendationOption['option']) => void;
  submitting: boolean;
  waiting: RecommendationOption['option'] | null;
}) {
  const [copyMessage, setCopyMessage] = useState('');
  const recommended = data.recommended_option === option.option;
  const pendingConfirmation = option.available && option.pending_confirmation;
  const awaitingSupplier = caseData.status === 'AWAITING_SUPPLIER';
  const disabledReason = submitDisabledReason(option, data, caseData, latestVersion, waiting);
  const isWaiting = waiting === option.option;
  const actionsTitleId = `recommendation-actions-${option.option}`;

  async function copyActionList() {
    if (!navigator.clipboard?.writeText) {
      setCopyMessage('Clipboard access is not available in this browser.');
      return;
    }

    const lines = [
      `${caseData.case_id} — Option ${option.option}: ${option.title}`,
      ...option.actions.map((action) => actionText(action, caseData)),
    ];
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      setCopyMessage('Action list copied.');
    } catch {
      setCopyMessage('The action list could not be copied. Try again from a secure browser page.');
    }
  }

  return (
    <article className={`recommendation-option card${option.available ? '' : ' recommendation-option--unavailable'}${pendingConfirmation ? ' recommendation-option--pending' : ''}${awaitingSupplier ? ' recommendation-option--awaiting' : ''}`}>
      <header className="recommendation-option__header">
        <div>
          <p className="recommendation-option__eyebrow">Option {option.option}</p>
          <h3>{optionTitles[option.option]}</h3>
        </div>
        <div className="recommendation-option__badges">
          {recommended && <Badge variant="NEUTRAL">Recommended</Badge>}
          {!option.available && <Badge variant="NEUTRAL">Unavailable</Badge>}
        </div>
      </header>

      {pendingConfirmation && (
        <section className="recommendation-option__confirmation" aria-label="Supplier confirmation" role="status">
          <strong>Pending supplier confirmation</strong>
          {option.confirmation?.sites.length ? (
            <ul>
              {option.confirmation.sites.map((site) => (
                <li key={site.site_id}><span className="mono">{site.site_id}</span>: {supplierConfirmationStatus(site)}</li>
              ))}
            </ul>
          ) : null}
        </section>
      )}
      {!option.available && option.unavailable_reason && (
        <p className="recommendation-option__unavailable-reason">{option.unavailable_reason}</p>
      )}

      {recommended && data.rule_reason && (
        <div className="recommendation-option__rule-reason">
          <strong>Rule reason</strong>
          <p>{data.rule_reason}</p>
        </div>
      )}

      <section className="recommendation-section" aria-labelledby={`recommendation-metrics-${option.option}`}>
        <h4 id={`recommendation-metrics-${option.option}`}>Before / After</h4>
        <div className="recommendation-table-scroll" role="region" aria-label={`Option ${option.option} before and after metrics`} tabIndex={0}>
          <table className="recommendation-table">
            <thead><tr><th scope="col">Metric</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
            <tbody>
              {metricRows.map(({ key, label }) => (
                <tr key={key}>
                  <th scope="row">{label}</th>
                  <td>{metricValue(key, option.before[key])}</td>
                  <td>{option.after ? metricValue(key, option.after[key]) : '—'}</td>
                </tr>
              ))}
              {(option.before.extra_duty != null || option.after?.extra_duty != null) && (
                <tr>
                  <th scope="row">Extra duty</th>
                  <td>{option.before.extra_duty === null ? '—' : formatIDR(option.before.extra_duty)}</td>
                  <td>{option.after?.extra_duty == null ? '—' : formatIDR(option.after.extra_duty)}</td>
                </tr>
              )}
              {(option.option === 'E' || option.option === 'S') && (
                <tr>
                  <th scope="row">Saving</th>
                  <td>—</td>
                  <td>{option.saving == null ? '—' : formatIDR(option.saving)}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="recommendation-section" aria-labelledby={actionsTitleId}>
        <header className="recommendation-section__header">
          <h4 id={actionsTitleId}>Proposed ERP actions</h4>
          <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>
        </header>
        {option.actions.length ? (
          <div className="recommendation-table-scroll" role="region" aria-label={`Option ${option.option} proposed ERP actions`} tabIndex={0}>
            <table className="recommendation-table recommendation-table--actions" aria-labelledby={actionsTitleId}>
              <thead><tr><th scope="col">#</th><th scope="col">Action</th><th scope="col">Object</th><th scope="col">Change / details</th></tr></thead>
              <tbody>
                {option.actions.map((action) => (
                  <tr key={`${option.option}-${action.seq}`}>
                    <td className="tabular">{action.seq}</td>
                    <td>{actionTypeLabel(action.type)}</td>
                    <td className="mono">{action.object}</td>
                    <td>{actionChange(action, caseData)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="recommendation-option__empty-actions">No ERP actions are proposed for this option.</p>
        )}
      </section>

      <section className="recommendation-option__rationale" aria-label="Recommendation rationale">
        <div className="recommendation-option__rationale-heading">
          <h4>Rationale</h4>
          <span>{option.rationale_source === 'llm' ? 'Recommendation Agent' : 'Sentra template'}</span>
        </div>
        <p>{option.rationale}</p>
      </section>

      {!data.execution_enabled && (
        <footer className="recommendation-option__footer">
          <div>
            <Badge variant="SIMULATED">Proposal only — not executed</Badge>
            {option.actions.length > 0 && (
              <Button variant="secondary" onClick={() => void copyActionList()}>Copy action list</Button>
            )}
            {copyMessage && <p className="recommendation-option__copy-message" role="status">{copyMessage}</p>}
          </div>
        </footer>
      )}
      {data.execution_enabled && data.recommended_option !== null && caseData.status !== 'CLOSED' && caseData.status !== 'DISMISSED' && (
        <footer className="recommendation-option__footer">
          {isWaiting && (
            <span className="recommendation-option__waiting" role="status">
              <Badge variant="NEUTRAL">Submitted — awaiting approval</Badge>
              <Link to="/approvals">Open Approvals</Link>
            </span>
          )}
          {!isWaiting && waiting !== null && disabledReason === null && (
            <span className="recommendation-option__replace-note">Submitting replaces the waiting Option {waiting}.</span>
          )}
          <Button
            variant="primary"
            disabled={disabledReason !== null || submitting}
            loading={submitting}
            title={disabledReason ?? undefined}
            onClick={() => onSubmit(option.option)}
          >
            {isWaiting ? 'Submitted' : 'Submit for approval'}
          </Button>
        </footer>
      )}
    </article>
  );
}

// Outstanding supplier questions of the case: REQUESTED rows of G.6 `requests`, or the site status for sites without rows.
function pendingSupplierRequests(caseData: CaseDetail): string[] {
  return caseData.sites.flatMap((site) => {
    const requested = site.requests.filter((request) => request.status === 'REQUESTED');
    if (requested.length) return requested.map((request) => `${site.site_id} · ${requestTypeLabel(request.request_type)}`);
    return site.requests.length === 0 && site.verification_status === 'REQUESTED' ? [site.site_id] : [];
  });
}

function AwaitingSupplierBanner({ caseData }: { caseData: CaseDetail }) {
  const pending = pendingSupplierRequests(caseData);
  return (
    <section className="recommendations-panel__awaiting" role="status" aria-label="Waiting for supplier replies">
      <span className="orbit recommendations-panel__awaiting-indicator" aria-hidden="true" />
      <div>
        <strong>Waiting for supplier replies</strong>
        <p>The recommendation is recalculated when the replies arrive. Submit for approval opens after that.</p>
        {pending.length > 0 && (
          <ul className="recommendations-panel__awaiting-list">
            {pending.map((item) => <li key={item} className="mono">{item}</li>)}
          </ul>
        )}
      </div>
    </section>
  );
}

function RecommendationsError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="case-detail__state case-detail__state--error" role="alert">
      <p>Recommendations could not be loaded.</p>
      <Button variant="secondary" onClick={onRetry}>Try again</Button>
    </div>
  );
}

/** `latestVersion`: the newest snapshot of the case; defaults to `version` (the page shows the latest unless one is picked). */
export function RecommendationsPanel({ caseData, version, latestVersion = version }: {
  caseData: CaseDetail;
  version: number;
  latestVersion?: number;
}) {
  const recommendations = useRecommendations(caseData.case_id, version);
  const submitRecommendation = useSubmitRecommendation(caseData.case_id);
  // EP-22 tells which option of this case already waits for approval, also after a page reload.
  const approvals = useApprovals(recommendations.data?.execution_enabled === true);
  const [submitMessage, setSubmitMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  // The option submitted in this session, until EP-22 has been read again after it (the queue is then the source).
  const [justSubmitted, setJustSubmitted] = useState<{ option: RecommendationOption['option']; at: number } | null>(null);
  const queueOption = waitingOption(approvals.data, caseData.case_id);
  const queueIsNewer = justSubmitted !== null && (approvals.dataUpdatedAt ?? 0) > justSubmitted.at;
  const waiting = queueOption ?? (justSubmitted && !queueIsNewer ? justSubmitted.option : null);

  useEffect(() => {
    if (submitMessage?.type !== 'success') return undefined;
    const timer = window.setTimeout(() => setSubmitMessage(null), 8000);
    return () => window.clearTimeout(timer);
  }, [submitMessage]);

  async function submitOption(option: RecommendationOption['option']) {
    if (submitRecommendation.isPending) return; // a second click while the first request runs sends nothing
    setSubmitMessage(null);
    try {
      await submitRecommendation.mutateAsync(option);
      setJustSubmitted({ option, at: Date.now() });
      setSubmitMessage({ type: 'success', text: `Option ${option} submitted for approval.` });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'The option could not be submitted for approval.';
      setSubmitMessage({ type: 'error', text: message });
    }
  }

  if (recommendations.isPending) {
    return <p className="case-detail__state" role="status">Loading recommendations…</p>;
  }

  if (recommendations.isError || !recommendations.data) {
    return <RecommendationsError onRetry={() => void recommendations.refetch()} />;
  }

  const data = recommendations.data;
  const decisionOption = decisionOptionLabel(data.decision?.option);
  const approvedBy = data.decision?.approved_by.join(', ') ?? '';
  return (
    <div className="case-detail__content recommendations-panel">
      {data.preliminary && (
        <div className="recommendations-panel__preliminary" role="status">
          <Badge variant="NEUTRAL">PRELIMINARY</Badge>
          <span>Supplier confirmation may change this recommendation.</span>
        </div>
      )}
      {caseData.status === 'AWAITING_SUPPLIER' && <AwaitingSupplierBanner caseData={caseData} />}

      {data.executive_decision_required && (
        <div className="recommendations-panel__executive-decision" role="alert">
          <strong>Executive decision required — record the meeting decision first</strong>
          <Link to={`/cases/${encodeURIComponent(caseData.case_id)}/meeting-room`}>Open Meeting Room</Link>
        </div>
      )}
      {data.decision && (
        <div className="recommendations-panel__decision-recorded" role="status">
          Decision recorded: {decisionOption} approved by {approvedBy}
        </div>
      )}
      {/* Portal to <body>: an animated (transformed) ancestor would make `position: fixed` scroll with the page. */}
      {submitMessage && createPortal(
        <div
          className={`recommendations-panel__toast recommendations-panel__toast--${submitMessage.type}`}
          role={submitMessage.type === 'error' ? 'alert' : 'status'}
        >
          <div className="recommendations-panel__toast-body">
            <strong>{submitMessage.type === 'success' ? 'Submitted for approval' : 'Submit failed'}</strong>
            <span>{submitMessage.text}</span>
            {submitMessage.type === 'success' && (
              <Link to="/approvals">Review the actions on the Approvals page →</Link>
            )}
          </div>
          <button
            type="button"
            className="recommendations-panel__toast-close"
            aria-label="Close message"
            onClick={() => setSubmitMessage(null)}
          >×
          </button>
        </div>,
        document.body,
      )}

      {data.recommended_option === null && (
        <section className="recommendation-no-option card" aria-labelledby="recommendation-no-option-title">
          <h3 id="recommendation-no-option-title">No option</h3>
          {data.rule_reason && <p>{data.rule_reason}</p>}
        </section>
      )}

      {data.options.map((option) => (
        <RecommendationOptionCard
          key={option.option}
          option={option}
          data={data}
          caseData={caseData}
          latestVersion={latestVersion}
          onSubmit={(selectedOption) => void submitOption(selectedOption)}
          submitting={submitRecommendation.isPending}
          waiting={waiting}
        />
      ))}

      <section className="recommendation-non-erp card" aria-labelledby="recommendation-non-erp-title">
        <h3 id="recommendation-non-erp-title">Non-ERP steps</h3>
        {data.non_erp_steps.length ? (
          <ul>{data.non_erp_steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ul>
        ) : (
          <p>No additional non-ERP steps are listed for this recommendation.</p>
        )}
      </section>
    </div>
  );
}
