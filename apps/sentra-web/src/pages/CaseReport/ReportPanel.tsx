import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ApiError } from '../../api/client';
import {
  useAgentRun,
  useCaseDetail,
  useCaseReport,
  useGenerateCaseReport,
  useSchedule,
  useSupplierMessages,
} from '../../api/hooks';
import type { Action, ActionPayload, CaseDetail, CaseReport, RecommendationOption } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatDate, formatDateTime, formatDemoOffset, formatIDR, formatIDRShort, formatPoLine, formatScore } from '../../lib/format';
import './report.css';

interface NarrativeSection {
  title: string;
  body: string;
}

function parseNarrative(markdown: string): NarrativeSection[] {
  const sections: NarrativeSection[] = [];
  let current: NarrativeSection = { title: 'Executive summary', body: '' };

  for (const line of markdown.split(/\r?\n/)) {
    const heading = /^#{1,6}\s+(.+?)\s*$/.exec(line);
    if (heading) {
      sections.push(current);
      current = { title: heading[1], body: '' };
    } else {
      current.body += `${current.body ? '\n' : ''}${line}`;
    }
  }
  sections.push(current);
  return sections.filter((section) => section.body.trim() || sections.length === 1);
}

function inlineMarkdown(text: string, keyPrefix: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={`${keyPrefix}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

function MarkdownContent({ markdown }: { markdown: string }) {
  const blocks = markdown.trim().split(/\n\s*\n/).filter(Boolean);
  return (
    <div className="case-report__markdown">
      {blocks.map((block, blockIndex) => {
        const lines = block.split(/\r?\n/);
        const listItems = lines.map((line) => /^\s*[-*]\s+(.+)$/.exec(line));
        if (listItems.every(Boolean)) {
          return (
            <ul key={`list-${blockIndex}`}>
              {listItems.map((match, itemIndex) => (
                <li key={`item-${itemIndex}`}>{inlineMarkdown(match?.[1] ?? '', `list-${blockIndex}-${itemIndex}`)}</li>
              ))}
            </ul>
          );
        }

        return (
          <p key={`paragraph-${blockIndex}`}>
            {lines.map((line, lineIndex) => (
              <span key={`line-${lineIndex}`}>
                {lineIndex > 0 && <br />}
                {inlineMarkdown(line, `paragraph-${blockIndex}-${lineIndex}`)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

function narrativeBody(sections: NarrativeSection[], title: string): string {
  return sections.find((section) => section.title.trim().toLowerCase() === title.toLowerCase())?.body ?? '';
}

function ReportSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="case-report__section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function CaseDate({ value, caseData }: { value: string; caseData: CaseDetail }) {
  const date = formatDate(value);
  return <>{caseData.mode === 'DEMO' ? `${date} (${formatDemoOffset(value, caseData.d0_date)})` : date}</>;
}

function sourceHref(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function actionLabel(type: Action['type']): string {
  const labels: Record<Action['type'], string> = {
    CHANGE_PO_DATE: 'Change purchase order date',
    CREATE_PR_PO: 'Create purchase requisition',
    REDUCE_OR_CANCEL_PO: 'Reduce purchase order quantity',
    RESCHEDULE_PRODUCTION_ORDER: 'Reschedule production order',
    RESCHEDULE_SALES_ORDER: 'Reschedule sales order',
    EXPEDITE_SHIPMENT: 'Expedite shipment',
  };
  return labels[type];
}

function actionPayloadSummary(payload: ActionPayload, caseData: CaseDetail): string {
  return Object.entries(payload).map(([key, value]) => {
    const label = key.replaceAll('_', ' ');
    if (typeof value === 'number' && /price|value/i.test(key)) return `${label}: ${formatIDR(value)}`;
    if (typeof value === 'number') return `${label}: ${value.toLocaleString('en-US')}`;
    if (typeof value === 'string' && /date/i.test(key)) return `${label}: ${formatDate(value)}${caseData.mode === 'DEMO' ? ` (${formatDemoOffset(value, caseData.d0_date)})` : ''}`;
    return `${label}: ${value}`;
  }).join(' · ');
}

function OptionSummary({ report }: { report: CaseReport }) {
  const recommendation = report.data.recommendation;
  return (
    <>
      {recommendation.preliminary && <p className="case-report__note"><Badge variant="NEUTRAL">PRELIMINARY</Badge> Supplier confirmation may change this recommendation.</p>}
      {recommendation.rule_reason && <p className="case-report__rule-reason"><strong>Rule reason:</strong> {recommendation.rule_reason}</p>}
      <div className="case-report__table-scroll">
        <table className="case-report__table">
          <thead><tr><th scope="col">Option</th><th scope="col">Availability</th><th scope="col">Revenue at risk before</th><th scope="col">Revenue at risk after</th><th scope="col">Premium</th></tr></thead>
          <tbody>
            {recommendation.options.map((option) => {
              const isRecommended = option.available && option.option !== 'E' && recommendation.recommended_option === option.option;
              return (
                <tr key={option.option}>
                  <th scope="row">
                    {option.title}
                    {isRecommended && <span className="case-report__table-badge">Recommended</span>}
                    {option.option === 'E' && option.label && <span className="case-report__table-badge">{option.label}</span>}
                  </th>
                  <td>{option.available ? 'Available' : option.unavailable_reason ?? 'Unavailable'}</td>
                  <td>{formatIDRShort(option.before.rar)}</td>
                  <td>{option.after ? formatIDRShort(option.after.rar) : '—'}</td>
                  <td>{option.after ? formatIDRShort(option.after.premium) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {recommendation.options.map((option) => (
        <div className="case-report__option-rationale" key={`rationale-${option.option}`}>
          <h3>{option.title}</h3>
          <p>{option.rationale}</p>
        </div>
      ))}
      {recommendation.non_erp_steps.length > 0 && (
        <div className="case-report__non-erp">
          <h3>Non-ERP steps</h3>
          <ul>{recommendation.non_erp_steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ul>
        </div>
      )}
    </>
  );
}

function ProposedActions({ option, caseData }: { option: RecommendationOption | undefined; caseData: CaseDetail }) {
  const actions = option?.actions ?? [];
  return (
    <>
      <p className="case-report__note"><Badge variant="SIMULATED">Proposed actions — not executed</Badge></p>
      {actions.length ? (
        <div className="case-report__table-scroll">
          <table className="case-report__table">
            <thead><tr><th scope="col">#</th><th scope="col">Action</th><th scope="col">Object</th><th scope="col">Proposed change</th></tr></thead>
            <tbody>{actions.map((action) => (
              <tr key={`${option?.option}-${action.seq}`}>
                <td>{action.seq}</td><td>{actionLabel(action.type)}</td><td>{action.object}</td><td>{actionPayloadSummary(action.payload, caseData)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <p>No proposed ERP actions are available for this report.</p>}
    </>
  );
}

function ActionStatusTable({ actions, caseData }: { actions: Action[]; caseData: CaseDetail }) {
  if (actions.length === 0) return null;
  return (
    <div className="case-report__table-scroll">
      <table className="case-report__table">
        <thead><tr><th scope="col">Action</th><th scope="col">Object</th><th scope="col">Status</th><th scope="col">Details</th></tr></thead>
        <tbody>{actions.map((action) => (
          <tr key={action.action_id}>
            <td>{actionLabel(action.type)}</td><td>{action.object}</td><td>{action.status.replaceAll('_', ' ')}</td><td>{actionPayloadSummary(action.payload, caseData)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <article className="case-report__chart-card">
      <h3>{title}</h3>
      {children}
    </article>
  );
}

function ReportCharts({ report }: { report: CaseReport }) {
  const { charts } = report;
  return (
    <div className="case-report__charts">
      <ChartCard title="Revenue at risk by site">
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={charts.rar_by_site} margin={{ top: 8, right: 12, bottom: 4, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="site_id" />
            <YAxis tickFormatter={(value: number) => formatIDRShort(value)} width={84} />
            <Tooltip formatter={(value) => formatIDRShort(Number(value))} />
            <Bar dataKey="rar" name="Revenue at risk" fill="#547fa8" radius={[5, 5, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
      <ChartCard title="Delay days by part">
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={charts.delay_by_part} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="part_id" />
            <YAxis allowDecimals={false} />
            <Tooltip formatter={(value) => `${Number(value).toLocaleString('en-US')} days`} />
            <Bar dataKey="days" name="Delay" fill="#c27b49" radius={[5, 5, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
      <ChartCard title="Units at risk by production order">
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={charts.units_by_order} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="order_id" />
            <YAxis allowDecimals={false} />
            <Tooltip formatter={(value) => Number(value).toLocaleString('en-US')} />
            <Bar dataKey="units" name="Units at risk" fill="#678c68" radius={[5, 5, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
      <ChartCard title="Revenue at risk before vs after">
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={charts.rar_by_version} margin={{ top: 8, right: 12, bottom: 4, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="version" tickFormatter={(value: number) => `v${value}`} />
            <YAxis tickFormatter={(value: number) => formatIDRShort(value)} width={84} />
            <Tooltip labelFormatter={(value) => `Version ${value}`} formatter={(value) => formatIDRShort(Number(value))} />
            <Bar dataKey="rar" name="Revenue at risk" fill="#846fa8" radius={[5, 5, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  );
}

function ReportError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="case-report__state case-report__state--error" role="alert">
      <p>{message}</p>
      <Button variant="secondary" onClick={onRetry}>Try again</Button>
    </div>
  );
}

export function ReportPanel({ caseId, caseData: suppliedCaseData, standalone = false }: {
  caseId: string;
  caseData?: CaseDetail;
  standalone?: boolean;
}) {
  const reportQuery = useCaseReport(caseId);
  const needsReportSnapshot = !suppliedCaseData || Boolean(reportQuery.data && suppliedCaseData.version !== reportQuery.data.version);
  const caseQuery = useCaseDetail(caseId, needsReportSnapshot, reportQuery.data?.version);
  const caseData = needsReportSnapshot ? caseQuery.data : suppliedCaseData;
  const generateMutation = useGenerateCaseReport(caseId);
  const scheduleQuery = useSchedule();
  const messageQuery = useSupplierMessages([caseId])[0];
  const [runId, setRunId] = useState<string | null>(null);
  const runQuery = useAgentRun(runId);
  const refreshedRunId = useRef<string | null>(null);
  const narrative = useMemo(() => parseNarrative(reportQuery.data?.narrative_md ?? ''), [reportQuery.data?.narrative_md]);
  const escalationNarrative = narrativeBody(narrative, 'Escalation');

  useEffect(() => {
    const previousTitle = document.title;
    if (reportQuery.data) {
      document.title = reportQuery.data.file_title;
    }
    return () => {
      document.title = previousTitle;
    };
  }, [reportQuery.data?.file_title]);

  useEffect(() => {
    if (runId && runQuery.data?.status === 'OK' && refreshedRunId.current !== runId) {
      refreshedRunId.current = runId;
      void reportQuery.refetch();
    }
  }, [reportQuery.refetch, runId, runQuery.data?.status]);

  function generateReport() {
    refreshedRunId.current = null;
    setRunId(null);
    generateMutation.mutate(undefined, {
      onSuccess: (result) => setRunId(result.run_id),
    });
  }

  const report = reportQuery.data;
  const caseReportNotFound = reportQuery.isError && reportQuery.error instanceof ApiError && reportQuery.error.status === 404;
  const generationPending = generateMutation.isPending || runQuery.data?.status === 'QUEUED' || runQuery.data?.status === 'RUNNING';
  const latestOption = report?.data.recommendation.options.find((option) => option.option === report.data.recommendation.recommended_option);
  const casePoKeys = new Set(caseData?.impact.po_lines.map((line) => `${line.po_id}/${line.item_no}`) ?? []);
  const caseImpactByPo = new Map(caseData?.impact.po_lines.map((line) => [`${line.po_id}/${line.item_no}`, line]) ?? []);
  const relatedScheduleLines = scheduleQuery.data?.po_lines.filter((line) => casePoKeys.has(`${line.po_id}/${line.item_no}`)) ?? [];

  return (
    <article className={`case-report${standalone ? ' case-report--standalone' : ''}`}>
      {standalone ? (
        <header className="case-report__standalone-header">
          <Link to={`/cases/${encodeURIComponent(caseId)}`} className="case-report__back-link">Back to case</Link>
          <div className="case-report__page-title">
            <p className="case-report__eyebrow">Case report</p>
            <h1>{caseData?.title ?? caseId}</h1>
          </div>
        </header>
      ) : (
        <header className="case-report__tab-header">
          <div>
            <p className="case-report__eyebrow">Generated from case snapshot</p>
            <h2>Report</h2>
          </div>
          <Link to={`/cases/${encodeURIComponent(caseId)}/report`} className="case-report__open-link">Open full report</Link>
        </header>
      )}

      <div className="case-report__toolbar">
        <div className="case-report__toolbar-status" aria-live="polite">
          {generateMutation.isPending && <span role="status">Queueing report generation…</span>}
          {runQuery.data?.status === 'QUEUED' && <span role="status">Report generation queued.</span>}
          {runQuery.data?.status === 'RUNNING' && <span role="status">Report is being generated…</span>}
          {runQuery.data?.status === 'OK' && <span role="status">Report generated successfully.</span>}
          {runQuery.data && ['ERROR', 'AGENT_TIMEOUT', 'SKIPPED'].includes(runQuery.data.status) && (
            <span role="alert">Report generation {runQuery.data.status.toLowerCase().replaceAll('_', ' ')}. Try again.</span>
          )}
          {runQuery.isError && <span role="alert">Report status could not be loaded.</span>}
          {generateMutation.isError && <span role="alert">Report could not be generated. Try again.</span>}
        </div>
        <div className="case-report__toolbar-actions">
          <Button variant="secondary" onClick={generateReport} disabled={generationPending}>Generate report</Button>
          <Button variant="primary" onClick={() => window.print()} disabled={!report}>Export PDF</Button>
        </div>
      </div>

      {caseQuery.isError && !suppliedCaseData && (
        <ReportError message="Case information could not be loaded." onRetry={() => void caseQuery.refetch()} />
      )}
      {reportQuery.isPending && <p className="case-report__state" role="status">Loading report…</p>}
      {reportQuery.isError && !caseReportNotFound && (
        <ReportError message="Report could not be loaded." onRetry={() => void reportQuery.refetch()} />
      )}
      {caseReportNotFound && (
        <div className="case-report__empty card">
          <h2>No report generated yet</h2>
          <p>Generate a report for this case to review the latest snapshot and export it as a PDF.</p>
        </div>
      )}

      {report && caseData && (
        <div className="case-report__document">
          <header className="case-report__document-header">
            <div>
              <p className="case-report__eyebrow">{caseData.case_id} · Version {report.version}</p>
              <h1>{caseData.title}</h1>
              <p className="case-report__meta">Generated {formatDateTime(report.generated_at)}</p>
            </div>
            <div className="case-report__document-badges">
              <Badge variant={report.simulated ? 'SIMULATED' : 'LIVE'}>{report.simulated ? 'SIMULATED' : 'LIVE'}</Badge>
              <Badge variant={report.data.kpi.level}>{report.data.kpi.level}</Badge>
            </div>
          </header>

          <ReportSection title="Executive summary">
            <MarkdownContent markdown={narrativeBody(narrative, 'Executive summary') || report.narrative_md} />
            <p className="case-report__summary-status">Case status: {report.data.kpi.status.replaceAll('_', ' ')}</p>
          </ReportSection>

          {escalationNarrative && (
            <ReportSection title="Escalation">
              <MarkdownContent markdown={escalationNarrative} />
            </ReportSection>
          )}

          <ReportSection title="What happened">
            {narrativeBody(narrative, 'What happened') && <MarkdownContent markdown={narrativeBody(narrative, 'What happened')} />}
            <p>{caseData.signal.summary}</p>
            <dl className="case-report__facts">
              <div><dt>Risk type</dt><dd>{caseData.risk_type.replaceAll('_', ' ')}</dd></div>
              <div><dt>Scope</dt><dd>{caseData.scope}</dd></div>
              <div><dt>Location</dt><dd>{caseData.location}, {caseData.country}</dd></div>
              <div><dt>Signal</dt><dd>{caseData.signal.signal_id}{caseData.signal.source_label ? ` · ${caseData.signal.source_label}` : ''}</dd></div>
            </dl>
          </ReportSection>

          <ReportSection title="Impact">
            {narrativeBody(narrative, 'Impact') && <MarkdownContent markdown={narrativeBody(narrative, 'Impact')} />}
            <div className="case-report__kpis">
              <div className="case-report__kpi"><span>Units at risk</span><strong>{report.data.kpi.units_at_risk.toLocaleString('en-US')}</strong></div>
              <div className="case-report__kpi"><span>Delayed output value</span><strong title={formatIDR(report.data.kpi.delayed_output_value)}>{formatIDRShort(report.data.kpi.delayed_output_value)}</strong></div>
              <div className="case-report__kpi"><span>Affected PO value</span><strong title={formatIDR(report.data.kpi.affected_po_value)}>{formatIDRShort(report.data.kpi.affected_po_value)}</strong></div>
              <div className="case-report__kpi"><span>Revenue at risk</span><strong title={formatIDR(report.data.kpi.revenue_at_risk)}>{formatIDRShort(report.data.kpi.revenue_at_risk)}</strong></div>
              <div className="case-report__kpi"><span>Priority score</span><strong>{formatScore(report.data.kpi.priority_score)}</strong></div>
            </div>
            <div className="case-report__table-scroll">
              <table className="case-report__table case-report__kpi-table">
                <caption>Impact snapshot values</caption>
                <tbody>
                  <tr><th scope="row">Units at risk</th><td>{report.data.kpi.units_at_risk.toLocaleString('en-US')}</td></tr>
                  <tr><th scope="row">Delayed output value</th><td>{formatIDR(report.data.kpi.delayed_output_value)}</td></tr>
                  <tr><th scope="row">Affected PO value</th><td>{formatIDR(report.data.kpi.affected_po_value)}</td></tr>
                  <tr><th scope="row">Revenue at risk</th><td>{formatIDR(report.data.kpi.revenue_at_risk)}</td></tr>
                  <tr><th scope="row">Priority score</th><td>{formatScore(report.data.kpi.priority_score)}</td></tr>
                </tbody>
              </table>
            </div>
          </ReportSection>

          <ReportSection title="Risk charts">
            <ReportCharts report={report} />
          </ReportSection>

          <ReportSection title="Supplier verification">
            {narrativeBody(narrative, 'Supplier verification') && <MarkdownContent markdown={narrativeBody(narrative, 'Supplier verification')} />}
            <div className="case-report__table-scroll">
              <table className="case-report__table">
                <thead><tr><th scope="col">Supplier</th><th scope="col">Tier</th><th scope="col">K_A</th><th scope="col">K_B</th><th scope="col">RPT status</th><th scope="col">Verification status</th></tr></thead>
                <tbody>
                  {caseData.sites.map((site) => (
                    <tr key={site.site_id}>
                      <th scope="row">{site.site_id} · {site.supplier_name}</th>
                      <td>{site.tier}</td>
                      <td>{site.k_a === null ? '—' : formatScore(site.k_a)}</td>
                      <td>{site.k_b === null ? '—' : formatScore(site.k_b)}</td>
                      <td>{site.rpt_status === 'UNAVAILABLE' ? <Badge variant="NEUTRAL">RPT unavailable</Badge> : site.rpt_status}</td>
                      <td>{site.verification_status.replaceAll('_', ' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3 className="case-report__subheading">Supplier messages</h3>
            {messageQuery?.isPending && <p role="status">Loading supplier messages…</p>}
            {messageQuery?.isError && <p role="alert">Supplier messages could not be loaded.</p>}
            {messageQuery?.data && messageQuery.data.length > 0 ? (
              <div className="case-report__table-scroll">
                <table className="case-report__table">
                  <thead><tr><th scope="col">Site</th><th scope="col">Direction</th><th scope="col">Message</th><th scope="col">Time</th></tr></thead>
                  <tbody>{messageQuery.data.map((message) => (
                    <tr key={message.message_id}>
                      <th scope="row">{message.site_id}</th>
                      <td>{message.direction === 'OUT' ? 'Sent' : 'Received'}{message.simulated && <span className="case-report__table-badge">SIMULATED</span>}</td>
                      <td>{message.body}</td>
                      <td>{formatDateTime(message.created_at)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : !messageQuery?.isPending && <p>No supplier messages are attached to this report snapshot.</p>}
          </ReportSection>

          <ReportSection title="Schedule status">
            {narrativeBody(narrative, 'Schedule status') && <MarkdownContent markdown={narrativeBody(narrative, 'Schedule status')} />}
            {scheduleQuery.isPending && <p role="status">Loading schedule status…</p>}
            {scheduleQuery.isError && <p role="alert">Schedule status could not be loaded.</p>}
            {scheduleQuery.data && relatedScheduleLines.length > 0 ? (
              <div className="case-report__table-scroll">
                <table className="case-report__table">
                  <thead><tr><th scope="col">Purchase order</th><th scope="col">Material</th><th scope="col">Supplier</th><th scope="col">Schedule status</th><th scope="col">Confirmed date</th><th scope="col">Slip days</th><th scope="col">Case ETA</th></tr></thead>
                  <tbody>{relatedScheduleLines.map((line) => {
                    const key = `${line.po_id}/${line.item_no}`;
                    const impactLine = caseImpactByPo.get(key);
                    return (
                      <tr key={key}>
                        <th scope="row">{formatPoLine(line.po_id, line.item_no)}</th>
                        <td>{line.material_id} · {line.material_name}</td>
                        <td>{line.supplier_id} · {line.supplier_name}</td>
                        <td>{line.status}</td>
                        <td><CaseDate value={line.confirmed_date} caseData={caseData} /></td>
                        <td>{line.slip_days.toLocaleString('en-US')}</td>
                        <td>{impactLine ? <CaseDate value={impactLine.new_eta} caseData={caseData} /> : '—'}</td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              </div>
            ) : scheduleQuery.data && <p>No case-linked purchase order lines are included in the current schedule snapshot.</p>}
          </ReportSection>

          <ReportSection title="Recommended plan">
            {narrativeBody(narrative, 'Recommended plan') && <MarkdownContent markdown={narrativeBody(narrative, 'Recommended plan')} />}
            <OptionSummary report={report} />
          </ReportSection>

          <ReportSection title="Action status">
            {narrativeBody(narrative, 'Action status') && <MarkdownContent markdown={narrativeBody(narrative, 'Action status')} />}
            {report.data.actions.length > 0 ? (
              <ActionStatusTable actions={report.data.actions} caseData={caseData} />
            ) : (
              <ProposedActions option={latestOption} caseData={caseData} />
            )}
          </ReportSection>

          <ReportSection title="Appendix: sources and assumptions">
            {narrativeBody(narrative, 'Appendix: sources and assumptions') && <MarkdownContent markdown={narrativeBody(narrative, 'Appendix: sources and assumptions')} />}
            {narrativeBody(narrative, 'Appendix') && <MarkdownContent markdown={narrativeBody(narrative, 'Appendix')} />}
            <p>Charts and KPI values are shown from report snapshot version {report.version}; chart values come from the API snapshot data.</p>
            {caseData.signal.source_urls.length > 0 ? (
              <ul className="case-report__source-list">
                {caseData.signal.source_urls.map((source, index) => {
                  const href = sourceHref(source);
                  return <li key={`${index}-${source}`}>{href ? <a href={href} target="_blank" rel="noreferrer">{href}</a> : source}</li>;
                })}
              </ul>
            ) : <p>No source URLs are attached to this case signal.</p>}
          </ReportSection>

          {narrative.filter((section) => !['executive summary', 'escalation', 'what happened', 'impact', 'supplier verification', 'schedule status', 'recommended plan', 'action status', 'appendix', 'appendix: sources and assumptions'].includes(section.title.trim().toLowerCase())).map((section) => (
            <ReportSection key={section.title} title={section.title}><MarkdownContent markdown={section.body} /></ReportSection>
          ))}
        </div>
      )}
    </article>
  );
}
