import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useCases } from '../../api/hooks';
import type { CaseSummary } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatDateTime, formatIDRShort, formatScore } from '../../lib/format';
import './cases.css';

type CaseListStatus = 'open' | 'all';

function labelFromCode(value: string): string {
  return value
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function CaseStatus({ status }: { status: CaseSummary['status'] }) {
  return <span className="cases-status">{labelFromCode(status)}</span>;
}

function CasesError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="cases-state cases-state--error" role="alert">
      <p>Cases could not be loaded.</p>
      <Button variant="secondary" onClick={onRetry}>Try again</Button>
    </div>
  );
}

export function CasesPage() {
  const [status, setStatus] = useState<CaseListStatus>('open');
  const cases = useCases(status);

  return (
    <section className="cases-page">
      <div className="page-header">
        <h1>Cases</h1>
      </div>
      <p className="page-description">Risk cases and their current exposure, priority, and status.</p>

      <section className="cases-panel card" aria-labelledby="cases-list-title">
        <header className="cases-panel__header">
          <div>
            <h2 id="cases-list-title">Risk cases</h2>
            {cases.data && (
              <p aria-live="polite">{cases.data.length} {cases.data.length === 1 ? 'case' : 'cases'}</p>
            )}
          </div>
          <label className="cases-filter">
            <span>Show</span>
            <select
              aria-label="Case list status"
              value={status}
              onChange={(event) => setStatus(event.target.value as CaseListStatus)}
            >
              <option value="open">Open cases</option>
              <option value="all">All cases</option>
            </select>
          </label>
        </header>

        {cases.isPending ? (
          <p className="cases-state" role="status">Loading cases…</p>
        ) : cases.isError || !cases.data ? (
          <CasesError onRetry={() => void cases.refetch()} />
        ) : (
          <div className="cases-table-scroll" role="region" aria-label="Risk cases" tabIndex={0}>
            <table className="cases-table" aria-labelledby="cases-list-title">
              <thead>
                <tr>
                  <th scope="col">Case</th>
                  <th scope="col">Risk</th>
                  <th scope="col">Level</th>
                  <th scope="col">Priority score</th>
                  <th scope="col">Units at risk</th>
                  <th scope="col">Revenue at risk</th>
                  <th scope="col">Status</th>
                  <th scope="col">Updated</th>
                </tr>
              </thead>
              <tbody>
                {cases.data.length ? cases.data.map((caseItem) => (
                  <tr key={caseItem.case_id}>
                    <td>
                      <Link className="cases-table__link" to={`/cases/${encodeURIComponent(caseItem.case_id)}`}>
                        {caseItem.title}
                      </Link>
                      <span className="cases-table__secondary mono">{caseItem.case_id}</span>
                      <span className="cases-table__secondary">
                        {caseItem.location || caseItem.country} · {caseItem.country}
                      </span>
                    </td>
                    <td>
                      <span className="cases-table__primary">{labelFromCode(caseItem.risk_type)}</span>
                      <span className="cases-table__secondary">{labelFromCode(caseItem.scope)}</span>
                    </td>
                    <td><Badge variant={caseItem.level}>{caseItem.level}</Badge></td>
                    <td className="tabular">{formatScore(caseItem.score)}</td>
                    <td className="tabular">{caseItem.units_at_risk.toLocaleString('en-US')}</td>
                    <td className="tabular">{formatIDRShort(caseItem.revenue_at_risk)}</td>
                    <td>
                      <div className="cases-table__status">
                        <CaseStatus status={caseItem.status} />
                        <Badge variant={caseItem.simulated ? 'SIMULATED' : 'LIVE'} />
                      </div>
                    </td>
                    <td>
                      <time className="cases-table__date" dateTime={caseItem.updated_at}>
                        {formatDateTime(caseItem.updated_at)}
                      </time>
                    </td>
                  </tr>
                )) : (
                  <tr>
                    <td className="cases-table__empty" colSpan={8}>
                      {status === 'open' ? 'No open cases.' : 'No cases are available.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}
