import { USE_MOCKS } from '../../api/client';
import { Badge } from '../../components/Badge';
import { AgentActivityTimeline } from '../../components/AgentActivityTimeline';

export function AgentActivityPage() {
  return (
    <section className="agent-activity-page">
      <div className="page-header">
        <h1>Agent activity</h1>
        {USE_MOCKS && <Badge variant="SIMULATED" />}
      </div>
      <p className="page-description">Review recent agent runs, tool calls, and branch decisions.</p>
      <AgentActivityTimeline showHeading={false} />
    </section>
  );
}
