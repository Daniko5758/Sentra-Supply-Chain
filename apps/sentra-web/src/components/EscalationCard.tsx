import type { Escalation, ParticipantRole, Role } from '../api/types';
import { Badge } from './Badge';
import { formatIDR, formatPercent } from '../lib/format';
import { ROLE_LABELS } from '../lib/labels';
import './EscalationCard.css';

const TIER_LABELS: Record<Escalation['tier'], string> = {
  0: 'Tier 0 — Handled in app',
  1: 'Tier 1 — Meeting Room',
  2: 'Tier 2 — Executive Meeting Room',
};

function participantLabel(role: ParticipantRole): string {
  return ROLE_LABELS[role as Role] ?? role;
}

export function EscalationCard({ escalation, compact = false }: { escalation: Escalation; compact?: boolean }) {
  return (
    <section className={`escalation-card card${compact ? ' escalation-card--compact' : ''}`} aria-labelledby="escalation-card-title">
      <header className="escalation-card__header">
        <div>
          <p className="escalation-card__eyebrow">Case escalation</p>
          <h2 id="escalation-card-title">Escalation</h2>
        </div>
        <span className={`escalation-card__tier escalation-card__tier--${escalation.tier}`}>
          {TIER_LABELS[escalation.tier]}
        </span>
      </header>

      <div className="escalation-card__sections">
        <section aria-label="Why">
          <h3>Why</h3>
          {escalation.reasons.length ? (
            <ul>{escalation.reasons.map((reason, index) => <li key={`${reason.code}-${index}`}>{reason.text}</li>)}</ul>
          ) : <p>No escalation reason was returned.</p>}
        </section>

        <section aria-label="Participants">
          <h3>Participants</h3>
          {escalation.participants.length ? (
            <ul>{escalation.participants.map((role) => <li key={role}>{participantLabel(role)}</li>)}</ul>
          ) : <p>No participants listed.</p>}
        </section>

        <section aria-label="Required approvers">
          <h3>Required approvers</h3>
          {escalation.required_approvers.length ? (
            <ul>{escalation.required_approvers.map((role) => <li key={role}>{participantLabel(role)}</li>)}</ul>
          ) : <p>None</p>}
        </section>

        <section className="escalation-card__policy" aria-label="Company policy (SIMULATED)">
          <h3>Company policy (SIMULATED)</h3>
          <Badge variant="SIMULATED">SIMULATED</Badge>
          <dl>
            <div><dt>SCM approval limit</dt><dd>{formatIDR(escalation.limits.scm_approval_limit)}</dd></div>
            <div><dt>Tariff escalation duty</dt><dd>{formatIDR(escalation.limits.tariff_escalation_duty)}</dd></div>
            <div><dt>Close-call band</dt><dd>{formatPercent(escalation.limits.close_call_band_pp)}</dd></div>
            <div><dt>Premium maximum</dt><dd>{formatPercent(escalation.limits.premium_max_pct)}</dd></div>
          </dl>
        </section>
      </div>
    </section>
  );
}
