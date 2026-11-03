import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { USE_MOCKS } from '../api/client';
import { useAgentTrace } from '../api/hooks';
import type { AgentTrace, TraceStatus } from '../api/types';
import { Badge } from './Badge';
import { Button } from './Button';
import { formatDateTime } from '../lib/format';
import './agent-activity.css';

interface AgentActivityTimelineProps {
  caseId?: string;
  showHeading?: boolean;
}

function labelFromCode(value: string): string {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

// Langkah yang ditulis server (mis. score_case) tidak punya task, run, atau durasi: API mengirim null (EP-13).
function formatDuration(durationMs: number | null): string {
  if (durationMs === null) return '—';
  if (durationMs < 1_000) return `${durationMs.toLocaleString('en-US')} ms`;
  return `${(durationMs / 1_000).toFixed(1)} s`;
}

const STATUS_LABELS: Record<TraceStatus, string> = {
  RUNNING: 'RUNNING',
  OK: 'OK',
  ERROR: 'ERROR',
  SKIPPED: 'SKIPPED',
  AGENT_TIMEOUT: 'AGENT_TIMEOUT',
  LLM_OVERRIDDEN: 'LLM_OVERRIDDEN',
};

function TraceStatus({ status }: { status: TraceStatus }) {
  const className = status.toLowerCase().replace(/_/g, '-');
  return (
    <span className={`agent-trace-status agent-trace-status--${className}`}>
      {status === 'RUNNING' && <span className="agent-trace-status__spinner" aria-hidden="true" />}
      {STATUS_LABELS[status]}
    </span>
  );
}

function TraceEntry({ entry }: { entry: AgentTrace }) {
  return (
    <li className={`agent-trace-entry agent-trace-entry--${entry.status.toLowerCase().replace(/_/g, '-')}`}>
      <span className="agent-trace-entry__marker" aria-hidden="true" />
      <article className="agent-trace-card">
        <header className="agent-trace-card__header">
          <div className="agent-trace-card__title">
            <time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>
            <h3>{labelFromCode(entry.step)}</h3>
            <p>{entry.task ? `Agent: ${labelFromCode(entry.task)}` : 'Recorded by SENTRA'}</p>
          </div>
          <div className="agent-trace-card__status">
            <TraceStatus status={entry.status} />
            <span className="agent-trace-duration">{formatDuration(entry.duration_ms)}</span>
          </div>
        </header>

        <dl className="agent-trace-fields">
          <div><dt>Tool</dt><dd>{entry.tool || 'Not recorded'}</dd></div>
          <div><dt>Input</dt><dd>{entry.input_summary || 'No input summary.'}</dd></div>
          <div><dt>Output</dt><dd>{entry.output_summary || 'No output summary.'}</dd></div>
        </dl>

        {entry.decision && (
          <aside className="agent-trace-decision" aria-label="Branch decision">
            <strong>Branch decision</strong>
            <p>{entry.decision}</p>
          </aside>
        )}

        <footer className="agent-trace-card__footer">
          {entry.run_id ? <span>Run <code>{entry.run_id}</code></span> : <span>No agent run</span>}
          {entry.case_id ? (
            <Link to={`/cases/${encodeURIComponent(entry.case_id)}`}>
              Case <code>{entry.case_id}</code> <span aria-hidden="true">→</span>
            </Link>
          ) : <span>Global activity</span>}
        </footer>
      </article>
    </li>
  );
}

export function AgentActivityTimeline({ caseId, showHeading = true }: AgentActivityTimelineProps) {
  const trace = useAgentTrace(caseId);
  const entries = useMemo(() => [...(trace.data ?? [])].sort((left, right) => {
    const timeDifference = Date.parse(left.created_at) - Date.parse(right.created_at);
    return Number.isNaN(timeDifference) || timeDifference === 0
      ? left.trace_id.localeCompare(right.trace_id)
      : timeDifference;
  }), [trace.data]);

  return (
    <section className="agent-activity" aria-label={caseId ? `Agent activity for ${caseId}` : 'Global agent activity'}>
      {showHeading && (
        <header className="agent-activity__header">
          <div>
            <h2>Agent activity</h2>
            <p>{caseId ? `Trace for case ${caseId}, ordered by event time.` : 'Recent agent steps, ordered by event time.'}</p>
          </div>
          <div className="agent-activity__actions">
            {USE_MOCKS && <Badge variant="SIMULATED" />}
            {trace.isFetching && !trace.isPending && <span role="status">Updating…</span>}
            {caseId && <Link className="agent-activity__global-link" to="/agent-activity">View global activity <span aria-hidden="true">→</span></Link>}
            <Button variant="ghost" onClick={() => void trace.refetch()} disabled={trace.isFetching}>Refresh</Button>
          </div>
        </header>
      )}

      {trace.isPending ? (
        <p className="agent-activity__state" role="status">Loading agent activity…</p>
      ) : trace.isError && !trace.data ? (
        <div className="agent-activity__state agent-activity__state--error" role="alert">
          <p>Agent activity could not be loaded.</p>
          <Button variant="secondary" onClick={() => void trace.refetch()}>Try again</Button>
        </div>
      ) : entries.length === 0 ? (
        <p className="agent-activity__state">No agent activity has been recorded.</p>
      ) : (
        <ol className="agent-trace-list" aria-live="polite">
          {entries.map((entry) => <TraceEntry entry={entry} key={entry.trace_id} />)}
        </ol>
      )}
    </section>
  );
}
