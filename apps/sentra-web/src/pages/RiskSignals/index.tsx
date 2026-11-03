import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useAuth } from '../../auth/AuthContext';
import { useAgentRun, useRunLiveScan, useSignals } from '../../api/hooks';
import type { AgentRun, ScanExternalRunData, Signal } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatDate, formatDateTime } from '../../lib/format';
import './risk-signals.css';

const countryNames = new Intl.DisplayNames(['en'], { type: 'region' });

function riskTypeLabel(riskType: Signal['risk_type']): string {
  return riskType.toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function countryLabel(countryCode: string): string {
  return countryNames.of(countryCode) ?? countryCode;
}

function signalLocation(signal: Signal): string {
  const country = countryLabel(signal.country);
  return signal.scope === 'LOCATION'
    ? [signal.kabupaten, country].filter(Boolean).join(', ')
    : country;
}

function sourceDetails(value: string): { href: string; host: string } | null {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:'
      ? { href: url.href, host: url.hostname }
      : null;
  } catch {
    return null;
  }
}

function signalEngineLabel(engine: Signal['engine']): string {
  switch (engine) {
    case 'BRAVE': return 'Brave Search';
    case 'SONAR': return 'SAP AI Core sonar';
    case 'CURRENTS': return 'Currents';
    case 'GDELT': return 'GDELT';
    case 'DEMO': return 'SENTRA demo scenario (SIMULATED)';
  }
}

function formattedPublishedDate(value: string): string {
  try {
    return formatDate(value);
  } catch {
    return value;
  }
}

function signalSources(signal: Signal) {
  if (signal.simulated) {
    return <span>{signal.source_label ?? 'SENTRA demo scenario (SIMULATED)'}</span>;
  }

  if (signal.source_urls.length === 0) {
    return <span>No source URL available</span>;
  }

  return (
    <div className="risk-signal__sources">
      {signal.source_urls.map((url, index) => {
        const source = sourceDetails(url);
        return source ? (
          <a href={source.href} key={`${signal.signal_id}-${url}`} rel="noreferrer" target="_blank">
            {source.host}
          </a>
        ) : (
          <span key={`${signal.signal_id}-${url}`}>Source {index + 1}</span>
        );
      })}
    </div>
  );
}

function SignalEvidenceList({ signal }: { signal: Signal }) {
  if (signal.simulated || signal.evidence.length === 0) return null;

  const allDatesVerified = signal.evidence.every((item) => Boolean(item.published_at));

  return (
    <section className="risk-signal-card__evidence" aria-label="Signal evidence">
      <h3>Evidence</h3>
      <ul>
        {signal.evidence.map((item, index) => {
          const source = sourceDetails(item.url);
          return (
            <li key={`${signal.signal_id}-evidence-${item.url}-${index}`}>
              {source ? (
                <a href={source.href} rel="noreferrer" target="_blank">{source.host}</a>
              ) : (
                <span>Source {index + 1}</span>
              )}
              {item.published_at && (
                <time dateTime={item.published_at}>{formattedPublishedDate(item.published_at)}</time>
              )}
            </li>
          );
        })}
      </ul>
      {allDatesVerified && <p>Publication dates verified by SENTRA</p>}
    </section>
  );
}

function isActiveRun(status: AgentRun['status'] | undefined): boolean {
  return status === 'QUEUED' || status === 'RUNNING';
}

function scanErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The scan could not be started. Try again.';
}

function SignalCard({ signal }: { signal: Signal }) {
  return (
    <article className="risk-signal-card">
      <div className="risk-signal-card__heading">
        <div className="risk-signal-card__badges">
          <Badge variant={signal.simulated ? 'SIMULATED' : 'LIVE'} />
          <span className="risk-signal-card__type">{riskTypeLabel(signal.risk_type)}</span>
          <span className="risk-signal-card__scope">{signal.scope === 'LOCATION' ? 'Location' : 'Country'}</span>
        </div>
        {signal.match === 'NO_SUPPLIER_MATCH' && (
          <span className="risk-signal-card__no-match">No supplier match</span>
        )}
      </div>

      <p className="risk-signal-card__summary">{signal.summary}</p>

      <dl className="risk-signal-card__details">
        <div>
          <dt>Location / country</dt>
          <dd>{signalLocation(signal)}</dd>
        </div>
        <div>
          <dt>Severity</dt>
          <dd>{signal.severity === null ? 'n/a' : signal.severity}</dd>
        </div>
        <div>
          <dt>Reported</dt>
          <dd><time dateTime={signal.fetched_at}>{formatDateTime(signal.fetched_at)}</time></dd>
        </div>
        <div>
          <dt>Related case</dt>
          <dd>
            {signal.case_id
              ? <Link to={`/cases/${encodeURIComponent(signal.case_id)}`}>{signal.case_id}</Link>
              : 'No related case'}
          </dd>
        </div>
      </dl>

      <footer className="risk-signal-card__footer">
        <span className="risk-signal-card__source-label">Source</span>
        {!signal.simulated && (
          <span className="risk-signal-card__engine">{signalEngineLabel(signal.engine)}</span>
        )}
        {signalSources(signal)}
      </footer>
      <SignalEvidenceList signal={signal} />
    </article>
  );
}

function ScanRunDetails({ data }: { data: ScanExternalRunData }) {
  const evidenceStats = data.evidence_stats;
  const skippedBudget = evidenceStats?.skipped_budget ?? 0;

  return (
    <div className="risk-scan-details">
      {data.signals_saved === 0 && (
        <p className="risk-scan-details__neutral">No new verified disruptions found.</p>
      )}

      {Boolean(data.unverified?.length) && (
        <section className="risk-scan-details__unverified" aria-label="Unverified (not saved)">
          <h3>Unverified (not saved)</h3>
          <ul>
            {data.unverified?.map((item, index) => (
              <li key={`${item.risk_type}-${item.location}-${index}`}>
                <p><strong>{item.risk_type}</strong> · {item.location}</p>
                {item.urls.length > 0 && (
                  <ul>
                    {item.urls.map((url, urlIndex) => {
                      const source = sourceDetails(url);
                      return (
                        <li key={`${url}-${urlIndex}`}>
                          {source ? (
                            <a href={source.href} rel="noreferrer" target="_blank">{url}</a>
                          ) : (
                            <span>{url}</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
                <p className="risk-scan-details__reason">{item.reason}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {evidenceStats && (
        <details className="risk-scan-details__evidence" open>
          <summary>Evidence checks</summary>
          <dl>
            {evidenceStats.candidates !== undefined && <div><dt>Candidates</dt><dd>{evidenceStats.candidates}</dd></div>}
            {evidenceStats.screened !== undefined && <div><dt>Screened</dt><dd>{evidenceStats.screened}</dd></div>}
            {evidenceStats.in_window !== undefined && <div><dt>Within date window</dt><dd>{evidenceStats.in_window}</dd></div>}
            {evidenceStats.stale !== undefined && <div><dt>Stale</dt><dd>{evidenceStats.stale}</dd></div>}
            {evidenceStats.not_found !== undefined && <div><dt>Not found</dt><dd>{evidenceStats.not_found}</dd></div>}
            {evidenceStats.fetch_failed !== undefined && <div><dt>Fetch failed</dt><dd>{evidenceStats.fetch_failed}</dd></div>}
          </dl>
          {skippedBudget > 0 && <p>{skippedBudget} articles not checked (page budget)</p>}
        </details>
      )}

      {data.skipped_targets?.map((item, index) => (
        <p className="risk-scan-details__skipped" key={`${item.source}-${item.target}-${index}`}>
          Not searched this run: {item.target} ({item.reason})
        </p>
      ))}
    </div>
  );
}

function ScanRunStatus({ run }: { run: AgentRun }) {
  const isFailure = run.status === 'ERROR' || run.status === 'AGENT_TIMEOUT' || run.status === 'SKIPPED';
  const statusClass = isFailure ? 'risk-scan-status--error' : run.status === 'OK' ? 'risk-scan-status--success' : 'risk-scan-status--progress';
  const message = run.status === 'QUEUED'
    ? 'Scan queued. Waiting for the agent…'
    : run.status === 'RUNNING'
      ? 'Scan is running…'
      : run.status === 'OK'
        ? run.summary ?? 'Scan completed.'
        : run.error ?? (run.status === 'AGENT_TIMEOUT' ? 'The scan timed out.' : 'The scan did not complete.');

  return (
    <div className={`risk-scan-status ${statusClass}`} role="status" aria-live="polite">
      <span className="risk-scan-status__label">{run.status.replace(/_/g, ' ')}</span>
      <span>{message}</span>
      {run.status === 'OK' && run.task === 'scan_external' && run.data && (
        <ScanRunDetails data={run.data} />
      )}
    </div>
  );
}

export function RiskSignalsPage() {
  const { me } = useAuth();
  const signals = useSignals();
  const scan = useRunLiveScan();
  const [runId, setRunId] = useState<string | null>(null);
  const run = useAgentRun(runId);
  const queryClient = useQueryClient();
  const refreshedRunId = useRef<string | null>(null);

  const allowedRole = me?.role === 'BUYER' || me?.role === 'SCM_MANAGER';
  const canScan = allowedRole && me?.mode === 'LIVE';
  const scanInProgress = scan.isPending || (Boolean(runId) && !run.data && !run.isError) || isActiveRun(run.data?.status);

  useEffect(() => {
    if (!runId || !run.data || isActiveRun(run.data.status) || refreshedRunId.current === runId) {
      return;
    }
    refreshedRunId.current = runId;
    void queryClient.invalidateQueries({ queryKey: ['signals'] });
  }, [queryClient, run.data?.status, runId]);

  function startScan() {
    scan.reset();
    setRunId(null);
    scan.mutate(undefined, {
      onSuccess: (response) => setRunId(response.run_id),
    });
  }

  return (
    <section className="risk-signals-page">
      <div className="page-header"><h1>Risk signals</h1></div>
      <p className="page-description">External risk signals matched against supplier locations and countries.</p>

      <section className="risk-scan-panel card" aria-labelledby="risk-scan-title">
        <div>
          <h2 id="risk-scan-title">Live source scan</h2>
          <p>Search external sources for recent supply chain risks.</p>
        </div>
        {canScan ? (
          <Button onClick={startScan} loading={scanInProgress} disabled={scanInProgress}>
            {scanInProgress ? 'Scanning' : 'Scan now'}
          </Button>
        ) : (
          <p className="risk-scan-panel__note">
            {allowedRole
              ? 'Switch the application to LIVE mode to run a scan.'
              : 'Live scans are available to Buyer and SCM Manager in LIVE mode.'}
          </p>
        )}
        {scan.isError && <p className="risk-scan-panel__error" role="alert">{scanErrorMessage(scan.error)}</p>}
        {runId && run.isPending && !run.data && (
          <div className="risk-scan-status" role="status" aria-live="polite">Scan queued. Waiting for the agent…</div>
        )}
        {runId && run.isError && (
          <div className="risk-scan-status risk-scan-status--error" role="alert">
            <span>Run status could not be loaded.</span>
            <Button variant="secondary" onClick={() => void run.refetch()}>Try again</Button>
          </div>
        )}
        {run.data && <ScanRunStatus run={run.data} />}
      </section>

      <section className="risk-signals-results" aria-labelledby="risk-signals-list-title" aria-busy={signals.isPending}>
        <header className="risk-signals-results__heading">
          <div>
            <h2 id="risk-signals-list-title">Latest signals</h2>
            <p>Newest signals appear first.</p>
          </div>
          <Button variant="secondary" onClick={() => void signals.refetch()} disabled={signals.isFetching}>
            Refresh list
          </Button>
        </header>

        {signals.isPending && <p className="risk-signals-state" role="status">Loading risk signals…</p>}
        {signals.isError && (
          <div className="risk-signals-state risk-signals-state--error" role="alert">
            <p>Risk signals could not be loaded.</p>
            <Button variant="secondary" onClick={() => void signals.refetch()}>Try again</Button>
          </div>
        )}
        {signals.data && signals.data.length === 0 && (
          <p className="risk-signals-state">No risk signals found.</p>
        )}
        {signals.data && signals.data.length > 0 && (
          <div className="risk-signal-list">
            {signals.data.map((signal) => <SignalCard key={signal.signal_id} signal={signal} />)}
          </div>
        )}
      </section>
    </section>
  );
}
