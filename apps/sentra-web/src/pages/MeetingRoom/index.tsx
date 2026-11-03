import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMeetingRoom, useResendMeetingRoomEmail } from '../../api/hooks';
import type { Meeting, MeetingRoom, ParticipantRole, Role } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { EscalationCard } from '../../components/EscalationCard';
import { decisionOptionLabel, ROLE_LABELS } from '../../lib/labels';
import { bcpBandLabel } from '../../lib/c42';
import { formatDateTime, formatIDR, formatIDRShort, formatScore } from '../../lib/format';
import { DecisionForm, OPTION_TITLES } from './DecisionForm';
import './meeting-room.css';

type SortColumn = 'tier' | 'recovery_days' | 'rar' | 'bcp_score' | 'score';
type SortDirection = 'asc' | 'desc';

function labelFromCode(value: string): string {
  return value.toLowerCase().split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function meetingKindLabel(kind: Meeting['kind']): string {
  return kind === 'EXECUTIVE_MEETING_ROOM' ? 'Executive Meeting Room' : 'Meeting Room';
}

function participantLabel(role: ParticipantRole | string): string {
  return ROLE_LABELS[role as Role] ?? role;
}

function MeetingSummary({ meeting, latest }: { meeting: Meeting; latest: boolean }) {
  const canJoin = Boolean(meeting.join_url) && meeting.provider !== 'NONE';
  return (
    <article className={`meeting-room__meeting${latest ? ' meeting-room__meeting--latest' : ''}`}>
      <div className="meeting-room__meeting-heading">
        <div>
          <p className="meeting-room__eyebrow">{latest ? 'Latest meeting' : 'Previous meeting'}</p>
          <h3>{meetingKindLabel(meeting.kind)}</h3>
          <p className="meeting-room__meeting-topic">{meeting.topic}</p>
        </div>
        <Badge variant={meeting.status === 'SCHEDULED' ? 'MEDIUM' : 'NEUTRAL'}>{meeting.status}</Badge>
      </div>
      <dl className="meeting-room__facts">
        <div><dt>Start time</dt><dd>{meeting.start_time ? formatDateTime(meeting.start_time) : 'Not scheduled'}</dd></div>
        <div><dt>Duration</dt><dd>{meeting.duration_min} minutes</dd></div>
        <div><dt>Provider</dt><dd>{meeting.provider}</dd></div>
      </dl>
      {canJoin ? (
        <a className="meeting-room__join" href={meeting.join_url ?? undefined} target="_blank" rel="noreferrer">Join Zoom meeting</a>
      ) : meeting.status === 'FAILED' ? (
        <p className="meeting-room__unavailable" role="status">Meeting link unavailable — see email log</p>
      ) : null}
      {meeting.error && <p className="meeting-room__meeting-error">{meeting.error}</p>}
    </article>
  );
}

function notificationTime(value: string | null): string {
  return value ? formatDateTime(value) : 'Not sent';
}

function sortValue(site: MeetingRoom['sites'][number], column: SortColumn): number {
  if (column === 'tier') return site.tier === 'ALT' ? 3 : Number(site.tier);
  return site[column];
}

function SitesTable({ sites }: { sites: MeetingRoom['sites'] }) {
  const [sortColumn, setSortColumn] = useState<SortColumn>('score');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
  const sortedSites = useMemo(() => sites
    .map((site, index) => ({ site, index }))
    .sort((a, b) => {
      const difference = sortValue(a.site, sortColumn) - sortValue(b.site, sortColumn);
      return (difference === 0 ? a.index - b.index : difference) * (sortDirection === 'asc' ? 1 : -1);
    })
    .map(({ site }) => site), [sites, sortColumn, sortDirection]);

  function changeSort(column: SortColumn) {
    if (column === sortColumn) {
      setSortDirection((current) => current === 'asc' ? 'desc' : 'asc');
      return;
    }
    setSortColumn(column);
    setSortDirection(column === 'score' ? 'desc' : 'asc');
  }

  function sortButton(column: SortColumn, label: string) {
    const active = sortColumn === column;
    return (
      <button className="meeting-room__sort-button" type="button" onClick={() => changeSort(column)} aria-label={`Sort by ${label}`}>
        {label}{active ? (sortDirection === 'asc' ? ' ↑' : ' ↓') : ''}
      </button>
    );
  }

  return (
    <section className="meeting-room__panel card" aria-labelledby="meeting-room-sites-title">
      <header className="meeting-room__section-header">
        <div><h2 id="meeting-room-sites-title">Affected sites</h2><p>Sorted by priority score, highest first.</p></div>
      </header>
      <div className="meeting-room__table-scroll" role="region" aria-label="Affected site priority table" tabIndex={0}>
        <table className="meeting-room__table">
          <thead><tr>
            <th scope="col">Site</th>
            <th scope="col">{sortButton('tier', 'Tier')}</th>
            <th scope="col">{sortButton('recovery_days', 'Recovery time (days)')}</th>
            <th scope="col">{sortButton('rar', 'Revenue at risk')}</th>
            <th scope="col">{sortButton('bcp_score', 'BCP score')}</th>
            <th scope="col">{sortButton('score', 'Priority score')}</th>
            <th scope="col">Verification</th>
          </tr></thead>
          <tbody>{sortedSites.length ? sortedSites.map((site) => (
            <tr key={site.site_id}>
              <th scope="row"><span>{site.supplier_name}</span><small>{site.site_id}</small></th>
              <td>{site.tier}</td>
              <td>{site.recovery_days} days</td>
              <td title={formatIDR(site.rar)}>{formatIDRShort(site.rar)}</td>
              <td>{formatScore(site.bcp_score)} · {bcpBandLabel(site.bcp_band)}</td>
              <td>{formatScore(site.score)}</td>
              <td>{labelFromCode(site.verification_status)}</td>
            </tr>
          )) : <tr><td className="meeting-room__empty" colSpan={7}>No affected sites were returned.</td></tr>}</tbody>
        </table>
      </div>
    </section>
  );
}

function EmailLog({ data }: { data: MeetingRoom }) {
  const { isScmManager } = useAuth();
  const resendMutation = useResendMeetingRoomEmail(data.case_id);
  const notification = resendMutation.data ?? data.notification;
  if (!notification) {
    return <section className="meeting-room__panel card" aria-labelledby="meeting-room-email-title"><h2 id="meeting-room-email-title">Email notification log</h2><p>No meeting email has been recorded.</p></section>;
  }
  return (
    <section className="meeting-room__panel card" aria-labelledby="meeting-room-email-title">
      <header className="meeting-room__section-header">
        <div><h2 id="meeting-room-email-title">Email notification log</h2><p>{meetingKindLabel(notification.kind)} · {notification.meeting_id}</p></div>
        <Badge variant={notification.status === 'SENT' ? 'LOW' : 'HIGH'}>{notification.status}</Badge>
      </header>
      <dl className="meeting-room__email-facts">
        <div><dt>Recipients</dt><dd>{notification.recipients.map((role) => participantLabel(role)).join(', ')}</dd></div>
        <div><dt>Sent at</dt><dd>{notificationTime(notification.sent_at)}</dd></div>
        <div><dt>Error</dt><dd>{notification.error ?? '—'}</dd></div>
      </dl>
      {resendMutation.isError && <p className="meeting-room__validation" role="alert">The email could not be resent.</p>}
      {resendMutation.isSuccess && <p className="meeting-room__decision-success" role="status">Email sent successfully.</p>}
      {isScmManager && notification.status === 'FAILED' && (
        <Button variant="secondary" onClick={() => resendMutation.mutate()} loading={resendMutation.isPending}>Resend email</Button>
      )}
    </section>
  );
}

function MeetingRoomContent({ caseId }: { caseId: string }) {
  const query = useMeetingRoom(caseId);
  if (query.isPending) return <section className="meeting-room-page"><p role="status">Loading Meeting Room…</p></section>;
  if (query.isError || !query.data) {
    return (
      <section className="meeting-room-page">
        <h1>Meeting Room</h1>
        <div className="meeting-room__state" role="alert"><p>Meeting Room information could not be loaded.</p><Button variant="secondary" onClick={() => void query.refetch()}>Try again</Button></div>
      </section>
    );
  }

  const data = query.data;
  const latestMeeting = data.meetings[0] ?? data.escalation.meeting;
  const previousMeetings = data.meetings.slice(1);
  const savedDecision = data.escalation.decision;
  const decisionText = savedDecision
    ? `Decision recorded: ${decisionOptionLabel(savedDecision.option)} approved by ${savedDecision.approved_by.join(', ')}`
    : null;

  return (
    <section className="meeting-room-page">
      <div className="meeting-room__breadcrumb"><Link to={`/cases/${encodeURIComponent(caseId)}`}>Back to case</Link><span aria-hidden="true">/</span><span>Meeting Room</span></div>
      <header className="meeting-room__page-header">
        <div>
          <p className="meeting-room__eyebrow">{data.case_id}</p>
          <h1>{data.title}</h1>
          <p className="meeting-room__meta">Updated {formatDateTime(data.updated_at)} · {labelFromCode(data.status)}</p>
        </div>
        <div className="meeting-room__badges">
          <Badge variant={data.simulated ? 'SIMULATED' : 'LIVE'} />
          <Badge variant={data.level}>{data.level}</Badge>
        </div>
      </header>

      <EscalationCard escalation={data.escalation} />
      {decisionText && <p className="meeting-room__decision-success" role="status">{decisionText}</p>}

      <div className="meeting-room__grid">
        <section className="meeting-room__panel card" aria-labelledby="meeting-room-latest-title">
          <header className="meeting-room__section-header"><div><h2 id="meeting-room-latest-title">Meeting details</h2><p>Latest meeting first</p></div></header>
          {latestMeeting ? <MeetingSummary meeting={latestMeeting} latest /> : <p>No meetings are scheduled for this case.</p>}
          {previousMeetings.length > 0 && (
            <div className="meeting-room__previous-meetings">
              <h3>Previous meetings</h3>
              {previousMeetings.map((meeting) => <MeetingSummary key={meeting.meeting_id} meeting={meeting} latest={false} />)}
            </div>
          )}
        </section>
        <EmailLog data={data} />
      </div>

      {data.escalation.tier >= 1 && <DecisionForm data={data} />}

      <SitesTable sites={data.sites} />

      <section className="meeting-room__panel meeting-room__recommendation card" aria-labelledby="meeting-room-recommendation-title">
        <header className="meeting-room__section-header"><div><h2 id="meeting-room-recommendation-title">Latest recommendation</h2></div></header>
        <p className="meeting-room__recommended-option">{data.latest_recommendation.recommended_option ? OPTION_TITLES[data.latest_recommendation.recommended_option] : 'No option'}</p>
        {data.latest_recommendation.preliminary && <Badge variant="NEUTRAL">PRELIMINARY</Badge>}
        {data.latest_recommendation.rule_reason && <p>{data.latest_recommendation.rule_reason}</p>}
      </section>
    </section>
  );
}

export function MeetingRoomPage() {
  const { caseId } = useParams();
  if (!caseId) {
    return <section className="meeting-room-page"><h1>Meeting Room</h1><p>Case ID is missing.</p></section>;
  }
  return <MeetingRoomContent key={caseId} caseId={caseId} />;
}
