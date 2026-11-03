import { useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../../api/client';
import {
  useAlerts,
  useCaseDetail,
  useDashboard,
  useHeatmapCountries,
  useInjectDemo,
  useMarkAlertRead,
  useResetDemo,
  useScenarios,
} from '../../api/hooks';
import type { DemoInjectRequest, DemoInjectSignal, RiskScope, RiskType, Scenario } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatDate, formatDateTime, formatIDRShort, formatPercent, formatScore } from '../../lib/format';
import '../../styles/dashboard.css';
import { useScrollReveal } from '../../lib/useScrollReveal';
import { GlobeSection } from './GlobeSection';

type ControlFeedback = { tone: 'success' | 'error'; message: string };

interface CustomSignalDraft {
  risk_type: RiskType;
  scope: RiskScope;
  country: string;
  kabupaten: string;
  severity: number;
  tariff_pp: string;
  category: string;
  downtime_days: string;
  effective_date: string;
}

const RISK_TYPES: RiskType[] = [
  'FLOOD',
  'LANDSLIDE',
  'FIRE',
  'EARTHQUAKE',
  'LOGISTICS',
  'TARIFF',
  'POLITICAL',
];

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : 'The request failed. Please try again.';
}

function displayRiskType(value: RiskType): string {
  return value.toLowerCase().replace(/_/g, ' ');
}

function signalPlace(signal: { scope: RiskScope; kabupaten: string | null; country: string }): string {
  return signal.scope === 'LOCATION' && signal.kabupaten
    ? `${signal.kabupaten} · ${signal.country}`
    : signal.country;
}

function DashboardError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="dashboard-error" role="alert">
      <p>{message}</p>
      <Button variant="secondary" onClick={onRetry}>Try again</Button>
    </div>
  );
}

function LiveAlertSources({ caseId }: { caseId: string }) {
  const caseDetail = useCaseDetail(caseId);
  const sourceUrls = caseDetail.data?.signal.source_urls ?? [];

  if (caseDetail.isLoading) {
    return <span className="dashboard-source-note">Loading verified source links…</span>;
  }
  if (caseDetail.isError) {
    return <Link className="dashboard-source-note" to={`/cases/${encodeURIComponent(caseId)}`}>View case sources</Link>;
  }
  if (sourceUrls.length === 0) {
    return <span className="dashboard-source-note">No source URL available</span>;
  }

  return (
    <span className="dashboard-source-links">
      {sourceUrls.map((url, index) => (
        <a href={url} key={url} rel="noreferrer" target="_blank">
          {sourceUrls.length === 1 ? 'Source' : `Source ${index + 1}`}
        </a>
      ))}
    </span>
  );
}

export function DemoControls({ d0Date }: { d0Date: string }) {
  const resetDemo = useResetDemo();
  const injectDemo = useInjectDemo();
  const scenarios = useScenarios();
  const [feedback, setFeedback] = useState<ControlFeedback | null>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [draft, setDraft] = useState<CustomSignalDraft>({
    risk_type: 'FLOOD',
    scope: 'LOCATION',
    country: '',
    kabupaten: '',
    severity: 1,
    tariff_pp: '',
    category: '',
    downtime_days: '',
    effective_date: d0Date,
  });
  const busy = resetDemo.isPending || injectDemo.isPending;
  // Only the button that started the injection shows the spinner; the others are just disabled.
  const injectingKey = injectDemo.isPending ? injectDemo.variables?.scenario ?? null : null;

  function reset() {
    setFeedback(null);
    setConfirmingReset(false);
    resetDemo.mutate(undefined, {
      onSuccess: (result) => setFeedback({
        tone: 'success',
        message: `Demo data reset. D0 date: ${formatDate(result.d0_date)}.`,
      }),
      onError: (error) => setFeedback({ tone: 'error', message: errorMessage(error) }),
    });
  }

  function injectScenario(scenario: Scenario['key']) {
    setFeedback(null);
    injectDemo.mutate({ scenario }, {
      onSuccess: (result) => setFeedback({
        tone: 'success',
        message: `Scenario queued. Run ${result.run_id}.`,
      }),
      onError: (error) => setFeedback({ tone: 'error', message: errorMessage(error) }),
    });
  }

  function createCustomRequest(): DemoInjectRequest {
    const place = draft.scope === 'LOCATION' ? draft.kabupaten.trim() : draft.country.trim();
    const commonSignal = {
      risk_type: draft.risk_type,
      country: draft.country.trim().toUpperCase(),
      kabupaten: draft.scope === 'LOCATION' ? draft.kabupaten.trim() : null,
      kecamatan: [],
      effective_date: draft.effective_date || null,
      tariff_pp: draft.risk_type === 'TARIFF' && draft.tariff_pp !== '' ? Number(draft.tariff_pp) : null,
      category: draft.risk_type === 'TARIFF' && draft.category.trim() ? draft.category.trim() : null,
      summary: `SIMULATED: ${displayRiskType(draft.risk_type)} scenario for ${place}.`,
      downtime_days: draft.downtime_days === '' ? null : Number(draft.downtime_days),
      source_urls: [],
    };

    const signal: DemoInjectSignal = draft.scope === 'LOCATION'
      ? { ...commonSignal, scope: 'LOCATION', severity: draft.severity }
      : { ...commonSignal, scope: 'COUNTRY', severity: null };

    return { scenario: 'custom', signal };
  }

  function submitCustom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    injectDemo.mutate(createCustomRequest(), {
      onSuccess: (result) => setFeedback({
        tone: 'success',
        message: `Custom scenario queued. Run ${result.run_id}.`,
      }),
      onError: (error) => setFeedback({ tone: 'error', message: errorMessage(error) }),
    });
  }

  return (
    <section className="dashboard-card dashboard-demo" data-reveal aria-labelledby="demo-controls-title">
      <div className="dashboard-card__heading">
        <div>
          <p className="dashboard-eyebrow">Scenario management</p>
          <h2 id="demo-controls-title">Demo controls</h2>
        </div>
        <div className="dashboard-demo__badges">
          {scenarios.data?.seed.dataset === 'TEST' && (
            <Badge variant="NEUTRAL" title="Test dataset — not the demo data">TEST DATA</Badge>
          )}
          <Badge variant="SIMULATED" />
        </div>
      </div>

      {confirmingReset && (
        <div className="dashboard-demo__confirm" role="alertdialog" aria-labelledby="reset-confirm-text">
          <p id="reset-confirm-text">
            Reset demo data deletes all cases, signals, supplier messages and ERP actions, and restores the mock SAP S/4HANA
            data from the seed. Continue?
          </p>
          <div className="dashboard-demo__confirm-actions">
            <Button variant="danger" onClick={reset}>Yes, reset</Button>
            <Button variant="secondary" onClick={() => setConfirmingReset(false)}>Cancel</Button>
          </div>
        </div>
      )}

      <div className="dashboard-demo__actions">
        <Button
          variant="secondary"
          disabled={busy || confirmingReset}
          loading={resetDemo.isPending}
          onClick={() => { setFeedback(null); setConfirmingReset(true); }}
        >
          Reset demo data
        </Button>
        {scenarios.data?.scenarios.map((scenario) => {
          const downtime = scenario.downtime_days === null
            ? 'Downtime: Not specified'
            : `Downtime: ${scenario.downtime_days} days`;
          const tooltip = [
            `Key: ${scenario.key}`,
            `Risk type: ${scenario.risk_type}`,
            `Location: ${scenario.location}`,
            downtime,
            scenario.summary,
          ].join('\n');
          return (
            <Button
              key={scenario.key}
              variant="secondary"
              disabled={busy}
              loading={injectingKey === scenario.key}
              title={tooltip}
              onClick={() => injectScenario(scenario.key)}
            >
              Inject: {displayRiskType(scenario.risk_type)} · {scenario.location}
            </Button>
          );
        })}
        {scenarios.isLoading && <span className="dashboard-demo__scenario-status">Loading scenarios…</span>}
        {scenarios.isError && (
          <span className="dashboard-demo__scenario-status" role="alert">
            Scenarios could not be loaded. <button type="button" onClick={() => void scenarios.refetch()}>Try again</button>
          </span>
        )}
      </div>

      {feedback && (
        <p className={`dashboard-feedback dashboard-feedback--${feedback.tone}`} role={feedback.tone === 'error' ? 'alert' : 'status'}>
          {feedback.message}
        </p>
      )}

      <form className="dashboard-custom-form" onSubmit={submitCustom}>
        <div className="dashboard-custom-form__heading">
          <h3>Inject custom scenario</h3>
          <p>Values are sent to the demo API as a simulated signal.</p>
        </div>
        <div className="dashboard-custom-form__grid">
          <label>
            <span>Risk type</span>
            <select
              value={draft.risk_type}
              onChange={(event) => setDraft((current) => ({ ...current, risk_type: event.target.value as RiskType }))}
            >
              {RISK_TYPES.map((riskType) => <option key={riskType} value={riskType}>{displayRiskType(riskType)}</option>)}
            </select>
          </label>
          <label>
            <span>Scope</span>
            <select
              value={draft.scope}
              onChange={(event) => setDraft((current) => ({ ...current, scope: event.target.value as RiskScope }))}
            >
              <option value="LOCATION">Location</option>
              <option value="COUNTRY">Country</option>
            </select>
          </label>
          <label>
            <span>Country code</span>
            <input
              autoComplete="country"
              maxLength={2}
              pattern="[A-Za-z]{2}"
              placeholder="ISO alpha-2"
              required
              value={draft.country}
              onChange={(event) => setDraft((current) => ({ ...current, country: event.target.value.toUpperCase() }))}
            />
          </label>
          {draft.scope === 'LOCATION' && (
            <label>
              <span>District</span>
              <input
                required
                value={draft.kabupaten}
                onChange={(event) => setDraft((current) => ({ ...current, kabupaten: event.target.value }))}
              />
            </label>
          )}
          {draft.scope === 'LOCATION' && (
            <label>
              <span>Severity</span>
              <input
                type="number"
                min="1"
                max="5"
                step="1"
                required
                value={draft.severity}
                onChange={(event) => setDraft((current) => ({ ...current, severity: Number(event.target.value) }))}
              />
            </label>
          )}
          <label>
            <span>Effective date</span>
            <input
              type="date"
              required
              value={draft.effective_date}
              onChange={(event) => setDraft((current) => ({ ...current, effective_date: event.target.value }))}
            />
          </label>
          <label>
            <span>Tariff percentage points</span>
            <input
              type="number"
              min="0"
              step="0.1"
              value={draft.tariff_pp}
              onChange={(event) => setDraft((current) => ({ ...current, tariff_pp: event.target.value }))}
            />
          </label>
          <label>
            <span>Category</span>
            <input
              value={draft.category}
              onChange={(event) => setDraft((current) => ({ ...current, category: event.target.value }))}
            />
          </label>
          <label>
            <span>Downtime (days)</span>
            <input
              type="number"
              min="1"
              step="1"
              value={draft.downtime_days}
              onChange={(event) => setDraft((current) => ({ ...current, downtime_days: event.target.value }))}
            />
          </label>
        </div>
        <div className="dashboard-custom-form__footer">
          <p>Country-level signals do not use a severity value.</p>
          <Button type="submit" disabled={busy} loading={injectingKey === 'custom'}>Inject custom</Button>
        </div>
      </form>
    </section>
  );
}

export function DashboardPage() {
  const { me, isScmManager } = useAuth();
  const dashboard = useDashboard();
  const alerts = useAlerts();
  // EP-10: opening an alert marks it read, so the bell count goes down.
  const markRead = useMarkAlertRead();
  const heatmap = useHeatmapCountries();
  // Demo injections and reset remain available in both application modes.
  const showDemoControls = Boolean(me && isScmManager);
  const pageRef = useRef<HTMLElement>(null);
  // Kartu dan item muncul saat di-scroll; elemen baru diamati ulang setelah data dimuat.
  useScrollReveal(pageRef, [dashboard.data, alerts.data, heatmap.data, showDemoControls]);
  const latestAlerts = alerts.data?.slice().sort(
    (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at),
  );

  return (
    <section className="dashboard-page" ref={pageRef}>
      <header className="dashboard-page__header">
        <div>
          <p className="dashboard-eyebrow">Supply chain operations</p>
          <h1>Dashboard</h1>
          <p>Current exposure, alerts and schedule signals from SENTRA.</p>
        </div>
        <Link className="dashboard-view-cases" to="/cases">View cases <span aria-hidden="true">→</span></Link>
      </header>

      {dashboard.isError && <DashboardError message={errorMessage(dashboard.error)} onRetry={() => void dashboard.refetch()} />}

      <section className="dashboard-overview" data-reveal="zoom" aria-labelledby="dashboard-overview-title">
        <div className="dashboard-overview__primary">
          <h2 id="dashboard-overview-title" className="dashboard-eyebrow">Exposure overview</h2>
          <span className="dashboard-overview__label">Revenue at risk</span>
          <strong className="dashboard-overview__value">
            {dashboard.data ? formatIDRShort(dashboard.data.kpi.revenue_at_risk_open_cases) : '—'}
          </strong>
          <span className="dashboard-overview__hint">Across all open cases</span>
        </div>
        <div className="dashboard-overview__metrics" aria-label="Case and alert counts">
          <div className="dashboard-overview__metric">
            <span className="dashboard-overview__label">Open cases</span>
            <strong>{dashboard.data ? dashboard.data.kpi.open_cases : '—'}</strong>
            <span className="dashboard-overview__hint">Cases requiring attention</span>
          </div>
          <div className="dashboard-overview__metric">
            <span className="dashboard-overview__label">High alerts</span>
            <strong>{dashboard.data ? dashboard.data.kpi.high_alerts : '—'}</strong>
            <span className="dashboard-overview__hint">Current HIGH priority alerts</span>
          </div>
        </div>
      </section>

      <GlobeSection />

      <div className="dashboard-content-grid">
        <section className="dashboard-card dashboard-alerts" data-reveal="left" aria-labelledby="latest-alerts-title">
          <div className="dashboard-card__heading">
            <div>
              <p className="dashboard-eyebrow">Attention feed</p>
              <h2 id="latest-alerts-title">Latest alerts</h2>
            </div>
            <span className="dashboard-count">{dashboard.data?.unread_alerts ?? '—'} unread</span>
          </div>
          {alerts.isLoading && <p className="dashboard-empty">Loading alerts…</p>}
          {alerts.isError && <DashboardError message={errorMessage(alerts.error)} onRetry={() => void alerts.refetch()} />}
          {latestAlerts?.length === 0 && <p className="dashboard-empty">No alerts to show.</p>}
          <div className="dashboard-alert-list">
            {latestAlerts?.map((alert, index) => (
              <article
                className={`dashboard-alert${alert.read ? '' : ' dashboard-alert--unread'}`}
                key={alert.alert_id}
                data-reveal
                style={{ '--reveal-i': index + 2 } as CSSProperties}
              >
                <div className="dashboard-alert__marker" aria-hidden="true" />
                <div className="dashboard-alert__main">
                  <div className="dashboard-alert__title-row">
                    <Badge variant={alert.level} />
                    <Link
                      to={`/cases/${encodeURIComponent(alert.case_id)}`}
                      className="dashboard-alert__title"
                      onClick={() => { if (!alert.read) markRead.mutate(alert.alert_id); }}
                    >
                      {alert.title}
                    </Link>
                    {!alert.read && <span className="dashboard-unread-label">Unread</span>}
                  </div>
                  <p className="dashboard-alert__meta">
                    <span>{alert.location}</span>
                    <span>Score {formatScore(alert.score)}</span>
                    <span>{alert.units_at_risk} units at risk</span>
                    <span>{formatIDRShort(alert.revenue_at_risk)}</span>
                  </p>
                  <div className="dashboard-alert__footer">
                    <Badge variant={alert.simulated ? 'SIMULATED' : 'LIVE'} />
                    <time dateTime={alert.created_at}>{formatDateTime(alert.created_at)}</time>
                  </div>
                  {!alert.simulated && <LiveAlertSources caseId={alert.case_id} />}
                </div>
              </article>
            ))}
          </div>
          <Link to="/signals" className="dashboard-card__link">Open risk signals <span aria-hidden="true">→</span></Link>
        </section>

        <div className="dashboard-side-column">
          <section className="dashboard-card dashboard-schedule" data-reveal="right" aria-labelledby="schedule-snapshot-title">
            <div className="dashboard-card__heading">
              <div>
                <p className="dashboard-eyebrow">Planning</p>
                <h2 id="schedule-snapshot-title">Schedule snapshot</h2>
              </div>
              <Link to="/schedule" aria-label="Open schedule">View <span aria-hidden="true">→</span></Link>
            </div>
            {dashboard.data ? (
              <div className="dashboard-schedule__stats">
                <div><strong>{dashboard.data.schedule.slipped}</strong><span>Slipped</span></div>
                <div><strong>{dashboard.data.schedule.on_time}</strong><span>On time</span></div>
                <div><strong>{dashboard.data.schedule.orders_short}</strong><span>Orders short</span></div>
              </div>
            ) : <p className="dashboard-empty">Loading schedule summary…</p>}
            <Link to="/schedule" className="dashboard-card__link">Review schedule <span aria-hidden="true">→</span></Link>
          </section>

          <section className="dashboard-card dashboard-heatmap" data-reveal="right" style={{ '--reveal-i': 1 } as CSSProperties} aria-labelledby="heatmap-summary-title">
            <div className="dashboard-card__heading">
              <div>
                <p className="dashboard-eyebrow">Supplier exposure</p>
                <h2 id="heatmap-summary-title">Risk heatmap</h2>
              </div>
              <Link to="/heatmap" aria-label="Open risk heatmap">View <span aria-hidden="true">→</span></Link>
            </div>
            {heatmap.isLoading && <p className="dashboard-empty">Loading country exposure…</p>}
            {heatmap.isError && <DashboardError message={errorMessage(heatmap.error)} onRetry={() => void heatmap.refetch()} />}
            {heatmap.data && (
              <div className="dashboard-country-list">
                {heatmap.data.countries.map((country, index) => (
                  <div className="dashboard-country" key={country.country}>
                    <span className="dashboard-country__name">{country.name}</span>
                    <span className={`dashboard-country__tier dashboard-country__tier--${country.exposure_tier}`}>
                      Tier {country.exposure_tier}
                    </span>
                    <span>{formatPercent(country.share)}</span>
                    <strong>{formatIDRShort(country.po_exposure)}</strong>
                    {/* Lebar batang = share dari API (EP-16); hanya tampilan, tumbuh saat kartu ter-scroll masuk. */}
                    <span className="dashboard-country__bar" aria-hidden="true">
                      <span style={{ width: `${country.share}%`, '--bar-i': index } as CSSProperties} />
                    </span>
                  </div>
                ))}
                {heatmap.data.countries.length === 0 && <p className="dashboard-empty">No country exposure to show.</p>}
              </div>
            )}
            <Link to="/heatmap" className="dashboard-card__link">View full heatmap <span aria-hidden="true">→</span></Link>
          </section>
        </div>

        <section className="dashboard-card dashboard-signals" data-reveal aria-labelledby="recent-signals-title">
          <div className="dashboard-card__heading">
            <div>
              <p className="dashboard-eyebrow">Monitoring</p>
              <h2 id="recent-signals-title">Recent signals</h2>
            </div>
            <Link to="/signals" aria-label="Open risk signals">View all <span aria-hidden="true">→</span></Link>
          </div>
          {dashboard.data?.latest_signals.length === 0 && <p className="dashboard-empty">No recent signals.</p>}
          <div className="dashboard-signal-list">
            {dashboard.data?.latest_signals.slice(0, 5).map((signal, index) => (
              <article
                className="dashboard-signal"
                key={signal.signal_id}
                data-reveal
                style={{ '--reveal-i': index + 1 } as CSSProperties}
              >
                <div className="dashboard-signal__topline">
                  <Badge variant={signal.simulated ? 'SIMULATED' : 'LIVE'} />
                  <span className="dashboard-signal__type">{displayRiskType(signal.risk_type)}</span>
                  <span className="dashboard-signal__scope">{signal.scope}</span>
                </div>
                <p>{signal.summary}</p>
                <div className="dashboard-signal__footer">
                  <span>{signalPlace(signal)}</span>
                  <time dateTime={signal.fetched_at}>{formatDateTime(signal.fetched_at)}</time>
                </div>
                {signal.simulated ? (
                  signal.source_label && <span className="dashboard-source-note">{signal.source_label}</span>
                ) : (
                  <span className="dashboard-source-links">
                    {signal.source_urls.length > 0
                      ? signal.source_urls.map((url, index) => (
                        <a href={url} key={url} rel="noreferrer" target="_blank">
                          {signal.source_urls.length === 1 ? 'Source' : `Source ${index + 1}`}
                        </a>
                      ))
                      : <span className="dashboard-source-note">No source URL available</span>}
                  </span>
                )}
              </article>
            ))}
          </div>
        </section>

        {showDemoControls && me && <DemoControls d0Date={me.d0_date} />}
      </div>
    </section>
  );
}
