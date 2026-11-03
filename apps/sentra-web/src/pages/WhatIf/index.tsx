import { lazy, Suspense, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../../auth/AuthContext';
import { ApiError } from '../../api/client';
import { useRunWhatIf, useWhatIfLocations } from '../../api/hooks';
import type { ImpactPOLine, ImpactProductionOrder, WhatIfResult } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatDate, formatDemoOffset, formatIDR, formatIDRShort, formatPoLine } from '../../lib/format';
import './what-if.css';

const ThreeBarChart = lazy(() => import('./ThreeBarChart'));
const WHAT_IF_BANNER = 'What-if only \u2014 no case, no email, no ERP change.';
const ERP_TABLE_LABEL = 'Mock SAP S/4HANA \u2014 SIMULATED';

const formatMoneyAxisValue = (value: number) => formatIDRShort(value);
const formatDayAxisValue = (value: number) => `${Math.round(value).toLocaleString('en-US')} days`;
const formatCountAxisValue = (value: number) => Math.round(value).toLocaleString('en-US');

function isFeatureDisabled(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && error.code === 'FEATURE_DISABLED';
}

function ChartCard({ title, children, featured = false }: { title: string; children: ReactNode; featured?: boolean }) {
  return (
    <article className={`whatif-chart card${featured ? ' whatif-chart--featured' : ''}`}>
      <h2>{title}</h2>
      {children}
    </article>
  );
}

function MoneyValue({ value }: { value: number }) {
  return <span title={formatIDR(value)}>{formatIDRShort(value)}</span>;
}

function hasImpact(result: WhatIfResult): boolean {
  const { kpi } = result;
  return [
    kpi.parts_affected,
    kpi.pos_affected,
    kpi.po_lines_affected,
    kpi.units_blocked,
    kpi.orders_blocked,
    kpi.max_delay_days,
    kpi.max_po_slip_days,
    kpi.output_value,
    kpi.po_value,
    kpi.rar,
  ].some((value) => value !== 0) || kpi.products_affected.length > 0;
}

function WhatIfKpis({ result }: { result: WhatIfResult }) {
  const { kpi } = result;
  const orderCount = kpi.orders_blocked.toLocaleString('en-US');
  const orderLabel = `production order${kpi.orders_blocked === 1 ? '' : 's'}`;
  const poCount = kpi.pos_affected.toLocaleString('en-US');
  const lineCount = kpi.po_lines_affected.toLocaleString('en-US');
  const lineLabel = `line${kpi.po_lines_affected === 1 ? '' : 's'}`;
  const metrics: { label: string; value: ReactNode; detail?: string }[] = [
    {
      label: 'Sites (Tier-1 / Tier-2)',
      value: `${result.sites_t1.length} / ${result.sites_t2.length}`,
      detail: `Tier-1: ${result.sites_t1.join(', ') || 'None'} \u00b7 Tier-2: ${result.sites_t2.join(', ') || 'None'}`,
    },
    { label: 'Parts affected', value: kpi.parts_affected.toLocaleString('en-US') },
    { label: 'POs affected (lines)', value: `${poCount} (${lineCount} ${lineLabel})` },
    {
      label: 'Finished products affected',
      value: kpi.products_affected.length.toLocaleString('en-US'),
      detail: `${kpi.products_affected.join(', ') || 'No affected products'} \u00b7 ${kpi.units_blocked.toLocaleString('en-US')} units \u00b7 ${orderCount} ${orderLabel}`,
    },
    { label: 'Max delay (days)', value: kpi.max_delay_days.toLocaleString('en-US') },
    { label: 'Max PO slip (days)', value: kpi.max_po_slip_days.toLocaleString('en-US') },
  ];

  return (
    <section className="whatif-kpis" aria-label="Simulation impact summary">
      <article className="whatif-kpi whatif-kpi--hero">
        <div className="whatif-kpi__hero-heading">
          <span className="whatif-kpi__label">Revenue at risk</span>
          <span className="whatif-kpi__hero-icon" aria-hidden="true">↗</span>
        </div>
        <strong className="whatif-kpi__hero-value" title={formatIDR(kpi.rar)}>{formatIDRShort(kpi.rar)}</strong>
        <span className="whatif-kpi__hero-caption">Across affected finished products</span>
        <div className="whatif-kpi__money-row">
          <div>
            <span className="whatif-kpi__label">Delayed output value</span>
            <strong title={formatIDR(kpi.output_value)}>{formatIDRShort(kpi.output_value)}</strong>
          </div>
          <div>
            <span className="whatif-kpi__label">Affected PO value</span>
            <strong title={formatIDR(kpi.po_value)}>{formatIDRShort(kpi.po_value)}</strong>
          </div>
        </div>
      </article>
      <div className="whatif-kpis__grid">
        {metrics.map((metric) => (
          <article className="whatif-kpi" key={metric.label}>
            <span className="whatif-kpi__label">{metric.label}</span>
            <strong className="whatif-kpi__value">{metric.value}</strong>
            {metric.detail && <span className="whatif-kpi__detail">{metric.detail}</span>}
          </article>
        ))}
      </div>
    </section>
  );
}

function DateValue({ value, d0Date, demo }: { value: string; d0Date?: string; demo: boolean }) {
  return (
    <span className="whatif-date">
      {formatDate(value)}
      {demo && d0Date && <small>{formatDemoOffset(value, d0Date)}</small>}
    </span>
  );
}

function ErpTableCaption({ children }: { children: ReactNode }) {
  return (
    <caption>
      <span>{children}</span>
      <span className="whatif-table__source">{ERP_TABLE_LABEL}</span>
    </caption>
  );
}

function PurchaseOrderTable({ rows, d0Date, demo }: { rows: ImpactPOLine[]; d0Date?: string; demo: boolean }) {
  return (
    <div className="whatif-table-scroll">
      <table className="whatif-table">
        <ErpTableCaption>Affected purchase order lines</ErpTableCaption>
        <thead>
          <tr>
            <th scope="col">PO / item</th>
            <th scope="col">Part</th>
            <th scope="col">Supplier</th>
            <th scope="col">Quantity</th>
            <th scope="col">Confirmed date</th>
            <th scope="col">New ETA</th>
            <th scope="col">Slip</th>
            <th scope="col">PO value</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row) => (
            <tr key={`${row.po_id}-${row.item_no}`}>
              <th scope="row">{formatPoLine(row.po_id, row.item_no)}</th>
              <td>{row.material_id}</td>
              <td>{row.supplier_id}</td>
              <td>{row.qty.toLocaleString('en-US')}</td>
              <td><DateValue value={row.confirmed_date} d0Date={d0Date} demo={demo} /></td>
              <td><DateValue value={row.new_eta} d0Date={d0Date} demo={demo} /></td>
              <td>{row.slip_days.toLocaleString('en-US')} days</td>
              <td><MoneyValue value={row.value} /></td>
            </tr>
          )) : (
            <tr><td className="whatif-table__empty" colSpan={8}>No affected purchase order lines.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ProductionOrderTable({ rows, d0Date, demo }: { rows: ImpactProductionOrder[]; d0Date?: string; demo: boolean }) {
  return (
    <div className="whatif-table-scroll">
      <table className="whatif-table">
        <ErpTableCaption>Affected production orders</ErpTableCaption>
        <thead>
          <tr>
            <th scope="col">Production order</th>
            <th scope="col">Product</th>
            <th scope="col">Order quantity</th>
            <th scope="col">Units blocked</th>
            <th scope="col">Blocking parts</th>
            <th scope="col">Start date</th>
            <th scope="col">Ready date</th>
            <th scope="col">Output value</th>
            <th scope="col">Sales order</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row) => (
            <tr key={row.order_id}>
              <th scope="row">{row.order_id}</th>
              <td>{row.product_id}</td>
              <td>{row.qty.toLocaleString('en-US')}</td>
              <td>{row.units_blocked.toLocaleString('en-US')}</td>
              <td>{row.blocking_parts.join(', ') || '\u2014'}</td>
              <td><DateValue value={row.start_date} d0Date={d0Date} demo={demo} /></td>
              <td>{row.ready_date ? <DateValue value={row.ready_date} d0Date={d0Date} demo={demo} /> : '\u2014'}</td>
              <td><MoneyValue value={row.output_value} /></td>
              <td>{row.sales_order_id ?? '\u2014'}</td>
            </tr>
          )) : (
            <tr><td className="whatif-table__empty" colSpan={9}>No affected production orders.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function SimulationCharts({ result }: { result: WhatIfResult }) {
  const { charts } = result;
  const [replayKey, setReplayKey] = useState(0);
  const siteData = useMemo(
    () => charts.rar_by_site.map(({ site_id, rar }) => ({ label: site_id, value: rar })),
    [charts.rar_by_site],
  );
  const delayData = useMemo(
    () => charts.delay_by_part.map(({ part_id, days }) => ({ label: part_id, value: days })),
    [charts.delay_by_part],
  );
  const productData = useMemo(
    () => charts.units_by_product.map(({ product_id, units }) => ({ label: product_id, value: units })),
    [charts.units_by_product],
  );

  return (
    <section className="whatif-chart-section" aria-label="Simulation charts">
      <header className="whatif-chart-section__header">
        <div>
          <p className="eyebrow">Impact breakdown</p>
          <h2>Where the disruption lands</h2>
        </div>
        <button className="whatif-replay" type="button" onClick={() => setReplayKey((key) => key + 1)}>
          <span aria-hidden="true">↻</span> Replay animation
        </button>
      </header>
      <div className="whatif-charts">
        <ChartCard title="Revenue at risk by site (standalone)" featured>
          {siteData.length ? (
            <Suspense fallback={<div className="whatif-chart__loading" role="status">Loading chart...</div>}>
              <ThreeBarChart
                data={siteData}
                color="#087f8c"
                label={`Revenue at risk by site: ${charts.rar_by_site.map(({ site_id, rar }) => `${site_id} ${formatIDR(rar)}`).join(', ')}`}
                formatValue={formatMoneyAxisValue}
                formatExactValue={formatIDR}
                replayKey={replayKey}
              />
            </Suspense>
          ) : <p className="whatif-chart__empty">No site impact data for this location.</p>}
          <p className="whatif-chart__note">Site values are standalone and may overlap.</p>
        </ChartCard>

        <ChartCard title="Delay days by part">
          {delayData.length ? (
            <Suspense fallback={<div className="whatif-chart__loading" role="status">Loading chart...</div>}>
              <ThreeBarChart
                data={delayData}
                color="#d17a35"
                label={`Delay days by part: ${charts.delay_by_part.map(({ part_id, days }) => `${part_id} ${days} days`).join(', ')}`}
                formatValue={formatDayAxisValue}
                formatExactValue={(value) => `${Math.round(value).toLocaleString('en-US')} days`}
                replayKey={replayKey}
              />
            </Suspense>
          ) : <p className="whatif-chart__empty">No part delay data for this location.</p>}
        </ChartCard>

        <ChartCard title="Units at risk by product">
          {productData.length ? (
            <Suspense fallback={<div className="whatif-chart__loading" role="status">Loading chart...</div>}>
              <ThreeBarChart
                data={productData}
                color="#548669"
                label={`Units at risk by product: ${charts.units_by_product.map(({ product_id, units }) => `${product_id} ${units} units`).join(', ')}`}
                formatValue={formatCountAxisValue}
                formatExactValue={(value) => `${Math.round(value).toLocaleString('en-US')} units`}
                replayKey={replayKey}
              />
            </Suspense>
          ) : <p className="whatif-chart__empty">No product impact data for this location.</p>}
        </ChartCard>
      </div>
    </section>
  );
}

export function WhatIfPage() {
  const { me } = useAuth();
  const locations = useWhatIfLocations();
  const simulation = useRunWhatIf();
  const [location, setLocation] = useState('');

  if (isFeatureDisabled(locations.error) || isFeatureDisabled(simulation.error)) {
    return <Navigate to="/" replace />;
  }

  function runSimulation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (location) simulation.mutate(location);
  }

  const result = simulation.data;
  const hasResultImpact = result ? hasImpact(result) : false;
  const isDemo = me?.mode === 'DEMO';

  return (
    <section className="whatif-page">
      <div className="whatif-banner" role="status">
        {result?.banner ?? WHAT_IF_BANNER}
      </div>

      <header className="page-header whatif-page__header">
        <div>
          <p className="eyebrow">Scenario planning</p>
          <h1>What-if analysis</h1>
          <p className="page-description">Explore the potential supply chain impact of a disruption at a supplier location.</p>
        </div>
        <Badge variant="SIMULATED">SIMULATED</Badge>
      </header>

      <form className="whatif-controls card" onSubmit={runSimulation}>
        <div className="whatif-controls__copy">
          <span className="whatif-controls__icon" aria-hidden="true">⌖</span>
          <div>
            <label htmlFor="whatif-location">Location</label>
            <p>Choose a supplier location to explore the 30-day impact horizon.</p>
          </div>
        </div>
        <div className="whatif-controls__actions">
          <select
            id="whatif-location"
            value={location}
            onChange={(event) => {
              setLocation(event.target.value);
              simulation.reset();
            }}
            disabled={locations.isPending || locations.isError || !locations.data?.length || simulation.isPending}
          >
            <option value="">Select a location</option>
            {locations.data?.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <Button
            type="submit"
            loading={simulation.isPending}
            disabled={!location || locations.isPending || locations.isError}
            arrow
          >
            Run simulation
          </Button>
        </div>
        {locations.isPending && <p className="whatif-controls__status" role="status">Loading locations...</p>}
        {locations.isError && (
          <div className="whatif-error" role="alert">
            <span>Locations could not be loaded.</span>
            <Button variant="secondary" onClick={() => void locations.refetch()}>Try again</Button>
          </div>
        )}
        {locations.data?.length === 0 && <p className="whatif-controls__status">No simulation locations are available.</p>}
        {simulation.isError && (
          <div className="whatif-error" role="alert">
            <span>The simulation could not be completed.</span>
            <Button variant="secondary" onClick={() => location && simulation.mutate(location)}>Try again</Button>
          </div>
        )}
      </form>

      {result && (
        <section className="whatif-result" aria-label={`${result.location} simulation results`}>
          <header className="whatif-result__header">
            <div>
              <p className="eyebrow">Simulation result</p>
              <h2>{result.location}</h2>
            </div>
            <Badge variant="SIMULATED">SIMULATED</Badge>
          </header>

          {hasResultImpact ? (
            <>
              <WhatIfKpis result={result} />
              <SimulationCharts result={result} />
              <section className="whatif-tables" aria-label="Affected orders">
                <PurchaseOrderTable rows={result.impact.po_lines} d0Date={me?.d0_date} demo={isDemo} />
                <ProductionOrderTable rows={result.impact.production_orders} d0Date={me?.d0_date} demo={isDemo} />
              </section>
            </>
          ) : (
            <p className="whatif-no-impact" role="status">No impact within the 30-day horizon</p>
          )}
        </section>
      )}
      {!result && !locations.isPending && !locations.isError && (
        <section className="whatif-empty-state" aria-label="Start a What-if simulation">
          <div className="whatif-empty-state__visual" aria-hidden="true">
            <svg viewBox="0 0 520 180" role="presentation">
              <path className="whatif-flow-line" d="M77 92 C160 92 158 45 260 45 S360 92 443 92" />
              <path className="whatif-flow-line whatif-flow-line--lower" d="M77 92 C160 92 158 139 260 139 S360 92 443 92" />
              <circle className="whatif-node whatif-node--outer" cx="77" cy="92" r="24" />
              <circle className="whatif-node whatif-node--middle" cx="260" cy="45" r="20" />
              <circle className="whatif-node whatif-node--middle" cx="260" cy="139" r="20" />
              <circle className="whatif-node whatif-node--target" cx="443" cy="92" r="28" />
              <circle className="whatif-node__core" cx="77" cy="92" r="7" />
              <circle className="whatif-node__core" cx="260" cy="45" r="6" />
              <circle className="whatif-node__core" cx="260" cy="139" r="6" />
              <circle className="whatif-node__core" cx="443" cy="92" r="9" />
              <circle className="whatif-flow-dot" cx="157" cy="74" r="4" />
              <circle className="whatif-flow-dot whatif-flow-dot--delay" cx="344" cy="112" r="4" />
            </svg>
            <span className="whatif-empty-state__tag whatif-empty-state__tag--source">Supplier</span>
            <span className="whatif-empty-state__tag whatif-empty-state__tag--parts">Parts &amp; sites</span>
            <span className="whatif-empty-state__tag whatif-empty-state__tag--output">Production</span>
          </div>
          <div className="whatif-empty-state__copy">
            <span className="whatif-empty-state__spark" aria-hidden="true">✳</span>
            <h2>See the ripple before it happens</h2>
            <p>Run a What-if simulation to trace supplier exposure through parts, sites, and finished products.</p>
          </div>
        </section>
      )}
    </section>
  );
}
