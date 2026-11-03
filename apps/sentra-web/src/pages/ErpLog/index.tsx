import { useErpLog } from '../../api/hooks';
import type { ErpChangeDocument } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { ErpShell } from '../../components/ErpShell';
import { EMPTY_VALUE, objectClassName } from '../../lib/erpLabels';
import { formatDateTime } from '../../lib/format';
import './erp-log.css';

function ErpLogError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="erp-log-state erp-log-state--error" role="alert">
      <p>ERP changes could not be loaded.</p>
      <Button variant="secondary" onClick={onRetry}>Try again</Button>
    </div>
  );
}

function ChangeValue({ change }: { change: ErpChangeDocument }) {
  return (
    <span className="erp-log__change">
      <span>{change.old_value || '—'}</span>
      <span aria-hidden="true">→</span>
      <strong>{change.new_value || '—'}</strong>
    </span>
  );
}

const CHANGE_INDICATORS: Record<string, string> = { U: 'Updated', I: 'Created', D: 'Deleted' };

interface ChangeGroup {
  key: string;
  rows: ErpChangeDocument[];
}

// One group per change_number, in the order of each group's first row. A row without
// change_number (written before v1.12) is its own group.
function groupByChangeNumber(rows: ErpChangeDocument[]): ChangeGroup[] {
  const groups: ChangeGroup[] = [];
  const byNumber = new Map<string, ChangeGroup>();
  for (const row of rows) {
    if (!row.change_number) {
      groups.push({ key: `row-${row.id}`, rows: [row] });
      continue;
    }
    let group = byNumber.get(row.change_number);
    if (!group) {
      group = { key: `change-${row.change_number}`, rows: [] };
      byNumber.set(row.change_number, group);
      groups.push(group);
    }
    group.rows.push(row);
  }
  return groups;
}

function groupTitle(first: ErpChangeDocument): string {
  const objectName = objectClassName(first.object_class);
  return [
    `Change ${first.change_number}`,
    [objectName, first.object_key].filter(Boolean).join(' '),
    first.tcode ? `Transaction ${first.tcode}` : '',
    first.username ? `User ${first.username}` : '',
  ].filter(Boolean).join(' · ');
}

function valueOrDash(value: string | null | undefined): string {
  return value ? value : EMPTY_VALUE;
}

function ChangeGroupBody({ group }: { group: ChangeGroup }) {
  const first = group.rows[0];
  if (!first.change_number) {
    return (
      <tbody className="erp-log__group">
        <tr>
          <th scope="col">Object</th>
          <th scope="col">Field</th>
          <th scope="col">Change</th>
          <th scope="col">Changed by</th>
          <th scope="col">Time</th>
        </tr>
        <tr>
          <td>
            <span className="erp-log__object-type">{first.object_type}</span>
            <span className="erp-log__object-id mono">{first.object_id}</span>
          </td>
          <td className="mono">{first.field}</td>
          <td><ChangeValue change={first} /></td>
          <td>{first.changed_by}</td>
          <td><time dateTime={first.changed_at}>{formatDateTime(first.changed_at)}</time></td>
        </tr>
      </tbody>
    );
  }

  return (
    <tbody className="erp-log__group">
      <tr className="erp-log__group-title">
        <th scope="colgroup" colSpan={5}>
          <span>{groupTitle(first)}</span>
          <time dateTime={first.changed_at}>{formatDateTime(first.changed_at)}</time>
        </th>
      </tr>
      <tr>
        <th scope="col">Table-field</th>
        <th scope="col">Field</th>
        <th scope="col">Change</th>
        <th scope="col">Old value</th>
        <th scope="col">New value</th>
      </tr>
      {group.rows.map((row) => (
        <tr key={row.id}>
          <td className="mono">{[row.table_name, row.field_name].filter(Boolean).join('-')}</td>
          <td>{row.field_label ?? row.field}</td>
          <td>{row.change_ind ? (CHANGE_INDICATORS[row.change_ind] ?? row.change_ind) : EMPTY_VALUE}</td>
          <td>{valueOrDash(row.old_value)}</td>
          <td>{valueOrDash(row.new_value)}</td>
        </tr>
      ))}
    </tbody>
  );
}

export function ErpLogPage() {
  const log = useErpLog();

  return (
    <section className="erp-log-page">
      <div className="page-header">
        <h1>ERP log</h1>
        <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>
      </div>
      <p className="page-description">Recorded ERP changes made by SENTRA actions. Every entry is simulated.</p>

      <ErpShell title="Display change documents">
        <section className="erp-log-panel card" aria-labelledby="erp-log-title">
          <header className="erp-log-panel__header">
            <div>
              <h2 id="erp-log-title">Change documents</h2>
              {log.data && <p aria-live="polite">{log.data.length} {log.data.length === 1 ? 'change' : 'changes'}</p>}
            </div>
          </header>

          {log.isPending ? (
            <p className="erp-log-state" role="status">Loading ERP changes…</p>
          ) : log.isError || !log.data ? (
            <ErpLogError onRetry={() => void log.refetch()} />
          ) : (
            <div className="erp-log-table-scroll" role="region" aria-label="ERP change documents" tabIndex={0}>
              <table className="erp-log-table" aria-labelledby="erp-log-title">
                {log.data.length ? groupByChangeNumber(log.data).map((group) => (
                  <ChangeGroupBody key={group.key} group={group} />
                )) : (
                  <tbody>
                    <tr>
                      <td className="erp-log__empty" colSpan={5}>No ERP changes have been recorded.</td>
                    </tr>
                  </tbody>
                )}
              </table>
            </div>
          )}
        </section>
      </ErpShell>
    </section>
  );
}
