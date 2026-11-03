import { useAuth } from '../../auth/AuthContext';
import { useRunSystemChecks, useSystemStatus } from '../../api/hooks';
import type { IntegrationStatus, IntegrationStatusValue, SchedulerStatus, SystemStatus } from '../../api/types';
import { ApplicationModeControl } from '../../components/ApplicationModeControl';
import { Button } from '../../components/Button';
import { formatDateTime } from '../../lib/format';
import './system-status.css';

const INTEGRATION_LABELS: Record<IntegrationStatus['name'], string> = {
  'claude-4.6-sonnet': 'Claude 4.6 Sonnet',
  'sap-rpt-1.6': 'SAP RPT-1.6',
  sonar: 'sonar',
  brave: 'Brave Search API',
  agentcore: 'AWS AgentCore',
  zoom: 'Zoom',
  gmail: 'Gmail',
};

const FEATURE_FLAGS: Array<{ key: keyof SystemStatus['feature_flags']; label: string }> = [
  { key: 'erp_execution', label: 'ERP execution' },
  { key: 'meeting_room', label: 'Meeting room' },
  { key: 'whatif', label: 'What-if analysis' },
];

function safeDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  try {
    return formatDateTime(value);
  } catch {
    return value;
  }
}

function formatRun(value: unknown): string {
  if (value === null || value === undefined) return 'No runs yet';
  if (typeof value === 'string') return safeDateTime(value);
  if (typeof value !== 'object') return String(value);

  const fields = value as Record<string, unknown>;
  const details = [
    fields.run_id,
    fields.status,
    typeof fields.started_at === 'string' ? safeDateTime(fields.started_at) : fields.started_at,
    fields.result ?? fields.summary ?? fields.message,
  ]
    .filter((field): field is string | number => typeof field === 'string' || typeof field === 'number')
    .map(String);
  return details.length > 0 ? details.join(' · ') : 'Run recorded';
}

function IntegrationStatusLabel({ status }: { status: IntegrationStatusValue }) {
  return <span className={`integration-status integration-status--${status.toLowerCase()}`}>{status}</span>;
}

function IntegrationRow({ integration }: { integration: IntegrationStatus }) {
  return (
    <div className="integration-row" role="row">
      <div className="integration-row__name" role="cell">
        <strong>{INTEGRATION_LABELS[integration.name]}</strong>
      </div>
      <span role="cell"><IntegrationStatusLabel status={integration.status} /></span>
      <span className="integration-row__detail" role="cell">{safeDateTime(integration.last_call_at)}</span>
      <span className="integration-row__detail" role="cell">{integration.duration_ms === null ? '—' : `${integration.duration_ms} ms`}</span>
      <span className="integration-row__message" role="cell">{integration.message ?? '—'}</span>
    </div>
  );
}

function SchedulerPanel({ scheduler }: { scheduler: SchedulerStatus }) {
  const autoScan = scheduler.auto_scan === 'ON' ? 'ON (LIVE)' : 'OFF (DEMO mode)';
  const dailyCheck = scheduler.daily_check;

  return (
    <section className="system-status__panel" aria-labelledby="scheduler-heading">
      <div className="system-status__section-heading">
        <div>
          <h2 id="scheduler-heading">Scheduler</h2>
          <p>Scheduled risk scans and daily schedule checks.</p>
        </div>
        <span className={`scheduler-state${scheduler.auto_scan === 'ON' ? ' scheduler-state--on' : ''}`}>
          Auto scan: {autoScan}
        </span>
      </div>
      <dl className="scheduler-grid">
        <div><dt>Scan interval</dt><dd>{scheduler.interval_min} minutes</dd></div>
        <div><dt>Last run</dt><dd>{formatRun(scheduler.last_run)}</dd></div>
        <div><dt>Next run</dt><dd>{safeDateTime(scheduler.next_run_at)}</dd></div>
        <div><dt>Daily schedule check</dt><dd>{dailyCheck?.status ?? 'No check recorded'}</dd></div>
        <div><dt>Last daily check</dt><dd>{safeDateTime(dailyCheck?.last_run_at)}</dd></div>
        {dailyCheck?.summary && <div className="scheduler-grid__summary"><dt>Result</dt><dd>{dailyCheck.summary}</dd></div>}
      </dl>
    </section>
  );
}

export function SystemStatusPage() {
  const { isScmManager } = useAuth();
  const { data, isPending, isError, error, refetch } = useSystemStatus();
  const checks = useRunSystemChecks();

  return (
    <section className="system-status">
      <div className="system-status__top">
        <div className="page-header">
          <h1>System status</h1>
        </div>
        <ApplicationModeControl />
      </div>
      <p className="page-description">Integration health, feature flags and scheduler.</p>

      {isPending && <p className="system-status__notice" role="status">Loading system status…</p>}
      {isError && (
        <div className="system-status__notice system-status__notice--error" role="alert">
          <span>{error instanceof Error ? error.message : 'Unable to load system status.'}</span>
          <Button variant="secondary" onClick={() => void refetch()}>Try again</Button>
        </div>
      )}
      {checks.isError && <p className="system-status__notice system-status__notice--error" role="alert">Checks could not be completed. Try again.</p>}

      {data && (
        <>
          <section className="system-status__panel" aria-labelledby="integrations-heading">
            <div className="system-status__section-heading">
              <div>
                <h2 id="integrations-heading">Integrations</h2>
                <p>Latest connection and call information for connected services.</p>
              </div>
              {isScmManager && (
                <Button loading={checks.isPending} onClick={() => checks.mutate()}>
                  Run checks
                </Button>
              )}
            </div>
            {checks.isPending && (
              <p className="system-status__running" role="status" aria-live="polite">
                System checks are running. This can take up to 3 minutes.
              </p>
            )}
            <div className="integration-table" role="table" aria-label="Integration status">
              <div className="integration-row integration-row--header" role="row">
                <span role="columnheader">Integration</span>
                <span role="columnheader">Status</span>
                <span role="columnheader">Last called</span>
                <span role="columnheader">Duration</span>
                <span role="columnheader">Message</span>
              </div>
              {data.integrations.filter((integration) => integration.name in INTEGRATION_LABELS).map((integration) => (
                <IntegrationRow key={integration.name} integration={integration} />
              ))}
            </div>
            <p className="system-status__attribution">Search powered by Brave Search API</p>
          </section>

          <section className="system-status__panel" aria-labelledby="features-heading">
            <div className="system-status__section-heading">
              <div>
                <h2 id="features-heading">Feature flags</h2>
                <p>Optional capabilities configured for this environment.</p>
              </div>
            </div>
            <div className="feature-flag-list">
              {FEATURE_FLAGS.map(({ key, label }) => {
                const enabled = data.feature_flags[key];
                return (
                  <div className="feature-flag" key={key}>
                    <span>{label}</span>
                    <span className={`feature-flag__value${enabled ? ' feature-flag__value--on' : ''}`}>
                      {enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="system-status__seed" aria-label="Active seed">
              <div className="system-status__seed-heading">
                <h3>Active seed</h3>
                {data.seed.dataset === 'TEST' && (
                  <span className="system-status__test-badge" data-testid="system-test-data-badge">TEST DATA</span>
                )}
              </div>
              <dl className="system-status__seed-grid">
                <div><dt>File</dt><dd>{data.seed.file}</dd></div>
                <div><dt>Version</dt><dd>{data.seed.version}</dd></div>
                <div><dt>Dataset</dt><dd>{data.seed.dataset}</dd></div>
              </dl>
            </div>
          </section>

          {data.scheduler && <SchedulerPanel scheduler={data.scheduler} />}
        </>
      )}
    </section>
  );
}
