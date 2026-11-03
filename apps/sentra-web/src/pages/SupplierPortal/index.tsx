import { Link } from 'react-router-dom';
import { useCases, useSupplierMessages } from '../../api/hooks';
import type { SupplierMessage } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatDateTime } from '../../lib/format';
import { requestTypeLabel } from '../../lib/c42';
import './supplier-portal.css';

type SupplierMessageGroup = { requestType: SupplierMessage['request_type']; messages: SupplierMessage[] };
type SupplierSiteMessages = { siteId: string; groups: SupplierMessageGroup[] };

export function messagesBySite(messages: SupplierMessage[]): SupplierSiteMessages[] {
  const grouped = new Map<string, SupplierMessage[]>();
  messages.forEach((message) => {
    const siteMessages = grouped.get(message.site_id) ?? [];
    siteMessages.push(message);
    grouped.set(message.site_id, siteMessages);
  });

  const requestOrder: SupplierMessage['request_type'][] = ['IMPACT', 'BACKUP_ACTIVATION', 'PULL_IN'];
  return [...grouped.entries()]
    .sort(([siteA], [siteB]) => siteA.localeCompare(siteB))
    .map(([siteId, siteMessages]) => {
      const byRequestType = new Map<SupplierMessage['request_type'], SupplierMessage[]>();
      siteMessages.forEach((message) => {
        const requestMessages = byRequestType.get(message.request_type) ?? [];
        requestMessages.push(message);
        byRequestType.set(message.request_type, requestMessages);
      });
      return {
        siteId,
        groups: requestOrder.flatMap((requestType) => {
          const requestMessages = byRequestType.get(requestType);
          return requestMessages ? [{
            requestType,
            messages: [...requestMessages].sort((messageA, messageB) => messageA.created_at.localeCompare(messageB.created_at)),
          }] : [];
        }),
      };
    });
}

function structuredFields(message: SupplierMessage): Array<[string, string]> | null {
  const structured = message.structured;
  if (!structured) return null;

  const fields: Array<[string, string]> = [];
  if (message.request_type === 'IMPACT' && 'affected' in structured) {
    fields.push(['Affected', structured.affected], ['Expected delay', `${structured.delay_days} days`]);
  } else if (message.request_type === 'BACKUP_ACTIVATION' && 'ramp_days' in structured) {
    fields.push(['Activation answer', structured.answer]);
    if (structured.ramp_days !== null) fields.push(['Ramp-up', `${structured.ramp_days} days`]);
  } else if (message.request_type === 'PULL_IN' && 'answer' in structured && !('ramp_days' in structured)) {
    fields.push(['Pull-in answer', structured.answer]);
  }

  if (fields.length === 0) return null;
  fields.push(['Notes', structured.notes || '—']);
  return fields;
}

export function SupplierMessageCard({ message }: { message: SupplierMessage }) {
  const incoming = message.direction === 'IN';
  const fields = structuredFields(message);

  return (
    <article className={`supplier-message${incoming ? ' supplier-message--incoming' : ' supplier-message--outgoing'}`}>
      <header className="supplier-message__header">
        <div>
          <h5>{incoming ? 'Incoming reply' : 'Outgoing request'}</h5>
          <time dateTime={message.created_at}>{formatDateTime(message.created_at)}</time>
        </div>
        <Badge variant={message.simulated ? 'SIMULATED' : 'LIVE'} />
      </header>
      <p className="supplier-message__body">{message.body}</p>
      {fields ? (
        <dl className="supplier-message__details">
          {fields.map(([label, value]) => (
            <div className={label === 'Notes' ? 'supplier-message__notes' : undefined} key={label}><dt>{label}</dt><dd>{value}</dd></div>
          ))}
        </dl>
      ) : (
        <p className="supplier-message__unstructured">No structured reply is attached to this message.</p>
      )}
    </article>
  );
}

export function SupplierPortalPage() {
  const cases = useCases('all', true);
  const caseIds = cases.data?.map((caseItem) => caseItem.case_id) ?? [];
  const messageQueries = useSupplierMessages(caseIds);

  return (
    <section className="supplier-portal-page">
      <div className="page-header supplier-portal__heading">
        <h1>Supplier portal</h1>
        <Badge variant="SIMULATED" />
      </div>
      <p className="page-description">Simulated confirmation requests and replies, grouped by case and supplier site.</p>

      {cases.isPending ? (
        <p className="supplier-portal__state" role="status">Loading supplier messages…</p>
      ) : cases.isError || !cases.data ? (
        <div className="supplier-portal__state" role="alert">
          <p>Cases could not be loaded.</p>
          <Button variant="secondary" onClick={() => void cases.refetch()}>Try again</Button>
        </div>
      ) : cases.data.length === 0 ? (
        <section className="supplier-portal__empty card">No cases are available.</section>
      ) : (
        <div className="supplier-portal__cases">
          {cases.data.map((caseItem, index) => {
            const query = messageQueries[index];
            const messages = query?.data ?? [];

            return (
              <section className="supplier-portal__case card" key={caseItem.case_id} aria-labelledby={`supplier-case-${caseItem.case_id}`}>
                <header className="supplier-portal__case-header">
                  <div>
                    <h2 id={`supplier-case-${caseItem.case_id}`}>{caseItem.title}</h2>
                    <p><span className="mono">{caseItem.case_id}</span><span>{caseItem.location || caseItem.country} · {caseItem.country}</span></p>
                  </div>
                  <Link className="supplier-portal__case-link" to={`/cases/${encodeURIComponent(caseItem.case_id)}`}>Open case</Link>
                </header>

                {query?.isPending ? (
                  <p className="supplier-portal__state" role="status">Loading messages…</p>
                ) : query?.isError ? (
                  <div className="supplier-portal__state" role="alert">
                    <p>Messages for this case could not be loaded.</p>
                    <Button variant="secondary" onClick={() => void query.refetch()}>Try again</Button>
                  </div>
                ) : messages.length === 0 ? (
                  <p className="supplier-portal__empty-message">No supplier messages are recorded for this case.</p>
                ) : (
                  <div className="supplier-portal__sites">
                    {messagesBySite(messages).map(({ siteId, groups }) => (
                      <section className="supplier-portal__site" key={siteId} aria-labelledby={`supplier-site-${caseItem.case_id}-${siteId}`}>
                        <h3 id={`supplier-site-${caseItem.case_id}-${siteId}`}>Site <span className="mono">{siteId}</span></h3>
                        {groups.map((group) => (
                          <section className="supplier-portal__request-group" key={group.requestType} aria-labelledby={`supplier-request-${caseItem.case_id}-${siteId}-${group.requestType}`}>
                            <h4 id={`supplier-request-${caseItem.case_id}-${siteId}-${group.requestType}`}>{requestTypeLabel(group.requestType)}</h4>
                            <div className="supplier-portal__thread">
                              {group.messages.map((message) => <SupplierMessageCard key={message.message_id} message={message} />)}
                            </div>
                          </section>
                        ))}
                      </section>
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
    </section>
  );
}
