import { useAuth } from '../auth/AuthContext';
import { useAgentRun, useSetSystemMode, useSystemMode } from '../api/hooks';
import { Badge } from './Badge';
import { Button } from './Button';
import { formatDateTime } from '../lib/format';

interface ApplicationModeControlProps {
  compact?: boolean;
}

/** Shared EP-29 mode indicator and SCM Manager control for the header and System status page. */
export function ApplicationModeControl({ compact = false }: ApplicationModeControlProps) {
  const { me, isScmManager } = useAuth();
  const modeQuery = useSystemMode();
  const setMode = useSetSystemMode();
  const mode = modeQuery.data?.mode ?? me?.mode ?? 'DEMO';
  const nextMode = mode === 'DEMO' ? 'LIVE' : 'DEMO';
  const scanRunId = mode === 'LIVE' ? modeQuery.data?.scan_run_id ?? null : null;
  const scanRun = useAgentRun(scanRunId);

  function safeDateTime(value: string | null | undefined): string | null {
    if (!value) return null;
    try {
      return formatDateTime(value);
    } catch {
      return value;
    }
  }

  const liveSince = mode === 'LIVE' ? safeDateTime(modeQuery.data?.live_since) : null;
  const nextScanAt = mode === 'LIVE' ? safeDateTime(modeQuery.data?.next_scan_at) : null;

  return (
    <div className={`application-mode${compact ? ' application-mode--compact' : ''}`}>
      <span className="application-mode__label">{mode} mode</span>
      <Badge variant={mode === 'LIVE' ? 'LIVE' : 'SIMULATED'} />
      {mode === 'LIVE' && (liveSince || nextScanAt || scanRunId) && (
        <span className="application-mode__meta" aria-live="polite">
          {liveSince && <span>Live since {liveSince}</span>}
          {nextScanAt && <span>Next scan {nextScanAt}</span>}
          {scanRunId && (
            <span className="application-mode__scan" title={scanRun.data?.summary ?? scanRunId}>
              Live scan {scanRun.isError ? 'status unavailable' : scanRun.data?.status ?? 'status loading'}
              {' · '}{scanRunId}
            </span>
          )}
        </span>
      )}
      {isScmManager && (
        <Button
          variant={compact ? 'ghost' : 'secondary'}
          className="application-mode__button"
          loading={setMode.isPending}
          disabled={modeQuery.isPending || modeQuery.isError}
          onClick={() => setMode.mutate(nextMode)}
          aria-label={`Switch application to ${nextMode} mode`}
        >
          {compact ? `Go ${nextMode}` : `Switch to ${nextMode}`}
        </Button>
      )}
      {modeQuery.isError && <span className="application-mode__error" role="status">Mode unavailable</span>}
      {setMode.isError && <span className="application-mode__error" role="alert">Could not change mode. Try again.</span>}
    </div>
  );
}
