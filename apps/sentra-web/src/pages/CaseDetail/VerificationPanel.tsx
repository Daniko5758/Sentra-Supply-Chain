import type { CaseDetail, CaseSite } from '../../api/types';
import { Badge } from '../../components/Badge';
import { requestStatusLabel, requestTypeLabel } from '../../lib/c42';
import { formatPercent, formatScore } from '../../lib/format';
import './verification.css';

function valueOrDash(value: number | null): string {
  return value === null ? '—' : formatScore(value);
}

function probabilityOrDash(value: number | null): string {
  return value === null ? '—' : formatPercent(value * 100);
}

function statusLabel(value: CaseSite['verification_status']): string {
  if (value === 'NO_RESPONSE') return 'No response';
  return value
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

// NO_RESPONSE: the request was sent but nothing came back before it was due; the analysis keeps the formula values (R-13 v2).
const NO_REPLY_NOTE = 'The supplier did not reply in time, so the analysis keeps the formula values.';

function triggerReason(site: CaseSite): string {
  const status = site.verification_status;
  if (status === 'REQUESTED' || status === 'CONFIRMED_BY_SUPPLIER' || status === 'NO_RESPONSE') {
    const triggers: string[] = [];
    if (site.k_a !== null && site.k_a < 50) {
      triggers.push(`K_A (${site.site_id}) = ${formatScore(site.k_a)} < 50`);
    }
    if (site.tier === '1' && site.k_b !== null && site.k_b < 50) {
      triggers.push(`K_B (${site.site_id}) = ${formatScore(site.k_b)} < 50`);
    }
    const noReply = status === 'NO_RESPONSE' ? ` ${NO_REPLY_NOTE}` : '';
    if (triggers.length) {
      return `Supplier confirmation was triggered because ${triggers.join(' and ')}.${noReply}`;
    }
    if (status === 'CONFIRMED_BY_SUPPLIER') return 'A supplier reply was recorded for this site.';
    return `Supplier confirmation was requested by the agent.${noReply}`;
  }

  return 'No supplier confirmation was requested for this site.';
}

function ConfidenceMetric({
  name,
  formula,
  probability,
  result,
}: {
  name: string;
  formula: number | null;
  probability: number | null;
  result: number | null;
}) {
  return (
    <section className="verification-metric" aria-label={`${name} confidence`}>
      <h4>{name}</h4>
      <dl>
        <div><dt>Formula</dt><dd>{valueOrDash(formula)}</dd></div>
        <div><dt>P from RPT</dt><dd>{probabilityOrDash(probability)}</dd></div>
        <div><dt>Result</dt><dd>{valueOrDash(result)}</dd></div>
      </dl>
    </section>
  );
}

function SiteVerification({ site }: { site: CaseSite }) {
  return (
    <article className="verification-site card">
      <header className="verification-site__header">
        <div>
          <h3>{site.supplier_name}</h3>
          <p><span className="mono">{site.site_id}</span><span>Tier {site.tier}</span></p>
        </div>
        <div className="verification-site__badges">
          <Badge variant="NEUTRAL">{site.rpt_status === 'UNAVAILABLE' ? 'RPT unavailable' : 'RPT available'}</Badge>
          {site.verification_status === 'NO_RESPONSE' ? (
            <span className="verification-request-status verification-request-status--no-response">{statusLabel(site.verification_status)}</span>
          ) : <Badge variant="NEUTRAL">{statusLabel(site.verification_status)}</Badge>}
          {site.excluded && <Badge variant="NEUTRAL">Excluded from impact</Badge>}
        </div>
      </header>

      <div className="verification-site__metrics">
        <ConfidenceMetric name="K_A · Affected" formula={site.k_a_formula} probability={site.p_rpt_a} result={site.k_a} />
        {site.tier === '1' && (
          <ConfidenceMetric name="K_B · On time" formula={site.k_b_formula} probability={site.p_rpt_b} result={site.k_b} />
        )}
      </div>

      <div className="verification-site__reason">
        <span>Trigger reason</span>
        <p>{triggerReason(site)}</p>
      </div>
      <section className="verification-site__requests" aria-labelledby={`verification-requests-${site.site_id}`}>
        <h4 id={`verification-requests-${site.site_id}`}>Requests</h4>
        {site.requests.length ? (
          <ul>
            {site.requests.map((request) => (
              <li key={request.request_id}>
                <span>{requestTypeLabel(request.request_type)}</span>
                <span className={`verification-request-status${request.status === 'NO_RESPONSE' ? ' verification-request-status--no-response' : ''}`}>
                  {requestStatusLabel(request)}
                </span>
              </li>
            ))}
          </ul>
        ) : <p>Not requested yet</p>}
      </section>
      {site.backup_declined && <p className="verification-site__backup-declined">Backup declined</p>}
      {site.rpt_status === 'UNAVAILABLE' && (
        <p className="verification-site__notice">RPT did not return a prediction. The API values above remain the source of truth.</p>
      )}
    </article>
  );
}

export function VerificationPanel({ data }: { data: CaseDetail }) {
  return (
    <div className="case-detail__content verification-panel">
      <section className="case-detail__panel card" aria-labelledby="verification-title">
        <header className="case-detail__panel-header">
          <div>
            <h3 id="verification-title">Supplier verification</h3>
            <p>Confidence scores, RPT predictions, and supplier confirmation status for candidate sites.</p>
          </div>
        </header>
      </section>

      {data.sites.length ? data.sites.map((site) => <SiteVerification key={site.site_id} site={site} />) : (
        <section className="verification-empty card">No candidate sites require verification for this case.</section>
      )}
    </div>
  );
}
