// Form keputusan Meeting Room (tier 1) dan Executive Meeting Room (tier 2): pilih opsi yang dihitung server, atau "Option X" =
// aksi ERP custom yang disusun rapat sendiri. Aksi custom dicek server (R-17) lalu langsung dieksekusi di Mock SAP S/4HANA.
// Tier 2 menyimpan catatan keputusan (EP-30); tier 1 tidak punya catatan, opsi biasa diajukan lewat EP-21 ke Approvals.
// UI tidak menghitung angka bisnis: harga, ongkos, tanggal kirim, dan nilai lama dibaca server dari ERP.
import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  useApprovals, useCustomActionCatalog, usePostCustomDecision, usePostDecision, useRecommendations, useSubmitRecommendation,
} from '../../api/hooks';
import { ApiError } from '../../api/client';
import type {
  ActionResult,
  ApprovalItem,
  ActionType,
  CustomActionCatalog,
  CustomActionRequest,
  CustomDecisionResult,
  DecisionOption,
  DecisionRequest,
  MeetingRoom,
  Option,
  ParticipantRole,
  RecommendationOption,
  Role,
} from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { SearchSelect } from '../../components/SearchSelect';
import { ACTION_TYPE_LABELS, actionChangeRows, actionObjectLabel, erpMessageText } from '../../lib/erpLabels';
import { formatDate, formatIDR, formatIDRShort, formatQuantity } from '../../lib/format';
import { decisionOptionLabel, ROLE_LABELS } from '../../lib/labels';
import './decision-form.css';

export const OPTION_TITLES: Record<Option, string> = {
  A: 'Option A — Protect production',
  B: 'Option B — Reschedule',
  C: 'Option C — Activate supplier backup site',
  D: 'Option D — Expedite shipment',
  E: 'Option E — Pull in before effective date',
  R: 'Option R — Update ERP dates only',
  S: 'Option S — Switch source outside tariff country',
};

const CUSTOM: DecisionOption = 'X';
const ACTION_TYPES = Object.keys(ACTION_TYPE_LABELS) as ActionType[];
const PO_ACTIONS: ActionType[] = ['CHANGE_PO_DATE', 'REDUCE_OR_CANCEL_PO', 'EXPEDITE_SHIPMENT'];

interface Draft {
  id: number;
  type: ActionType;
  poKey: string;
  newDate: string;
  newQty: string;
  orderId: string;
  soId: string;
  sourceKey: string;
  qty: string;
  byAir: boolean;
}

function participantLabel(role: ParticipantRole | string): string {
  return ROLE_LABELS[role as Role] ?? role;
}

function poKey(poId: string, itemNo: number): string {
  return `${poId}/${itemNo}`;
}

function sourceKey(materialId: string, supplierId: string): string {
  return `${materialId}@${supplierId}`;
}

function newDraft(id: number, type: ActionType = 'CHANGE_PO_DATE'): Draft {
  return { id, type, poKey: '', newDate: '', newQty: '', orderId: '', soId: '', sourceKey: '', qty: '', byAir: false };
}

function wholeNumber(value: string): number | null {
  return /^\d+$/.test(value.trim()) ? Number(value.trim()) : null;
}

function laterDate(a: string, b: string): string {
  return a > b ? a : b;
}

function decisionText(option: DecisionOption | null, approvedBy: string[]): string {
  return `Decision recorded: ${decisionOptionLabel(option)} approved by ${approvedBy.join(', ')}`;
}

function formatErrorDetails(details: Record<string, unknown> | undefined): string | null {
  if (!details) return null;
  const entries = Object.entries(details).filter(([key]) => key !== 'index' && key !== 'error');
  if (entries.length === 0) return null;
  return entries.map(([key, value]) => {
    const formatted = Array.isArray(value) ? value.join(', ')
      : value && typeof value === 'object' ? JSON.stringify(value)
        : String(value);
    return `${key}: ${formatted}`;
  }).join(' · ');
}

type ProblemField = 'object' | 'date' | 'qty';

interface Problem {
  text: string;
  missing: boolean; // a field is still empty (a hint until the user tries to save), not a wrong value
  field: ProblemField; // the box the problem is about, outlined in red
}

function missing(text: string, field: ProblemField): Problem {
  return { text, missing: true, field };
}

function invalid(text: string, field: ProblemField): Problem {
  return { text, missing: false, field };
}

/** Problem of a draft that the form can see before sending; the server checks everything again (R-17). */
function draftProblem(draft: Draft, catalog: CustomActionCatalog): Problem | null {
  const today = catalog.today;
  if (PO_ACTIONS.includes(draft.type)) {
    const line = catalog.po_lines.find((item) => poKey(item.po_id, item.item_no) === draft.poKey);
    if (!line) return missing('Choose a PO line.', 'object');
    if (draft.type === 'CHANGE_PO_DATE') {
      if (!draft.newDate) return missing('Choose the new delivery date.', 'date');
      if (draft.newDate < today) return invalid(`The new date cannot be before today (${formatDate(today)}).`, 'date');
    }
    if (draft.type === 'REDUCE_OR_CANCEL_PO') {
      const qty = wholeNumber(draft.newQty);
      if (qty === null) return missing('Enter the new quantity (0 cancels the line).', 'qty');
      if (qty >= line.qty) return invalid(`The new quantity must be lower than ${formatQuantity(line.qty)}.`, 'qty');
    }
    if (draft.type === 'EXPEDITE_SHIPMENT' && line.shipping_condition === 'AIR') return invalid('This PO line already ships by air.', 'object');
    return null;
  }
  if (draft.type === 'RESCHEDULE_PRODUCTION_ORDER') {
    if (!catalog.production_orders.some((order) => order.order_id === draft.orderId)) return missing('Choose a production order.', 'object');
    if (!draft.newDate) return missing('Choose the new start date.', 'date');
    if (draft.newDate < today) return invalid(`The new date cannot be before today (${formatDate(today)}).`, 'date');
    return null;
  }
  if (draft.type === 'RESCHEDULE_SALES_ORDER') {
    const sales = catalog.sales_orders.find((order) => order.so_id === draft.soId);
    if (!sales) return missing('Choose a sales order.', 'object');
    if (!draft.newDate) return missing('Choose the new delivery date.', 'date');
    if (draft.newDate < laterDate(today, sales.confirmed_delivery_date)) {
      return invalid(`The new date cannot be before ${formatDate(laterDate(today, sales.confirmed_delivery_date))}.`, 'date');
    }
    return null;
  }
  const source = catalog.alternate_sources.find((item) => sourceKey(item.material_id, item.supplier_id) === draft.sourceKey);
  if (!source) return missing('Choose an approved alternate source.', 'object');
  const qty = wholeNumber(draft.qty);
  if (qty === null || qty < 1) return missing('Enter the quantity to buy.', 'qty');
  if (qty > source.max_qty) return invalid(`The quantity cannot be above ${formatQuantity(source.max_qty)}.`, 'qty');
  return null;
}

/** The ERP object a draft writes (the three PO-line actions share the line); null while none is chosen. */
function draftObjectKey(draft: Draft): string | null {
  if (PO_ACTIONS.includes(draft.type)) return draft.poKey ? `PO:${draft.poKey}` : null;
  if (draft.type === 'RESCHEDULE_PRODUCTION_ORDER') return draft.orderId ? `PRD:${draft.orderId}` : null;
  if (draft.type === 'RESCHEDULE_SALES_ORDER') return draft.soId ? `SO:${draft.soId}` : null;
  return draft.sourceKey ? `SRC:${draft.sourceKey}` : null;
}

function draftObjectName(draft: Draft): string {
  if (PO_ACTIONS.includes(draft.type)) return `PO line ${draft.poKey.replace('/', ' / ')}`;
  if (draft.type === 'RESCHEDULE_PRODUCTION_ORDER') return `Production order ${draft.orderId}`;
  if (draft.type === 'RESCHEDULE_SALES_ORDER') return `Sales order ${draft.soId}`;
  return 'This purchase from the alternate source';
}

/** Problems of every draft. One ERP object may be changed by one action only: SENTRA checks every action against the ERP before
 *  the first one runs, so a second change of the same object would be checked against values that no longer hold. */
function draftProblems(drafts: Draft[], catalog: CustomActionCatalog): (Problem | null)[] {
  const seen = new Map<string, number>();
  return drafts.map((draft, index) => {
    const key = draftObjectKey(draft);
    if (key !== null) {
      const first = seen.get(key);
      if (first !== undefined) {
        return invalid(`${draftObjectName(draft)} is already changed by action ${first + 1}; combine the change into one action.`, 'object');
      }
      seen.set(key, index);
    }
    return draftProblem(draft, catalog);
  });
}

function draftRequest(draft: Draft, catalog: CustomActionCatalog): CustomActionRequest {
  if (PO_ACTIONS.includes(draft.type)) {
    const line = catalog.po_lines.find((item) => poKey(item.po_id, item.item_no) === draft.poKey);
    const base: CustomActionRequest = { type: draft.type, po_id: line?.po_id, item_no: line?.item_no };
    if (draft.type === 'CHANGE_PO_DATE') return { ...base, new_date: draft.newDate };
    if (draft.type === 'REDUCE_OR_CANCEL_PO') return { ...base, new_qty: wholeNumber(draft.newQty) ?? undefined };
    return base;
  }
  if (draft.type === 'RESCHEDULE_PRODUCTION_ORDER') return { type: draft.type, order_id: draft.orderId, new_date: draft.newDate };
  if (draft.type === 'RESCHEDULE_SALES_ORDER') return { type: draft.type, so_id: draft.soId, new_date: draft.newDate };
  const source = catalog.alternate_sources.find((item) => sourceKey(item.material_id, item.supplier_id) === draft.sourceKey);
  return {
    type: draft.type,
    material_id: source?.material_id,
    supplier_id: source?.supplier_id,
    qty: wholeNumber(draft.qty) ?? undefined,
    by_air: draft.byAir,
  };
}

// --- option cards ----------------------------------------------------------------------------------------------------------

function OptionCard({
  option,
  recommended,
  checked,
  onSelect,
}: {
  option: RecommendationOption;
  recommended: boolean;
  checked: boolean;
  onSelect: () => void;
}) {
  return (
    <label className={`decision-option${checked ? ' decision-option--checked' : ''}`}>
      <input type="radio" name="decision-option" value={option.option} checked={checked} onChange={onSelect} />
      <span className="decision-option__body">
        <span className="decision-option__title">
          {OPTION_TITLES[option.option]}
          {recommended && <span className="decision-option__tag">Recommended</span>}
        </span>
        {option.label && <span className="decision-option__label">{option.label}</span>}
        {option.after && (
          <span className="decision-option__facts">
            <span>Extra cost <strong title={formatIDR(option.after.premium)}>{formatIDRShort(option.after.premium)}</strong></span>
            <span>Units at risk after <strong>{formatQuantity(option.after.units_blocked)}</strong></span>
            <span>{option.actions.length} ERP action(s)</span>
          </span>
        )}
      </span>
    </label>
  );
}

function CustomOptionCard({ checked, onSelect }: { checked: boolean; onSelect: () => void }) {
  return (
    <label className={`decision-option decision-option--custom${checked ? ' decision-option--checked' : ''}`}>
      <input type="radio" name="decision-option" value={CUSTOM} checked={checked} onChange={onSelect} />
      <span className="decision-option__body">
        <span className="decision-option__title">Other — custom ERP actions</span>
        <span className="decision-option__label">
          The meeting defines its own ERP changes. They are checked by SENTRA and executed in Mock SAP S/4HANA when you save.
        </span>
      </span>
    </label>
  );
}

// --- custom action builder ---------------------------------------------------------------------------------------------------

const CASE_TAG = 'This case';

/** The rows a box offers: only those in the impact of this case, unless "Show all ERP objects" is on or the case has none of this
 *  kind (then all, with a note). The chosen row always stays in the list. */
function scoped<T extends { affected: boolean }>(rows: T[], showAll: boolean, isChosen: (row: T) => boolean): { rows: T[]; fallback: boolean } {
  if (showAll) return { rows, fallback: false };
  if (!rows.some((row) => row.affected)) return { rows, fallback: true };
  return { rows: rows.filter((row) => row.affected || isChosen(row)), fallback: false };
}

function ScopeNote({ fallback, what }: { fallback: boolean; what: string }) {
  return fallback ? <p className="custom-action__hint custom-action__scope-note">This case has no {what}; all ERP objects are shown.</p> : null;
}

function DraftFields({ draft, catalog, showAll, errorField, onChange }: {
  draft: Draft;
  catalog: CustomActionCatalog;
  showAll: boolean;
  errorField: ProblemField | null; // the box to outline in red
  onChange: (patch: Partial<Draft>) => void;
}) {
  const today = catalog.today;
  const objectError = errorField === 'object';
  const inputClass = (field: ProblemField) => (errorField === field ? 'custom-action__input--error' : undefined);
  if (PO_ACTIONS.includes(draft.type)) {
    const candidates = draft.type === 'EXPEDITE_SHIPMENT'
      ? catalog.po_lines.filter((line) => line.shipping_condition !== 'AIR')
      : catalog.po_lines;
    const { rows: lines, fallback } = scoped(candidates, showAll, (item) => poKey(item.po_id, item.item_no) === draft.poKey);
    const line = catalog.po_lines.find((item) => poKey(item.po_id, item.item_no) === draft.poKey);
    return (
      <>
        <SearchSelect
          className="custom-action__field--wide"
          invalid={objectError}
          label="PO line"
          placeholder="Search PO, item, material, supplier or date…"
          value={draft.poKey}
          onChange={(next) => onChange({ poKey: next })}
          options={lines.map((item) => ({
            value: poKey(item.po_id, item.item_no),
            label: `${item.po_id} / ${item.item_no} · ${item.material_name}`,
            detail: `${formatQuantity(item.qty)} pcs · delivery ${formatDate(item.confirmed_date)} · ${item.supplier_name} · ${item.shipping_condition} / ${item.incoterms}`,
            keywords: `${item.material_id} ${item.supplier_id} ${item.confirmed_date}`,
            tag: item.affected ? CASE_TAG : undefined,
          }))}
        />
        <ScopeNote fallback={fallback} what="open PO lines" />
        {draft.type === 'CHANGE_PO_DATE' && (
          <label className="custom-action__field">
            <span>New delivery date</span>
            <input type="date" min={today} value={draft.newDate} className={inputClass('date')} aria-invalid={errorField === 'date'} onChange={(event) => onChange({ newDate: event.target.value })} />
          </label>
        )}
        {draft.type === 'REDUCE_OR_CANCEL_PO' && (
          <label className="custom-action__field">
            <span>New quantity</span>
            <input type="number" min={0} max={line ? line.qty - 1 : undefined} step={1} inputMode="numeric" value={draft.newQty}
              className={inputClass('qty')} aria-invalid={errorField === 'qty'}
              onChange={(event) => onChange({ newQty: event.target.value })} />
          </label>
        )}
        {line && (
          <p className="custom-action__hint">
            {draft.type === 'CHANGE_PO_DATE' && <>Current delivery date {formatDate(line.confirmed_date)}.</>}
            {draft.type === 'REDUCE_OR_CANCEL_PO' && <>Current quantity {formatQuantity(line.qty)}. Enter 0 to cancel the line (deletion indicator L).</>}
            {draft.type === 'EXPEDITE_SHIPMENT' && (
              <>Ships {line.shipping_condition} / {line.incoterms} today; it will ship AIR / FCA.{' '}
                {line.expedite_cost_per_unit === null
                  ? 'This part has no expedite rate, so SENTRA will refuse it.'
                  : `Freight is computed by SENTRA at ${formatIDR(line.expedite_cost_per_unit)} per unit.`}
              </>
            )}
          </p>
        )}
      </>
    );
  }
  if (draft.type === 'RESCHEDULE_PRODUCTION_ORDER') {
    const order = catalog.production_orders.find((item) => item.order_id === draft.orderId);
    const { rows: orders, fallback } = scoped(catalog.production_orders, showAll, (item) => item.order_id === draft.orderId);
    return (
      <>
        <SearchSelect
          className="custom-action__field--wide"
          invalid={objectError}
          label="Production order"
          placeholder="Search order, product or start date…"
          value={draft.orderId}
          onChange={(next) => onChange({ orderId: next })}
          options={orders.map((item) => ({
            value: item.order_id,
            label: `${item.order_id} · ${item.product_name}`,
            detail: `${formatQuantity(item.qty)} pcs · start ${formatDate(item.start_date)} · ${item.status}`,
            keywords: `${item.product_id} ${item.start_date}`,
            tag: item.affected ? CASE_TAG : undefined,
          }))}
        />
        <ScopeNote fallback={fallback} what="open production orders" />
        <label className="custom-action__field">
          <span>New start date</span>
          <input type="date" min={today} value={draft.newDate} className={inputClass('date')} aria-invalid={errorField === 'date'} onChange={(event) => onChange({ newDate: event.target.value })} />
        </label>
        {order && <p className="custom-action__hint">Current start date {formatDate(order.start_date)} ({order.status}).</p>}
      </>
    );
  }
  if (draft.type === 'RESCHEDULE_SALES_ORDER') {
    const sales = catalog.sales_orders.find((item) => item.so_id === draft.soId);
    const { rows: salesOrders, fallback } = scoped(catalog.sales_orders, showAll, (item) => item.so_id === draft.soId);
    return (
      <>
        <SearchSelect
          className="custom-action__field--wide"
          invalid={objectError}
          label="Sales order"
          placeholder="Search order, customer, product or date…"
          value={draft.soId}
          onChange={(next) => onChange({ soId: next })}
          options={salesOrders.map((item) => ({
            value: item.so_id,
            label: `${item.so_id} · ${item.customer}`,
            detail: `${item.product_name} · ${formatQuantity(item.qty)} pcs · delivery ${formatDate(item.confirmed_delivery_date)}`,
            keywords: `${item.product_id} ${item.confirmed_delivery_date}`,
            tag: item.affected ? CASE_TAG : undefined,
          }))}
        />
        <ScopeNote fallback={fallback} what="sales orders" />
        <label className="custom-action__field">
          <span>New delivery date</span>
          <input type="date" min={sales ? laterDate(today, sales.confirmed_delivery_date) : today} value={draft.newDate}
            className={inputClass('date')} aria-invalid={errorField === 'date'}
            onChange={(event) => onChange({ newDate: event.target.value })} />
        </label>
        {sales && <p className="custom-action__hint">Current delivery date {formatDate(sales.confirmed_delivery_date)}. A sales order can only move later.</p>}
      </>
    );
  }
  const source = catalog.alternate_sources.find((item) => sourceKey(item.material_id, item.supplier_id) === draft.sourceKey);
  const { rows: sources, fallback } = scoped(catalog.alternate_sources, showAll,
    (item) => sourceKey(item.material_id, item.supplier_id) === draft.sourceKey);
  return (
    <>
      <SearchSelect
        className="custom-action__field--wide"
        invalid={objectError}
        label="Approved alternate source"
        placeholder="Search material, supplier or country…"
        value={draft.sourceKey}
        onChange={(next) => onChange({ sourceKey: next, byAir: false })}
        options={sources.map((item) => ({
          value: sourceKey(item.material_id, item.supplier_id),
          label: `${item.material_name} @ ${item.supplier_name}`,
          detail: `${formatIDR(item.price)} per unit · max ${formatQuantity(item.max_qty)} pcs · delivery ${formatDate(item.delivery_date)}`
            + (item.air_delivery_date ? ` (air ${formatDate(item.air_delivery_date)})` : ''),
          keywords: `${item.material_id} ${item.supplier_id} ${item.country ?? ''}`,
          tag: item.affected ? CASE_TAG : undefined,
        }))}
      />
      <ScopeNote fallback={fallback} what="delayed or blocking materials with an approved alternate source" />
      <label className="custom-action__field">
        <span>Quantity</span>
        <input type="number" min={1} max={source?.max_qty} step={1} inputMode="numeric" value={draft.qty}
          className={inputClass('qty')} aria-invalid={errorField === 'qty'}
          onChange={(event) => onChange({ qty: event.target.value })} />
      </label>
      {source && (
        <>
          {source.air_delivery_date && (
            <label className="custom-action__check">
              <input type="checkbox" checked={draft.byAir} onChange={(event) => onChange({ byAir: event.target.checked })} />
              <span>Ship by air (delivery {formatDate(source.air_delivery_date)})</span>
            </label>
          )}
          <p className="custom-action__hint">
            New PR and PO at {formatIDR(source.price)} per unit, delivery {formatDate(draft.byAir && source.air_delivery_date ? source.air_delivery_date : source.delivery_date)}
            {draft.byAir && source.air_freight_per_unit !== null ? `, air freight ${formatIDR(source.air_freight_per_unit)} per unit` : ''}.
          </p>
        </>
      )}
    </>
  );
}

function CustomActionsBuilder({
  catalog,
  drafts,
  serverIndex,
  serverMessage,
  showErrors,
  onChange,
  onAdd,
  onRemove,
}: {
  catalog: CustomActionCatalog;
  drafts: Draft[];
  serverIndex: number | null;
  serverMessage: string | null;
  showErrors: boolean; // the user tried to save: empty fields are errors now, not hints
  onChange: (id: number, patch: Partial<Draft>) => void;
  onAdd: () => void;
  onRemove: (id: number) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const problems = draftProblems(drafts, catalog);
  const inCase = catalog.po_lines.filter((line) => line.affected).length;
  return (
    <section className="custom-actions" aria-labelledby="custom-actions-title">
      <header className="custom-actions__header">
        <div>
          <h3 id="custom-actions-title">Custom ERP actions</h3>
          <p>Old values, prices, freight and delivery dates are read from Mock SAP S/4HANA by SENTRA.</p>
        </div>
        <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>
      </header>
      <div className="custom-actions__scope">
        <label className="custom-action__check">
          <input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} />
          <span>Show all ERP objects</span>
        </label>
        <span className="custom-actions__scope-text">
          {showAll
            ? `All open ERP objects are listed; objects of this case are marked "${CASE_TAG}" and come first.`
            : `Only objects of this case are listed (${inCase} of ${catalog.po_lines.length} open PO lines).`}
        </span>
      </div>
      <ol className="custom-actions__list">
        {drafts.map((draft, index) => {
          const problem = problems[index];
          const shown = problem && (!problem.missing || showErrors) ? problem : null; // what counts as an error now
          return (
            <li key={draft.id} className={`custom-action${serverIndex === index || shown ? ' custom-action--error' : ''}`}>
              <div className="custom-action__top">
                <span className="custom-action__number" aria-hidden="true">{index + 1}</span>
                <label className="custom-action__field custom-action__field--type">
                  <span>Action {index + 1} type</span>
                  <select value={draft.type} onChange={(event) => onChange(draft.id, { ...newDraft(draft.id, event.target.value as ActionType) })}>
                    {ACTION_TYPES.map((type) => <option key={type} value={type}>{ACTION_TYPE_LABELS[type]}</option>)}
                  </select>
                </label>
                {drafts.length > 1 && (
                  <button type="button" className="custom-action__remove" onClick={() => onRemove(draft.id)} aria-label={`Remove action ${index + 1}`}>
                    Remove
                  </button>
                )}
              </div>
              <div className="custom-action__fields">
                <DraftFields draft={draft} catalog={catalog} showAll={showAll} errorField={shown?.field ?? null}
                  onChange={(patch) => onChange(draft.id, patch)} />
              </div>
              {problem && (shown
                ? <p className="custom-action__problem">{problem.text}</p>
                : <p className="custom-action__hint">{problem.text}</p>)}
              {serverIndex === index && serverMessage && <p className="custom-action__problem" role="alert">{serverMessage}</p>}
            </li>
          );
        })}
      </ol>
      <Button variant="secondary" onClick={onAdd} disabled={drafts.length >= catalog.max_actions}>
        + Add action
      </Button>
    </section>
  );
}

// --- results -------------------------------------------------------------------------------------------------------------------

const STATUS_LABELS: Record<string, string> = {
  PROPOSED: 'Proposed', APPROVED: 'Approved', EXECUTED: 'Executed', FAILED: 'Failed', REJECTED: 'Rejected',
};

function ResultItem({ action }: { action: ActionResult }) {
  const object = actionObjectLabel(action.type, action.object);
  const messages = action.erp_result?.messages ?? [];
  return (
    <li className={`custom-result custom-result--${action.status.toLowerCase()}`}>
      <div className="custom-result__heading">
        <span className="custom-action__number" aria-hidden="true">{action.seq}</span>
        <div>
          <strong>{ACTION_TYPE_LABELS[action.type]}</strong>
          <span className="custom-result__object">{object.title}{object.detail ? ` · ${object.detail}` : ''}</span>
        </div>
        <span className={`custom-result__status custom-result__status--${action.status.toLowerCase()}`}>
          {STATUS_LABELS[action.status] ?? action.status}
        </span>
      </div>
      <dl className="custom-result__changes">
        {actionChangeRows(action.type, action.payload).map((row) => (
          <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>
        ))}
      </dl>
      {action.erp_result?.document && <p className="custom-result__document mono">{action.erp_result.document}</p>}
      {messages.length > 0 && (
        <ul className="custom-result__messages">
          {messages.map((message, index) => <li key={`${message.id}-${message.number}-${index}`}>{erpMessageText(message)}</li>)}
        </ul>
      )}
      {action.error && <p className="custom-action__problem">{action.error}</p>}
    </li>
  );
}

function CustomResults({ result }: { result: CustomDecisionResult }) {
  const executed = result.actions.filter((action) => action.status === 'EXECUTED').length;
  const open = result.actions.some((action) => action.status !== 'EXECUTED');
  return (
    <section className="custom-results" aria-labelledby="custom-results-title" aria-live="polite">
      <header className="custom-actions__header">
        <div>
          <h3 id="custom-results-title">ERP execution results</h3>
          <p>{executed} of {result.actions.length} action(s) executed · case status {result.case_status.replace(/_/g, ' ').toLowerCase()} ·
            extra cost <span title={formatIDR(result.premium)}>{formatIDRShort(result.premium)}</span></p>
        </div>
        <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>
      </header>
      <ul className="custom-results__list">
        {result.actions.map((action) => <ResultItem key={action.action_id} action={action} />)}
      </ul>
      <div className="custom-results__links">
        {open && <Link to="/approvals">Open Approvals to retry or cancel the remaining actions</Link>}
        <Link to="/erp-log">View ERP log</Link>
      </div>
    </section>
  );
}

// --- form ----------------------------------------------------------------------------------------------------------------------

const CLOSED_STATUSES = ['CLOSED', 'DISMISSED'];
const REPLACED_STATUSES = ['PROPOSED', 'FAILED']; // what a new submission replaces (executor.OPEN_FOR_DECISION)

function waitingByOption(items: ApprovalItem[], caseId: string): { option: DecisionOption; count: number }[] {
  const counts = new Map<DecisionOption, number>();
  for (const item of items) {
    if (item.case_id !== caseId || !REPLACED_STATUSES.includes(item.status)) continue;
    const option = item.option as DecisionOption;
    counts.set(option, (counts.get(option) ?? 0) + 1);
  }
  return [...counts.entries()].map(([option, count]) => ({ option, count }));
}
const EXECUTABLE_STATUSES = ['READY_FOR_DECISION', 'AWAITING_APPROVAL', 'EXECUTED'];

function statusText(status: string): string {
  return status.replace(/_/g, ' ').toLowerCase();
}

/** Tier 2: the decision note of the Executive Meeting Room (EP-30), or custom ERP actions with that note (Option X).
 *  Tier 1: submit a computed option for approval (EP-21, as on the Recommendations tab), or custom ERP actions without a note. */
export function DecisionForm({ data }: { data: MeetingRoom }) {
  const { me } = useAuth();
  const executive = data.escalation.tier === 2;
  const recommendations = useRecommendations(data.case_id);
  const decisionMutation = usePostDecision(data.case_id);
  const submitMutation = useSubmitRecommendation(data.case_id);
  const customMutation = usePostCustomDecision(data.case_id);
  const customAllowed = Boolean(me?.feature_flags?.erp_execution);
  const approvals = useApprovals(customAllowed && me?.role === 'SCM_MANAGER');
  const [selectedOption, setSelectedOption] = useState<DecisionOption | null | undefined>();
  const [budgetApproved, setBudgetApproved] = useState(false);
  const [approvedBy, setApprovedBy] = useState<ParticipantRole[]>(data.escalation.required_approvers);
  const [drafts, setDrafts] = useState<Draft[]>([newDraft(1)]);
  const [nextId, setNextId] = useState(2);
  const [lastResult, setLastResult] = useState<CustomDecisionResult | null>(null);
  const [showErrors, setShowErrors] = useState(false); // set by a save with incomplete custom actions
  const isCustom = selectedOption === CUSTOM;
  const catalog = useCustomActionCatalog(data.case_id, customAllowed && isCustom);
  const requiredApprovers = data.escalation.required_approvers;
  const participants = data.escalation.meeting?.participants ?? data.escalation.participants;
  const needsBudgetApproval = executive && data.escalation.reasons.some((reason) => reason.code === 'W1');
  const availableOptions = recommendations.data?.options.filter((option) => option.available && !option.pending_confirmation) ?? [];
  const meetingKind = executive ? 'EXECUTIVE_MEETING_ROOM' : 'MEETING_ROOM';
  const meetingId = data.escalation.meeting?.kind === meetingKind
    ? data.escalation.meeting.meeting_id
    : data.meetings.find((meeting) => meeting.kind === meetingKind)?.meeting_id;
  const missingApprovers = requiredApprovers.filter((role) => !approvedBy.includes(role));

  useEffect(() => {
    if (!recommendations.data || selectedOption !== undefined) return;
    const recommended = recommendations.data.recommended_option;
    const defaultOption = availableOptions.find((option) => option.option === recommended) ?? availableOptions[0];
    setSelectedOption(defaultOption?.option ?? (executive ? null : customAllowed ? CUSTOM : null));
  }, [availableOptions, customAllowed, executive, recommendations.data, selectedOption]);

  if (me?.role !== 'SCM_MANAGER' || (data.escalation.tier !== 1 && data.escalation.tier !== 2)) return null;
  // Tier 1 has no decision note; both of its paths are ERP execution (F-12), so the form needs that flag.
  if (!executive && (!customAllowed || CLOSED_STATUSES.includes(data.status))) return null;
  if (executive && !data.escalation.decision_required) {
    // The page shows "Decision recorded" itself; the results of custom actions stay visible after they were executed.
    return lastResult ? (
      <section className="meeting-room__decision decision-form card" aria-label="Custom ERP actions of the meeting decision">
        <CustomResults result={lastResult} />
      </section>
    ) : null;
  }

  const decision = executive ? decisionMutation.data ?? data.escalation.decision : null;
  const activeError = isCustom ? customMutation.error : executive ? decisionMutation.error : submitMutation.error;
  const errorDetails = activeError instanceof ApiError ? activeError.details : undefined;
  const serverIndex = isCustom && typeof errorDetails?.index === 'number' ? errorDetails.index : null;
  const details = formatErrorDetails(errorDetails);
  const optionsReady = recommendations.isSuccess;
  const recommended = recommendations.data?.recommended_option ?? null;
  const catalogReady = catalog.isSuccess && catalog.data;
  const problems = catalogReady ? draftProblems(drafts, catalog.data as CustomActionCatalog) : [];
  const draftsValid = Boolean(catalogReady) && problems.every((problem) => problem === null);
  // Custom actions: the button stays enabled while fields are empty, so a click can say what is missing (alert below).
  const chosenOption = availableOptions.find((option) => option.option === selectedOption);
  const optionValid = isCustom
    ? Boolean(catalogReady)
    : !executive
      ? Boolean(chosenOption && chosenOption.actions.length > 0)
      : availableOptions.length === 0
        ? selectedOption === null
        : Boolean(chosenOption);
  const showApproval = executive || isCustom;
  // ERP actions (custom, or a tier-1 submission) follow the case status machine (PRD 8.3), like EP-21.
  const statusBlocks = (isCustom || !executive) && !EXECUTABLE_STATUSES.includes(data.status);
  const pending = decisionMutation.isPending || customMutation.isPending || submitMutation.isPending;
  const canSave = optionsReady && optionValid && !pending && !statusBlocks
    && (!showApproval || (Boolean(meetingId) && missingApprovers.length === 0 && (!needsBudgetApproval || budgetApproved)));

  function toggleApprover(role: ParticipantRole) {
    setApprovedBy((current) => current.includes(role) ? current.filter((item) => item !== role) : [...current, role]);
  }

  function changeDraft(id: number, patch: Partial<Draft>) {
    customMutation.reset();
    setDrafts((current) => current.map((draft) => draft.id === id ? { ...draft, ...patch, id } : draft));
  }

  function addDraft() {
    setDrafts((current) => [...current, newDraft(nextId)]);
    setNextId((current) => current + 1);
  }

  function removeDraft(id: number) {
    customMutation.reset();
    setDrafts((current) => current.filter((draft) => draft.id !== id));
  }

  function chooseOption(option: DecisionOption | null) {
    submitMutation.reset();
    setSelectedOption(option);
  }

  function saveDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isCustom && catalogReady && !draftsValid) {
      setShowErrors(true);
      return;
    }
    if (!canSave) return;
    const form = event.currentTarget;
    const notes = (form.elements.namedItem('decision-notes') as HTMLTextAreaElement | null)?.value.trim();
    const approved = participants.filter((role) => approvedBy.includes(role));
    if (isCustom && catalog.data && meetingId) {
      customMutation.mutate({
        meeting_id: meetingId,
        budget_approved: budgetApproved,
        approved_by: approved,
        ...(notes ? { notes } : {}),
        actions: drafts.map((draft) => draftRequest(draft, catalog.data as CustomActionCatalog)),
      }, {
        onSuccess: (result) => {
          setLastResult(result);
          setShowErrors(false);
          if (executive) return;
          // Tier 1: the form stays for a next decision; start again with one empty action.
          setDrafts([newDraft(nextId)]);
          setNextId((current) => current + 1);
          setBudgetApproved(false);
          const textarea = form.elements.namedItem('decision-notes') as HTMLTextAreaElement | null;
          if (textarea) textarea.value = '';
        },
      });
      return;
    }
    if (!executive) {
      if (chosenOption) submitMutation.mutate(chosenOption.option);
      return;
    }
    if (!meetingId) return;
    const request: DecisionRequest = {
      meeting_id: meetingId,
      option: availableOptions.length === 0 ? null : (selectedOption as Option | null | undefined) ?? null,
      budget_approved: budgetApproved,
      approved_by: approved,
    };
    if (notes) request.notes = notes;
    decisionMutation.mutate(request);
  }

  // EP-21 and the custom actions both replace the actions of the case that still wait for a decision (PROPOSED or FAILED).
  const waiting = waitingByOption(approvals.data ?? [], data.case_id);
  const replacesWaiting = waiting.length > 0 && (isCustom || (!executive && Boolean(chosenOption)));

  const submitLabel = isCustom
    ? executive ? 'Save decision and execute' : 'Save and execute'
    : executive ? 'Save decision' : 'Submit for approval';

  return (
    <section className="meeting-room__decision decision-form card" aria-labelledby="meeting-room-decision-title">
      <header>
        <div>
          <p className="meeting-room__eyebrow">{executive ? 'Executive Meeting Room' : 'Meeting Room'}</p>
          <h2 id="meeting-room-decision-title">{executive ? 'Record meeting decision' : 'Meeting outcome'}</h2>
          <p className="decision-form__intro">
            {executive
              ? 'Choose the option the meeting agreed on, or define the ERP actions yourself.'
              : 'Submit the option the meeting agreed on for approval, or define the ERP actions yourself.'}
          </p>
        </div>
        <Badge variant="SIMULATED">SIMULATED</Badge>
      </header>

      {decision && <p className="meeting-room__decision-success" role="status">{decisionText(decision.option, decision.approved_by)}</p>}
      {!executive && lastResult && <CustomResults result={lastResult} />}
      {!executive && submitMutation.data && (
        <p className="meeting-room__decision-success" role="status">
          Option {submitMutation.data.option} was submitted for approval ({submitMutation.data.actions.length} ERP action(s)).{' '}
          <Link to="/approvals">Open Approvals</Link>
        </p>
      )}
      {recommendations.isPending && <p role="status">Loading available options…</p>}
      {recommendations.isError && <p role="alert">Recommendation options could not be loaded.</p>}

      {!decision && (
        <form className="meeting-room__decision-form" onSubmit={saveDecision}>
          <fieldset className="decision-form__step" disabled={!optionsReady}>
            <legend><span className="decision-form__step-number">1</span>Decision</legend>
            <div className="decision-form__options" role="radiogroup" aria-label="Option">
              {availableOptions.map((option) => (
                <OptionCard key={option.option} option={option} recommended={option.option === recommended}
                  checked={selectedOption === option.option} onSelect={() => chooseOption(option.option)} />
              ))}
              {executive && availableOptions.length === 0 && optionsReady && (
                <label className={`decision-option${selectedOption === null ? ' decision-option--checked' : ''}`}>
                  <input type="radio" name="decision-option" value="" checked={selectedOption === null} onChange={() => chooseOption(null)} />
                  <span className="decision-option__body">
                    <span className="decision-option__title">No option</span>
                    <span className="decision-option__label">No computed option can be chosen for this case.</span>
                  </span>
                </label>
              )}
              {customAllowed && <CustomOptionCard checked={isCustom} onSelect={() => chooseOption(CUSTOM)} />}
            </div>
            {!executive && !isCustom && chosenOption && (
              <p className="decision-form__muted decision-form__note">
                {chosenOption.actions.length > 0
                  ? 'The actions of this option go to the Approvals page, where the SCM Manager approves them before they reach the ERP.'
                  : `Option ${chosenOption.option} has no ERP actions.`}
              </p>
            )}
          </fieldset>

          {isCustom && (
            <div className="decision-form__step decision-form__step--plain">
              {catalog.isPending && <p role="status">Loading ERP objects…</p>}
              {catalog.isError && (
                <div className="meeting-room__validation" role="alert">
                  <p>ERP objects could not be loaded.</p>
                  <Button variant="secondary" onClick={() => void catalog.refetch()}>Try again</Button>
                </div>
              )}
              {catalog.data && (
                <CustomActionsBuilder catalog={catalog.data} drafts={drafts} serverIndex={serverIndex} showErrors={showErrors}
                  serverMessage={serverIndex !== null && activeError instanceof Error ? activeError.message : null}
                  onChange={changeDraft} onAdd={addDraft} onRemove={removeDraft} />
              )}
            </div>
          )}

          {showApproval && (
            <fieldset className="decision-form__step">
              <legend><span className="decision-form__step-number">{isCustom ? 3 : 2}</span>Approval</legend>
              <div className="meeting-room__approvers">
                <p className="decision-form__label">Approved by</p>
                <div className="meeting-room__checkbox-list">
                  {participants.map((role) => {
                    const required = requiredApprovers.includes(role);
                    return (
                      <label key={role} className={`decision-chip${approvedBy.includes(role) ? ' decision-chip--on' : ''}`}>
                        <input type="checkbox" checked={approvedBy.includes(role)} onChange={() => toggleApprover(role)} />
                        <span>{participantLabel(role)}{required ? ' (required)' : ''}</span>
                      </label>
                    );
                  })}
                </div>
              </div>

              <div className="meeting-room__field meeting-room__field--budget">
                <label className={`decision-chip${budgetApproved ? ' decision-chip--on' : ''}`}>
                  <input type="checkbox" checked={budgetApproved} onChange={(event) => setBudgetApproved(event.target.checked)} />
                  <span>Budget approved</span>
                </label>
                {needsBudgetApproval && <p>Required because the escalation includes reason W1.</p>}
                {isCustom && !needsBudgetApproval && (
                  <p className="decision-form__muted">Required when the extra cost of the custom actions is above the SCM Manager approval limit.</p>
                )}
              </div>

              <label className="meeting-room__field">
                <span>Notes</span>
                <textarea name="decision-notes" rows={3} maxLength={1000} placeholder="Minutes of the meeting (optional)" />
              </label>
            </fieldset>
          )}

          {showApproval && missingApprovers.length > 0 && (
            <p className="meeting-room__validation" role="alert">Missing approver: {missingApprovers.map(participantLabel).join(', ')}</p>
          )}
          {needsBudgetApproval && !budgetApproved && (
            <p className="meeting-room__validation" role="alert">Budget approval is required for reason W1.</p>
          )}
          {showApproval && !meetingId && (
            <p className="meeting-room__validation" role="alert">
              {executive
                ? 'An Executive Meeting Room meeting is required before recording a decision.'
                : 'A Meeting Room meeting is required before custom ERP actions can be saved.'}
            </p>
          )}
          {statusBlocks && (
            <p className="meeting-room__validation" role="alert">
              The case is {statusText(data.status)}: ERP actions can be saved once it is ready for decision.
            </p>
          )}
          {isCustom && showErrors && !draftsValid && (
            <div className="meeting-room__validation decision-form__error-list" role="alert">
              <p>Complete the custom ERP actions before saving:</p>
              <ul>
                {problems.map((problem, index) => problem && (
                  <li key={drafts[index].id}>Action {index + 1}: {problem.text}</li>
                ))}
              </ul>
            </div>
          )}
          {activeError && serverIndex === null && (
            <div className="meeting-room__validation" role="alert">
              <p>{activeError instanceof Error ? activeError.message : 'Decision could not be saved.'}</p>
              {details && <p>Details: {details}</p>}
            </div>
          )}

          <div className="decision-form__submit">
            {replacesWaiting && (
              <p className="decision-form__warning decision-form__warning--replace" role="alert">
                {waiting.map(({ option, count }) => `${count} action(s) of ${decisionOptionLabel(option)}`).join(' and ')}{' '}
                {waiting.reduce((sum, item) => sum + item.count, 0) === 1 ? 'is' : 'are'} waiting in Approvals. Saving replaces them:
                they become Rejected and are not sent to the ERP. <Link to="/approvals">Open Approvals</Link>
              </p>
            )}
            {isCustom && (
              <p className="decision-form__warning">
                {executive ? 'Saving records the decision and executes' : 'Saving executes'} {drafts.length} action(s) in Mock SAP S/4HANA right away.
              </p>
            )}
            <Button type="submit" variant="primary" disabled={!canSave} loading={pending}>{submitLabel}</Button>
          </div>
        </form>
      )}
    </section>
  );
}
