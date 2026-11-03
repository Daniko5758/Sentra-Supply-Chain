import { Link } from 'react-router-dom';
import { useDashboard, useSystemStatus } from '../api/hooks';
import { useAuth } from '../auth/AuthContext';
import type { SystemStatus } from '../api/types';
import sentraMark from '../assets/sentra-mark.png';
import { ROLE_LABELS } from '../lib/labels';
import { ApplicationModeControl } from './ApplicationModeControl';
import { Button } from './Button';
import { Icon } from './Icon';

type OverallStatus = { tone: 'ok' | 'failed' | 'unknown'; text: string };

/** Ringkasan status untuk indikator header; sumber data EP-26 (sama dengan halaman System status). */
export function summarizeStatus(status: SystemStatus | undefined): OverallStatus {
  if (!status || status.integrations.length === 0) {
    return { tone: 'unknown', text: 'System status unknown' };
  }
  const relevantIntegrations = status.integrations;
  const failed = relevantIntegrations.filter((i) => i.status === 'FAILED');
  if (failed.length > 0) {
    return {
      tone: 'failed',
      text: failed.length === 1 ? `${failed[0].name} failed` : `${failed.length} integrations failed`,
    };
  }
  if (relevantIntegrations.some((i) => i.status === 'UNKNOWN')) {
    return { tone: 'unknown', text: 'Some checks pending' };
  }
  return { tone: 'ok', text: 'All systems operational' };
}

/** Simbol logo SENTRA (src/assets/sentra-mark.png, dipotong dari logo resmi tim). */
function LogoMark() {
  return <img className="logo-mark" src={sentraMark} width={32} height={32} alt="" aria-hidden="true" />;
}

export function Header() {
  const { me, signOut, isSigningOut } = useAuth();
  const { data: status } = useSystemStatus();
  const { data: dashboard } = useDashboard();
  const overall = summarizeStatus(status);
  const unread = dashboard?.unread_alerts ?? 0;

  return (
    <header className="app-header">
      <Link to="/dashboard" className="app-header__brand">
        <LogoMark />
        <span className="app-header__logo">SENTRA</span>
        <span className="app-header__tagline">Supply Chain Risk &amp; Recovery Agent</span>
      </Link>

      {me?.seed.dataset === 'TEST' && (
        <span className="dataset-badge" data-testid="dataset-badge" aria-label="Test dataset">
          TEST DATA
        </span>
      )}

      <div className="app-header__spacer" />

      {me && <ApplicationModeControl compact />}

      <Link to="/system-status" className="status-indicator" title="Open System status">
        <span
          className={`status-dot status-dot--${overall.tone}${overall.tone === 'ok' ? ' status-dot--live' : ''}`}
          aria-hidden="true"
        />
        <span>{overall.text}</span>
      </Link>

      <Link
        to="/dashboard"
        className={`alert-bell${unread > 0 ? ' alert-bell--unread' : ''}`}
        aria-label={`Alerts: ${unread} unread`}
        title="Alerts"
      >
        <Icon name="bell" size={19} className="alert-bell__icon" />
        {unread > 0 && <span className="alert-bell__count">{unread}</span>}
      </Link>

      {me && (
        <div className="app-header__user">
          <span className="avatar" aria-hidden="true">
            {me.username.slice(0, 2).toUpperCase()}
          </span>
          <span className="app-header__who">
            <span className="app-header__username">{me.username}</span>
            <span className="app-header__role">{ROLE_LABELS[me.role]}</span>
          </span>
        </div>
      )}

      {me && (
        <Button
          className="app-header__logout"
          variant="ghost"
          loading={isSigningOut}
          onClick={() => void signOut()}
        >
          Sign out
        </Button>
      )}
    </header>
  );
}
