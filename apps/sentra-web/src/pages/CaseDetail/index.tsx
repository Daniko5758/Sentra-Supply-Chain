import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useCaseDetail, useCaseDisposition, useRecommendations } from '../../api/hooks';
import type { CaseDetail, CaseVersion, Impact } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { Badge } from '../../components/Badge';
import { AgentActivityTimeline } from '../../components/AgentActivityTimeline';
import { Button } from '../../components/Button';
import { ChatConversation } from '../../components/ChatConversation';
import { EscalationCard } from '../../components/EscalationCard';
import { CaseDispositionDialog, type CaseDisposition } from './CaseDispositionDialog';
import { RecommendationsPanel } from './RecommendationsPanel';
import { ScoreHelp } from './ScoreHelp';
import { bcpBandLabel } from '../../lib/c42';
import {
  formatDate,
  formatDateTime,
  formatDemoOffset,
  formatIDR,
  formatIDRShort,
  formatPoLine,
  formatPercent,
  formatScore,
} from '../../lib/format';
import { NetworkExplorer } from '../SupplierNetwork/NetworkExplorer';
import { ActiveBackupSites, CountryDisruptionSummary } from './C42Content';
import { ReportPanel } from '../CaseReport/ReportPanel';
import { VerificationPanel } from './VerificationPanel';
import './case-detail.css';

const CASE_TABS = [
  'Overview',
  'Impact',
  'Network',
  'Verification',
  'Agent activity',
  'Chat',
  'Recommendations',
  'Report',
] as const;

type CaseTab = (typeof CASE_TABS)[number];

function labelFromCode(value: string): string {
  return value
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function versionTriggerLabel(trigger: CaseVersion['trigger']): string {
  switch (trigger) {
    case 'supplier_reply': return 'After supplier replies';
    case 'erp_execution': return 'After ERP execution';
    default: return 'Initial analysis';
  }
}

function CaseDate({
  value,
  d0Date,
  demo,
}: {
  value: string | null;
  d0Date: string;
  demo: boolean;
}) {
  if (!value) return <span>No date within horizon</span>;
  return (
    <time className="case-date" dateTime={value}>
      <span>{formatDate(value)}</span>
      {demo && <span className="case-date__offset">{formatDemoOffset(value, d0Date)}</span>}
    </time>
  );
}

function CaseDateTime({
  value,
  d0Date,
  demo,
}: {
  value: string;
  d0Date: string;
  demo: boolean;
}) {
  return (
    <time className="case-datetime" dateTime={value}>
      <span>{formatDateTime(value)}</span>
      {demo && <span className="case-date__offset">{formatDemoOffset(value.slice(0, 10), d0Date)}</span>}
    </time>
  );
}

function CaseKpi({ label, children, className = '' }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={`case-kpi ${className}`.trim()}>
      <span className="case-kpi__label">{label}</span>
      <strong className="case-kpi__value">{children}</strong>
    </div>
  );
}

function CaseTableSection({
  id,
  title,
  description,
  simulatedSource = false,
  children,
}: {
  id: string;
  title: string;
  description?: string;
  simulatedSource?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="case-detail__panel card" aria-labelledby={id}>
      <header className="case-detail__panel-header">
        <div>
          <h3 id={id}>{title}</h3>
          {description && <p>{description}</p>}
        </div>
        {simulatedSource && <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>}
      </header>
      {children}
    </section>
  );
}

function CaseDetailError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="case-detail__state case-detail__state--error" role="alert">
      <p>Case details could not be loaded.</p>
      <Button variant="secondary" onClick={onRetry}>Try again</Button>
    </div>
  );
}

const CLOSABLE_STATUSES: ReadonlyArray<CaseDetail['status']> = ['READY_FOR_DECISION', 'EXECUTED'];

/** EP-19 buttons for the SCM Manager (PRD 8.3): Close case from READY_FOR_DECISION or EXECUTED, Dismiss from any open status. */
function CaseDispositionControls({ data }: { data: CaseDetail }) {
  const disposition = useCaseDisposition(data.case_id);
  const [dialog, setDialog] = useState<{ action: CaseDisposition; trigger: HTMLElement } | null>(null);
  const [message, setMessage] = useState('');
  const open = data.status !== 'CLOSED' && data.status !== 'DISMISSED';
  const closable = CLOSABLE_STATUSES.includes(data.status);

  async function confirm(action: CaseDisposition, comment: string) {
    await disposition.mutateAsync({ action, comment });
    setDialog(null);
    setMessage(action === 'close' ? 'Case closed.' : 'Case dismissed.');
  }

  return (
    <div className="case-disposition">
      {open && (
        <div className="case-disposition__buttons">
          <Button
            variant="secondary"
            disabled={!closable}
            title={closable ? undefined : 'A case can be closed when it is Ready For Decision or Executed'}
            onClick={(event) => setDialog({ action: 'close', trigger: event.currentTarget })}
          >
            Close case
          </Button>
          <Button variant="danger" onClick={(event) => setDialog({ action: 'dismiss', trigger: event.currentTarget })}>
            Dismiss…
          </Button>
        </div>
      )}
      {message && <p className="case-disposition__message" role="status">{message}</p>}
      {dialog && (
        <CaseDispositionDialog
          action={dialog.action}
          caseLabel={`${data.case_id} · ${data.title}`}
          returnFocusTo={dialog.trigger}
          busy={disposition.isPending}
          onCancel={() => setDialog(null)}
          onConfirm={(comment) => confirm(dialog.action, comment)}
        />
      )}
    </div>
  );
}

function CaseHeader({
  data,
  selectedVersion,
  onVersionChange,
  canDispose,
}: {
  data: CaseDetail;
  selectedVersion: number;
  onVersionChange: (version: number) => void;
  canDispose: boolean;
}) {
  const demo = data.mode === 'DEMO';
  return (
    <>
      <div className="case-detail__breadcrumb"><Link to="/cases">Cases</Link><span aria-hidden="true">/</span><span>{data.case_id}</span></div>
      <div className="case-detail__header">
        <div className="case-detail__title-block">
          <div className="page-header">
            <h1>{data.title}</h1>
          </div>
          <div className="case-detail__badges">
            <Badge variant={data.simulated ? 'SIMULATED' : 'LIVE'} />
            <Badge variant={data.kpi.level}>{data.kpi.level}</Badge>
            <span className="cases-status">{labelFromCode(data.status)}</span>
          </div>
          <p className="case-detail__meta">
            <span className="mono">{data.case_id}</span>
            <span>{labelFromCode(data.risk_type)}</span>
            <span>{labelFromCode(data.scope)}</span>
            <span>{data.location || data.country} · {data.country}</span>
          </p>
        </div>
        <div className="case-detail__side">
          <label className="case-version">
            <span>Version</span>
            <select
              aria-label="Case version"
              value={selectedVersion}
              onChange={(event) => onVersionChange(Number(event.target.value))}
            >
              {data.versions.map((item) => (
                <option key={item.version} value={item.version}>
                  Version {item.version} · {versionTriggerLabel(item.trigger)}
                </option>
              ))}
            </select>
          </label>
          {canDispose && <CaseDispositionControls data={data} />}
        </div>
      </div>
      <p className="page-description">
        Created <CaseDateTime value={data.created_at} d0Date={data.d0_date} demo={demo} />
        <span className="case-detail__updated">Updated <CaseDateTime value={data.updated_at} d0Date={data.d0_date} demo={demo} /></span>
      </p>
    </>
  );
}

function OverviewPanel({ data, meetingRoomAvailable }: { data: CaseDetail; meetingRoomAvailable: boolean }) {
  const demo = data.mode === 'DEMO';
  return (
    <div className="case-detail__content">
      {data.preliminary && (
        <div className="case-detail__preliminary" role="status">
          <strong>Preliminary analysis</strong>
          <span>Supplier verification may change this case snapshot.</span>
        </div>
      )}

      <section className="case-kpis" aria-label="Case key performance indicators">
        <CaseKpi label="Units at risk">{data.kpi.units_at_risk.toLocaleString('en-US')}</CaseKpi>
        <CaseKpi label="Delayed output value">{formatIDRShort(data.kpi.delayed_output_value)}</CaseKpi>
        <CaseKpi label="Affected PO value">{formatIDRShort(data.kpi.affected_po_value)}</CaseKpi>
        <CaseKpi label="Revenue at risk">{formatIDRShort(data.kpi.revenue_at_risk)}</CaseKpi>
        <CaseKpi label="Priority score" className="case-kpi--score">
          <span>{formatScore(data.kpi.priority_score)}</span>
          <ScoreHelp priority={data.priority} />
        </CaseKpi>
        <CaseKpi label="Level"><Badge variant={data.kpi.level}>{data.kpi.level}</Badge></CaseKpi>
        <CaseKpi label="Status"><span className="case-kpi__status">{labelFromCode(data.kpi.status)}</span></CaseKpi>
      </section>

      {data.escalation && (
        <div className="case-detail__escalation">
          <EscalationCard escalation={data.escalation} compact />
          {meetingRoomAvailable && (
            <Link className="case-detail__meeting-room-link" to={`/cases/${encodeURIComponent(data.case_id)}/meeting-room`}>
              Open Meeting Room
            </Link>
          )}
        </div>
      )}

      <ActiveBackupSites sites={data.sites} />

      <section className="case-detail__panel card" aria-labelledby="case-event-title">
        <header className="case-detail__panel-header">
          <div>
            <h3 id="case-event-title">Risk event</h3>
            <p>{labelFromCode(data.risk_type)} · {labelFromCode(data.scope)} · {data.location || data.country}</p>
          </div>
          <Badge variant={data.simulated ? 'SIMULATED' : 'LIVE'} />
        </header>
        <div className="case-event__body">
          <p className="case-event__summary">{data.signal.summary}</p>
          {data.signal.severity !== null && (
            <p className="case-event__severity">Severity <strong>{data.signal.severity}</strong></p>
          )}
          <div className="case-event__source">
            <span className="case-detail__eyebrow">Source</span>
            {data.signal.source_label && <span>{data.signal.source_label}</span>}
            {data.signal.source_urls.length > 0 ? (
              <ul>
                {data.signal.source_urls.map((url) => (
                  <li key={url}><a href={url} target="_blank" rel="noreferrer">{url}</a></li>
                ))}
              </ul>
            ) : (
              <span className="case-detail__muted">No source URLs attached.</span>
            )}
          </div>
          <div className="case-event__footer">
            <span>Signal <span className="mono">{data.signal.signal_id}</span></span>
            <span className="case-event__updated">Updated <CaseDateTime value={data.updated_at} d0Date={data.d0_date} demo={demo} /></span>
          </div>
        </div>
      </section>
    </div>
  );
}

function ImpactPanel({ data }: { data: CaseDetail }) {
  const demo = data.mode === 'DEMO';
  const priorityBySite = new Map(data.priority.map((row) => [row.site_id, row]));
  const impact: Impact = data.impact;

  return (
    <div className="case-detail__content case-detail__content--impact">
      <CountryDisruptionSummary disruption={impact.disruption} />
      {(impact.tariff || impact.exposure) && (
        <div className="case-impact-highlights">
          {impact.tariff && (
            <section className="case-detail__panel card" aria-labelledby="case-tariff-title">
              <h3 id="case-tariff-title">Tariff impact</h3>
              <dl className="case-impact-facts">
                <div><dt>Country</dt><dd>{impact.tariff.country}</dd></div>
                <div><dt>Category</dt><dd>{impact.tariff.category}</dd></div>
                <div><dt>Tariff</dt><dd>{formatPercent(impact.tariff.tariff_pp)}</dd></div>
                <div><dt>Effective</dt><dd><CaseDate value={impact.tariff.effective_date} d0Date={data.d0_date} demo={demo} /></dd></div>
                <div><dt>Affected value</dt><dd>{formatIDR(impact.tariff.affected_value)}</dd></div>
                <div><dt>Extra duty</dt><dd>{formatIDR(impact.tariff.extra_duty)}</dd></div>
              </dl>
              {impact.tariff.not_stated.length > 0 && (
                <p className="case-detail__muted">Not stated in source: {impact.tariff.not_stated.join(', ')}</p>
              )}
            </section>
          )}
          {impact.exposure && (
            <section className="case-detail__panel card" aria-labelledby="case-exposure-title">
              <h3 id="case-exposure-title">Country exposure</h3>
              <p className="case-impact-highlight__value">{formatIDR(impact.exposure.po_value)}</p>
              <p>{impact.exposure.note}</p>
            </section>
          )}
        </div>
      )}

      <CaseTableSection id="case-sites-title" title="Site candidates" description={`${data.sites.length} candidate sites`}>
        <div className="case-table-scroll" role="region" aria-label="Site candidates" tabIndex={0}>
          <table className="case-table" aria-labelledby="case-sites-title">
            <thead><tr>
              <th scope="col">Supplier</th><th scope="col">Tier</th><th scope="col">Path</th>
              <th scope="col">Recovery time</th><th scope="col">Revenue at risk</th>
              <th scope="col">Priority score</th><th scope="col">BCP score</th><th scope="col">Verification</th>
            </tr></thead>
            <tbody>
              {data.sites.length ? data.sites.map((site) => {
                const priority = priorityBySite.get(site.site_id);
                return (
                  <tr key={site.site_id}>
                    <td><span className="case-table__primary">{site.supplier_name}</span><span className="case-table__secondary mono">{site.site_id}</span></td>
                    <td>{site.tier}</td>
                    <td className="case-table__path">{site.path}</td>
                    <td className="tabular">{site.recovery_days} days</td>
                    <td className="tabular">{priority ? formatIDR(priority.rar) : '—'}</td>
                    <td className="tabular">{priority ? formatScore(priority.score) : '—'}</td>
                    <td className="tabular">{priority ? `${formatScore(priority.bcp_score)} · ${bcpBandLabel(priority.bcp_band)}` : '—'}</td>
                    <td>{labelFromCode(site.verification_status)}</td>
                  </tr>
                );
              }) : <tr><td className="case-table__empty" colSpan={8}>No site candidates for this case.</td></tr>}
            </tbody>
          </table>
        </div>
      </CaseTableSection>

      <CaseTableSection id="case-part-delays-title" title="Part delays" description={`${impact.part_delays.length} affected parts`}>
        <div className="case-table-scroll" role="region" aria-label="Part delays" tabIndex={0}>
          <table className="case-table" aria-labelledby="case-part-delays-title">
            <thead><tr><th scope="col">Part</th><th scope="col">Delay</th><th scope="col">Cause site</th><th scope="col">Via site</th></tr></thead>
            <tbody>
              {impact.part_delays.length ? impact.part_delays.map((part) => (
                <tr key={part.part_id}>
                  <td className="mono">{part.part_id}</td><td className="tabular">{part.days} days</td>
                  <td className="mono">{part.cause_site ?? '—'}</td><td className="mono">{part.via_site ?? '—'}</td>
                </tr>
              )) : <tr><td className="case-table__empty" colSpan={4}>No part delays are recorded.</td></tr>}
            </tbody>
          </table>
        </div>
      </CaseTableSection>

      <CaseTableSection id="case-po-title" title="Affected purchase order lines" description={`${impact.po_lines.length} affected PO lines`} simulatedSource>
        <div className="case-table-scroll" role="region" aria-label="Affected purchase order lines" tabIndex={0}>
          <table className="case-table case-table--wide" aria-labelledby="case-po-title">
            <thead><tr>
              <th scope="col">PO line</th><th scope="col">Part</th><th scope="col">Supplier</th><th scope="col">Qty</th>
              <th scope="col">Confirmed</th><th scope="col">New ETA</th><th scope="col">Slip</th><th scope="col">Value</th><th scope="col">Cause</th>
            </tr></thead>
            <tbody>
              {impact.po_lines.length ? impact.po_lines.map((line) => (
                <tr key={`${line.po_id}-${line.item_no}`}>
                  <td className="mono">{formatPoLine(line.po_id, line.item_no)}</td><td className="mono">{line.material_id}</td>
                  <td className="mono">{line.supplier_id}</td><td className="tabular">{line.qty.toLocaleString('en-US')}</td>
                  <td><CaseDate value={line.confirmed_date} d0Date={data.d0_date} demo={demo} /></td>
                  <td><CaseDate value={line.new_eta} d0Date={data.d0_date} demo={demo} /></td>
                  <td className="tabular">{line.slip_days} days</td><td className="tabular">{formatIDR(line.value)}</td>
                  <td><span className="mono">{line.cause_site}</span>{line.via_site && <span className="case-table__secondary">via {line.via_site}</span>}</td>
                </tr>
              )) : <tr><td className="case-table__empty" colSpan={9}>No affected purchase order lines.</td></tr>}
            </tbody>
          </table>
        </div>
      </CaseTableSection>

      <CaseTableSection id="case-production-title" title="Affected production orders" description={`${impact.production_orders.length} affected production orders`} simulatedSource>
        <div className="case-table-scroll" role="region" aria-label="Affected production orders" tabIndex={0}>
          <table className="case-table case-table--wide" aria-labelledby="case-production-title">
            <thead><tr>
              <th scope="col">Order</th><th scope="col">Product</th><th scope="col">Qty</th><th scope="col">Units blocked</th>
              <th scope="col">Blocking parts</th><th scope="col">Ready date</th><th scope="col">Output value</th><th scope="col">Sales order</th>
            </tr></thead>
            <tbody>
              {impact.production_orders.length ? impact.production_orders.map((order) => (
                <tr key={order.order_id}>
                  <td className="mono">{order.order_id}</td><td>{order.product_id}</td><td className="tabular">{order.qty.toLocaleString('en-US')}</td>
                  <td className="tabular">{order.units_blocked.toLocaleString('en-US')}</td><td>{order.blocking_parts.join(', ') || '—'}</td>
                  <td><CaseDate value={order.ready_date} d0Date={data.d0_date} demo={demo} /></td><td className="tabular">{formatIDR(order.output_value)}</td>
                  <td className="mono">{order.sales_order_id}</td>
                </tr>
              )) : <tr><td className="case-table__empty" colSpan={8}>No affected production orders.</td></tr>}
            </tbody>
          </table>
        </div>
      </CaseTableSection>

      <CaseTableSection id="case-sales-title" title="Affected sales orders" description={`${impact.sales_orders.length} affected sales orders`} simulatedSource>
        <div className="case-table-scroll" role="region" aria-label="Affected sales orders" tabIndex={0}>
          <table className="case-table" aria-labelledby="case-sales-title">
            <thead><tr><th scope="col">Sales order</th><th scope="col">Customer</th><th scope="col">Old date</th><th scope="col">New date</th><th scope="col">Units at risk</th></tr></thead>
            <tbody>
              {impact.sales_orders.length ? impact.sales_orders.map((order) => (
                <tr key={order.so_id}>
                  <td className="mono">{order.so_id}</td><td>{order.customer}</td>
                  <td><CaseDate value={order.old_date} d0Date={data.d0_date} demo={demo} /></td>
                  <td><CaseDate value={order.new_date} d0Date={data.d0_date} demo={demo} /></td>
                  <td className="tabular">{order.units_at_risk.toLocaleString('en-US')}</td>
                </tr>
              )) : <tr><td className="case-table__empty" colSpan={5}>No affected sales orders.</td></tr>}
            </tbody>
          </table>
        </div>
      </CaseTableSection>
    </div>
  );
}

/** Case chat: the suggested questions name the case level and its latest recommended option (EP-15). */
function CaseChat({ data }: { data: CaseDetail }) {
  const recommendations = useRecommendations(data.case_id);
  return (
    <ChatConversation
      caseId={data.case_id}
      caseContext={{ level: data.kpi.level, recommendedOption: recommendations.data?.recommended_option }}
    />
  );
}

function CaseDetailContent({ caseId }: { caseId: string }) {
  const [selectedVersion, setSelectedVersion] = useState<number | undefined>();
  const [activeTab, setActiveTab] = useState<CaseTab>('Overview');
  const { me, isScmManager } = useAuth();
  // Halaman kasus di-poll tiap 10 detik (AGENTS.md sentra-web 2.5) agar hasil agen muncul tanpa muat ulang.
  const detail = useCaseDetail(caseId, true, selectedVersion, true);

  if (detail.isPending) {
    return (
      <section className="case-detail-page">
        <div className="page-header"><h1>Case details</h1></div>
        <p className="case-detail__state" role="status">Loading case details…</p>
      </section>
    );
  }

  // Hanya tanpa data yang menampilkan galat; polling latar yang gagal tidak boleh menyembunyikan halaman.
  if (!detail.data) {
    return (
      <section className="case-detail-page">
        <div className="page-header"><h1>Case details</h1></div>
        <CaseDetailError onRetry={() => void detail.refetch()} />
      </section>
    );
  }

  const data = detail.data;
  const version = selectedVersion ?? data.version;
  // Without a picked version the page shows the latest snapshot; a picked one is compared with the newest in the list.
  const latestVersion = selectedVersion === undefined
    ? version
    : Math.max(version, ...data.versions.map((item) => item.version));

  return (
    <section className="case-detail-page">
      <CaseHeader data={data} selectedVersion={version} onVersionChange={setSelectedVersion} canDispose={isScmManager} />

      <nav className="case-tabs" aria-label="Case detail sections">
        {CASE_TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            className={`case-tabs__button${activeTab === tab ? ' is-active' : ''}`}
            aria-pressed={activeTab === tab}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </nav>

      {activeTab === 'Overview' ? <OverviewPanel data={data} meetingRoomAvailable={me?.feature_flags.meeting_room === true} />
        : activeTab === 'Impact' ? <ImpactPanel data={data} />
          : activeTab === 'Network' ? <NetworkExplorer fixedCaseId={data.case_id} version={version} embedded />
            : activeTab === 'Verification' ? <VerificationPanel data={data} />
              : activeTab === 'Recommendations' ? <RecommendationsPanel caseData={data} version={version} latestVersion={latestVersion} />
              : activeTab === 'Report' ? <ReportPanel caseId={data.case_id} caseData={data} />
              : activeTab === 'Chat' ? (
                <section className="case-chat card" aria-labelledby="case-chat-title">
                  <header className="case-chat__header">
                    <div>
                      <h2 id="case-chat-title">Chat about this case</h2>
                      <p>Ask SENTRA about this case and its supply chain impact.</p>
                    </div>
                    <span>{data.case_id}</span>
                  </header>
                  <CaseChat data={data} />
                </section>
              )
            : activeTab === 'Agent activity' ? <AgentActivityTimeline caseId={data.case_id} />
          : null}
    </section>
  );
}

export function CaseDetailPage() {
  const { caseId } = useParams();
  if (!caseId) {
    return (
      <section className="case-detail-page">
        <div className="page-header"><h1>Case not found</h1></div>
        <Link to="/cases">Back to cases</Link>
      </section>
    );
  }
  return <CaseDetailContent key={caseId} caseId={caseId} />;
}
