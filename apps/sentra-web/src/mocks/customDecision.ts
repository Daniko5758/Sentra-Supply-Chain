// Option X di mode mock: respons POST /api/cases/{id}/decision/custom-actions mengikuti aksi yang diisi di form.
// Aksi yang sama dengan fixture (hasil backend asli) memakai fixture apa adanya. Aksi lain dibentuk dari isi request dan nilai
// lama dari fixture catalog; tidak ada angka, dokumen, atau pesan ERP baru (document null, messages kosong).
import type {
  ActionPayload,
  ActionResult,
  CustomActionCatalog,
  CustomActionRequest,
  CustomDecisionRequest,
  CustomDecisionResult,
} from '../api/types';

function objectOf(action: CustomActionRequest): string {
  if (action.type === 'CREATE_PR_PO') return `${action.material_id} @ ${action.supplier_id}`;
  if (action.type === 'RESCHEDULE_PRODUCTION_ORDER') return String(action.order_id);
  if (action.type === 'RESCHEDULE_SALES_ORDER') return String(action.so_id);
  return `${action.po_id}/${action.item_no}`;
}

function payloadOf(action: CustomActionRequest, catalog: CustomActionCatalog): Record<string, unknown> {
  const line = catalog.po_lines.find((item) => item.po_id === action.po_id && item.item_no === action.item_no);
  switch (action.type) {
    case 'CHANGE_PO_DATE':
      return { po_id: action.po_id, item_no: action.item_no, old_date: line?.confirmed_date, new_date: action.new_date };
    case 'REDUCE_OR_CANCEL_PO':
      return { po_id: action.po_id, item_no: action.item_no, old_qty: line?.qty, new_qty: action.new_qty };
    case 'EXPEDITE_SHIPMENT':
      return {
        po_id: action.po_id, item_no: action.item_no, old_shipping_condition: line?.shipping_condition, new_shipping_condition: 'AIR',
        old_incoterms: line?.incoterms, new_incoterms: 'FCA',
      };
    case 'RESCHEDULE_PRODUCTION_ORDER': {
      const order = catalog.production_orders.find((item) => item.order_id === action.order_id);
      return { order_id: action.order_id, old_date: order?.start_date, new_date: action.new_date };
    }
    case 'RESCHEDULE_SALES_ORDER': {
      const sales = catalog.sales_orders.find((item) => item.so_id === action.so_id);
      return { so_id: action.so_id, old_date: sales?.confirmed_delivery_date, new_date: action.new_date };
    }
    default: {
      const source = catalog.alternate_sources.find((item) => item.material_id === action.material_id && item.supplier_id === action.supplier_id);
      return {
        material_id: action.material_id, supplier_id: action.supplier_id, qty: action.qty, price: source?.price,
        delivery_date: action.by_air ? source?.air_delivery_date : source?.delivery_date,
      };
    }
  }
}

function sameAction(request: CustomActionRequest, fixture: ActionResult): boolean {
  if (request.type !== fixture.type || objectOf(request) !== fixture.object) return false;
  const payload = fixture.payload as Record<string, unknown>;
  return request.new_date === undefined || payload.new_date === request.new_date;
}

export function mockCustomDecision(
  request: CustomDecisionRequest,
  fixture: CustomDecisionResult,
  catalog: CustomActionCatalog | undefined,
): CustomDecisionResult {
  // Tier 2 has a decision note; tier 1 (Meeting Room) has none, like the real API.
  const decision = fixture.decision
    ? { ...fixture.decision, approved_by: request.approved_by, budget_approved: request.budget_approved, notes: request.notes ?? null }
    : null;
  const caseId = decision?.case_id ?? fixture.actions[0]?.idempotency_key.split(':')[0] ?? '';
  const batch = decision?.decision_id ?? 'ACT-X1';
  const actions = request.actions.map((action, index): ActionResult => {
    const seq = index + 1;
    const known = fixture.actions.find((item) => sameAction(action, item));
    if (known) return { ...structuredClone(known), seq, action_id: `ACT-X${seq}`, idempotency_key: `${caseId}:X:${seq}:${batch}` };
    return {
      action_id: `ACT-X${seq}`,
      seq,
      type: action.type,
      object: objectOf(action),
      payload: (catalog ? payloadOf(action, catalog) : { ...action }) as unknown as ActionPayload,
      status: 'EXECUTED',
      idempotency_key: `${caseId}:X:${seq}:${batch}`,
      erp_result: { document: null, messages: [] },
      error: null,
    };
  });
  return { ...fixture, decision, actions };
}
