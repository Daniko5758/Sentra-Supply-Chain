import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { useSchedule } from '../../api/hooks';
import type { SchedulePOLine, ScheduleProductionOrder } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatDate, formatDateTime, formatDemoOffset } from '../../lib/format';
import './schedule.css';

const quantityFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

function ScheduleDate({ value, d0Date, demo }: { value: string; d0Date: string; demo: boolean }) {
  return (
    <time className="schedule-date" dateTime={value}>
      <span>{formatDate(value)}</span>
      {demo && <span className="schedule-date__offset">{formatDemoOffset(value, d0Date)}</span>}
    </time>
  );
}

function PurchaseOrderStatus({ status }: { status: SchedulePOLine['status'] }) {
  const statusClass = status === 'Slipped' ? 'schedule-status--slipped' : 'schedule-status--on-time';
  return <span className={`schedule-status ${statusClass}`}>{status}</span>;
}

function CoverageStatus({ coverage }: { coverage: ScheduleProductionOrder['coverage'] }) {
  const statusClass = coverage === 'Short' ? 'schedule-status--short' : 'schedule-status--covered';
  return <span className={`schedule-status ${statusClass}`}>{coverage}</span>;
}

function ScheduleStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="schedule-stat">
      <span className="schedule-stat__label">{label}</span>
      <strong className="schedule-stat__value">{quantityFormatter.format(value)}</strong>
    </div>
  );
}

function ScheduleLoading() {
  return <p className="schedule-state" role="status">Loading schedule…</p>;
}

export function SchedulePage() {
  const [showSlippedOnly, setShowSlippedOnly] = useState(false);
  const { me } = useAuth();
  const schedule = useSchedule();

  if (schedule.isPending) {
    return (
      <section>
        <div className="page-header"><h1>Schedule</h1></div>
        <p className="page-description">Purchase order lines and production orders from the Schedule Agent.</p>
        <ScheduleLoading />
      </section>
    );
  }

  if (schedule.isError || !schedule.data) {
    return (
      <section>
        <div className="page-header"><h1>Schedule</h1></div>
        <p className="page-description">Purchase order lines and production orders from the Schedule Agent.</p>
        <div className="schedule-state schedule-state--error" role="alert">
          <p>Schedule data could not be loaded.</p>
          <Button variant="secondary" onClick={() => void schedule.refetch()}>Try again</Button>
        </div>
      </section>
    );
  }

  const data = schedule.data;
  const demo = me?.mode === 'DEMO';
  const visiblePOLines = showSlippedOnly
    ? data.po_lines.filter((line) => line.status === 'Slipped')
    : data.po_lines;

  return (
    <section className="schedule-page">
      <div className="page-header">
        <h1>Schedule</h1>
        {demo && <Badge variant="SIMULATED" />}
      </div>
      <p className="page-description">Purchase order lines and production orders from the Schedule Agent.</p>

      <section className="schedule-summary card" aria-labelledby="schedule-summary-title">
        <div className="schedule-summary__heading">
          <div>
            <h2 id="schedule-summary-title">Schedule Agent summary</h2>
            <p>{data.agent_summary?.text ?? 'No agent summary is available yet.'}</p>
          </div>
          {demo && <Badge variant="SIMULATED" />}
        </div>
        {data.agent_summary && (
          <div className="schedule-summary__meta">
            <span>Source: <span className="mono">{data.agent_summary.source}</span></span>
            <time dateTime={data.agent_summary.generated_at}>
              Generated {formatDateTime(data.agent_summary.generated_at)}
            </time>
          </div>
        )}
      </section>

      <div className="schedule-stats" aria-label="Schedule totals">
        <ScheduleStat label="PO lines" value={data.totals.po_lines} />
        <ScheduleStat label="Slipped" value={data.totals.slipped} />
        <ScheduleStat label="On time" value={data.totals.on_time} />
        <ScheduleStat label="Orders covered" value={data.totals.orders_covered} />
        <ScheduleStat label="Orders short" value={data.totals.orders_short} />
      </div>

      <section className="schedule-panel card" aria-labelledby="po-lines-title">
        <header className="schedule-panel__header">
          <div>
            <h2 id="po-lines-title">Purchase order lines</h2>
            <p aria-live="polite">
              {showSlippedOnly
                ? `Showing ${quantityFormatter.format(visiblePOLines.length)} of ${quantityFormatter.format(data.po_lines.length)} lines`
                : `${quantityFormatter.format(data.po_lines.length)} lines`}
            </p>
          </div>
          <div className="schedule-panel__actions">
            <label className="schedule-filter">
              <input
                type="checkbox"
                checked={showSlippedOnly}
                onChange={(event) => setShowSlippedOnly(event.target.checked)}
              />
              <span>Slipped only</span>
            </label>
            <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>
          </div>
        </header>
        <div className="schedule-table-scroll" role="region" aria-label="Purchase order lines" tabIndex={0}>
          <table className="schedule-table" aria-labelledby="po-lines-title">
            <thead>
              <tr>
                <th scope="col">PO</th>
                <th scope="col">Item</th>
                <th scope="col">Part</th>
                <th scope="col">Supplier</th>
                <th scope="col">Qty</th>
                <th scope="col">Requested</th>
                <th scope="col">Confirmed</th>
                <th scope="col">Slip (days)</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {visiblePOLines.length ? visiblePOLines.map((line) => (
                <tr key={`${line.po_id}-${line.item_no}`}>
                  <td className="mono">{line.po_id}</td>
                  <td className="tabular">{line.item_no}</td>
                  <td>
                    <span className="schedule-table__primary">{line.material_name}</span>
                    <span className="schedule-table__secondary mono">{line.material_id}</span>
                  </td>
                  <td>
                    <span className="schedule-table__primary">{line.supplier_name}</span>
                    <span className="schedule-table__secondary mono">{line.supplier_id}</span>
                  </td>
                  <td className="tabular">{quantityFormatter.format(line.qty)}</td>
                  <td><ScheduleDate value={line.requested_date} d0Date={data.d0_date} demo={demo} /></td>
                  <td><ScheduleDate value={line.confirmed_date} d0Date={data.d0_date} demo={demo} /></td>
                  <td className="tabular">{quantityFormatter.format(line.slip_days)}</td>
                  <td><PurchaseOrderStatus status={line.status} /></td>
                </tr>
              )) : (
                <tr>
                  <td className="schedule-table__empty" colSpan={9}>
                    {showSlippedOnly ? 'No slipped purchase order lines found.' : 'No purchase order lines found.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="schedule-panel card" aria-labelledby="production-orders-title">
        <header className="schedule-panel__header">
          <div>
            <h2 id="production-orders-title">Production orders</h2>
            <p>{quantityFormatter.format(data.production_orders.length)} orders</p>
          </div>
          <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>
        </header>
        <div className="schedule-table-scroll" role="region" aria-label="Production orders" tabIndex={0}>
          <table className="schedule-table schedule-table--production" aria-labelledby="production-orders-title">
            <thead>
              <tr>
                <th scope="col">Order</th>
                <th scope="col">Product</th>
                <th scope="col">Qty</th>
                <th scope="col">Start</th>
                <th scope="col">Sales order</th>
                <th scope="col">Coverage</th>
                <th scope="col">Short parts</th>
              </tr>
            </thead>
            <tbody>
              {data.production_orders.length ? data.production_orders.map((order) => (
                <tr key={order.order_id}>
                  <td className="mono">{order.order_id}</td>
                  <td>{order.product_id}</td>
                  <td className="tabular">{quantityFormatter.format(order.qty)}</td>
                  <td><ScheduleDate value={order.start_date} d0Date={data.d0_date} demo={demo} /></td>
                  <td className="mono">{order.sales_order_id || '—'}</td>
                  <td><CoverageStatus coverage={order.coverage} /></td>
                  <td>
                    {order.coverage === 'Short' && order.short_parts.length > 0
                      ? order.short_parts.join(', ')
                      : '—'}
                  </td>
                </tr>
              )) : (
                <tr><td className="schedule-table__empty" colSpan={7}>No production orders found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}
