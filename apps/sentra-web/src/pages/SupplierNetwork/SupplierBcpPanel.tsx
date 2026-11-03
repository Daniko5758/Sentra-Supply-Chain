import type { BusinessContinuityPlan, SupplierBackup } from '../../api/types';
import { bcpBandLabel } from '../../lib/c42';
import { formatScore } from '../../lib/format';

const BCP_CATEGORIES = [
  { key: 'governance', label: 'Governance & documentation' },
  { key: 'resilience', label: 'Operational resilience & recovery' },
  { key: 'supply_chain', label: 'Supply chain & logistics' },
  { key: 'testing', label: 'Testing & exercises' },
] as const;

export function SupplierBcpPanel({ bcp }: { bcp: BusinessContinuityPlan }) {
  return (
    <section className="network-profile__section network-profile__bcp" aria-labelledby="supplier-bcp-title">
      <div className="network-profile__bcp-heading">
        <h3 id="supplier-bcp-title">Business continuity plan</h3>
        <div className="network-profile__bcp-score">
          <span>BCP score</span>
          <strong>{bcp.score}</strong>
          <span className={`network-profile__bcp-band network-profile__bcp-band--${bcp.band.toLowerCase().replace('_risk', '')}`}>{bcpBandLabel(bcp.band)}</span>
        </div>
      </div>
      <ul className="network-profile__bcp-categories">
        {BCP_CATEGORIES.map(({ key, label }) => {
          const value = bcp.categories[key];
          return (
            <li key={key}>
              <span className="network-profile__bcp-category">{label}</span>
              <div
                className="network-profile__bcp-meter"
                role="meter"
                aria-label={label}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={value}
              >
                <span className="network-profile__bcp-track" aria-hidden="true">
                  <span style={{ width: `${value}%` }} />
                </span>
                <strong>{formatScore(value)}</strong>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="network-profile__bcp-rto">Stated RTO: {bcp.stated_rto_days === null ? 'Not stated' : `${bcp.stated_rto_days} days`}</p>
      <p className="network-profile__muted">Supplier BCP readiness score — questionnaire mapped to ISO 22301 clauses and ISO/TS 22318.</p>
    </section>
  );
}

export function SupplierBackupPanel({ backup }: { backup: SupplierBackup }) {
  if (!backup.site) return null;

  return (
    <section className="network-profile__backup" aria-labelledby="supplier-backup-title">
      <h3 id="supplier-backup-title">Backup site</h3>
      <strong>{backup.site}</strong>
      <dl>
        {backup.ramp_days !== null && <div><dt>Ramp-up</dt><dd>{backup.ramp_days} days</dd></div>}
        {backup.surcharge_pct !== null && <div><dt>Surcharge</dt><dd>{formatScore(backup.surcharge_pct)}%</dd></div>}
      </dl>
    </section>
  );
}
