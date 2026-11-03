// C-51: nama objek SAP dan isi perubahan aksi ERP, dipakai halaman ERP log dan Approvals.
// Hanya pemetaan label (kontrak E.2.3) dan format tampilan; tidak ada nama atau angka dari seed.
import type { ActionPayload, ActionType, ErpMessage } from '../api/types';
import { formatDateOrRaw, formatIDR, formatQuantity } from './format';

export const EMPTY_VALUE = '—';

// Jenis aksi ERP → nama tampilan (dipakai form aksi custom di Meeting Room).
export const ACTION_TYPE_LABELS: Record<ActionType, string> = {
  CHANGE_PO_DATE: 'Change PO delivery date',
  REDUCE_OR_CANCEL_PO: 'Reduce or cancel PO quantity',
  EXPEDITE_SHIPMENT: 'Expedite shipment by air',
  CREATE_PR_PO: 'Buy from approved alternate source',
  RESCHEDULE_PRODUCTION_ORDER: 'Reschedule production order',
  RESCHEDULE_SALES_ORDER: 'Reschedule sales order',
};

const ERP_MESSAGE_TYPES: Record<ErpMessage['type'], { label: string }> = {
  S: { label: 'Success' },
  W: { label: 'Warning' },
  E: { label: 'Error' },
  I: { label: 'Info' },
};

/** Pesan ERP bergaya BAPIRET2 sebagai satu baris; kalimat pesannya tampil apa adanya dari API. */
export function erpMessageText(message: ErpMessage): string {
  const type = ERP_MESSAGE_TYPES[message.type];
  const prefix = type ? type.label : message.type;
  return message.id === 'SENTRA' ? `${prefix} ${message.message}` : `${prefix} ${message.id}/${message.number} ${message.message}`;
}

// Kelas objek change document (kontrak E.2.3) → nama tampilan (teks baku C-50, playbook C 5B.2).
export const OBJECT_NAMES: Record<string, string> = {
  EINKBELEG: 'Purchase order',
  BANF: 'Purchase requisition',
  ZPRODORD: 'Production order',
  VERKBELEG: 'Sales order',
};

/** Nama objek dari `object_class`; kelas lain tampil apa adanya, kosong bila tidak ada (5B.2). */
export function objectClassName(objectClass: string | undefined): string {
  if (!objectClass) return '';
  return OBJECT_NAMES[objectClass] ?? objectClass;
}

// Jenis aksi → kelas objek yang ditulis aksi itu (kontrak E.2.3). CREATE_PR_PO dimulai dari PR (E-13).
const ACTION_OBJECT_CLASS: Record<ActionType, string> = {
  CHANGE_PO_DATE: 'EINKBELEG',
  REDUCE_OR_CANCEL_PO: 'EINKBELEG',
  EXPEDITE_SHIPMENT: 'EINKBELEG',
  CREATE_PR_PO: 'BANF',
  RESCHEDULE_PRODUCTION_ORDER: 'ZPRODORD',
  RESCHEDULE_SALES_ORDER: 'VERKBELEG',
};

export interface ObjectLabel {
  title: string;
  detail: string | null;
}

/** `object` aksi (kontrak G.3) → judul bergaya SAP. Format yang tidak dikenal tampil apa adanya (tidak pernah error). */
export function actionObjectLabel(type: ActionType, object: string): ObjectLabel {
  const raw: ObjectLabel = { title: object, detail: null };
  const name = objectClassName(ACTION_OBJECT_CLASS[type]);
  if (!name) return raw;
  if (type === 'CREATE_PR_PO') {
    return /^\S+ @ \S+$/.test(object) ? { title: `${name} (new)`, detail: object } : raw;
  }
  if (type === 'RESCHEDULE_PRODUCTION_ORDER' || type === 'RESCHEDULE_SALES_ORDER') {
    return /^\S+$/.test(object) ? { title: `${name} ${object}`, detail: null } : raw;
  }
  const line = /^(\d+)\/(\d+)$/.exec(object);
  return line ? { title: `${name} ${line[1]} / Item ${line[2]}`, detail: null } : raw;
}

export interface ChangeRow {
  label: string;
  value: string;
}

type PayloadRecord = Record<string, unknown>;

function text(p: PayloadRecord, key: string): string {
  const value = p[key];
  if (typeof value === 'string' && value) return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return EMPTY_VALUE;
}

function dateText(p: PayloadRecord, key: string): string {
  const value = p[key];
  return typeof value === 'string' && value ? formatDateOrRaw(value) : EMPTY_VALUE;
}

function quantityText(p: PayloadRecord, key: string): string {
  const value = p[key];
  return typeof value === 'number' && Number.isFinite(value) ? formatQuantity(value) : EMPTY_VALUE;
}

function idrText(p: PayloadRecord, key: string): string {
  const value = p[key];
  return typeof value === 'number' && Number.isFinite(value) ? formatIDR(value) : EMPTY_VALUE;
}

function change(oldValue: string, newValue: string): string {
  return `${oldValue} → ${newValue}`;
}

/** Isi `payload` sebuah aksi sebagai pasangan label–nilai (C-51 P2). Field yang tidak ada tampil `—`; tidak ada satuan
 *  karena payload tidak membawanya (kontrak G.3). Label sama dengan `field_label` change document (kontrak E.2.3). */
export function actionChangeRows(type: ActionType, payload: ActionPayload | null | undefined): ChangeRow[] {
  const p: PayloadRecord = payload && typeof payload === 'object' ? (payload as PayloadRecord) : {};
  switch (type) {
    case 'CHANGE_PO_DATE':
      return [{ label: 'Delivery date', value: change(dateText(p, 'old_date'), dateText(p, 'new_date')) }];
    case 'REDUCE_OR_CANCEL_PO':
      if (p.new_qty === 0) {
        // Batal = flag hapus L, qty item tidak berubah (kontrak G.3 v1.12, E-12).
        return [
          { label: 'Deletion indicator', value: change(EMPTY_VALUE, 'L') },
          { label: 'PO quantity', value: `${quantityText(p, 'old_qty')} (unchanged)` },
        ];
      }
      return [{ label: 'PO quantity', value: change(quantityText(p, 'old_qty'), quantityText(p, 'new_qty')) }];
    case 'CREATE_PR_PO': {
      const rows: ChangeRow[] = [
        { label: 'Material', value: text(p, 'material_id') },
        { label: 'Supplier', value: text(p, 'supplier_id') },
        { label: 'Quantity', value: quantityText(p, 'qty') },
        { label: 'Net price', value: idrText(p, 'price') },
        { label: 'Delivery date', value: dateText(p, 'delivery_date') },
        { label: 'Incoterms', value: text(p, 'incoterms') },
        { label: 'Shipping condition', value: text(p, 'shipping_condition') },
      ];
      if (typeof p.freight_per_unit === 'number' && p.freight_per_unit > 0) {
        rows.push({ label: 'Freight per unit', value: idrText(p, 'freight_per_unit') });
      }
      return rows;
    }
    case 'EXPEDITE_SHIPMENT':
      // Teks baku 5A.4 "Aksi expedite", persis seperti tab Recommendations (RecommendationsPanel.tsx:39).
      return [{
        label: 'Shipping',
        value: `Expedite shipment ${text(p, 'po_id')}/${text(p, 'item_no')}: ${text(p, 'old_shipping_condition')} → AIR, `
          + `${text(p, 'old_incoterms')} → FCA (${idrText(p, 'freight_cost')})`,
      }];
    case 'RESCHEDULE_PRODUCTION_ORDER':
      return [{ label: 'Basic start date', value: change(dateText(p, 'old_date'), dateText(p, 'new_date')) }];
    case 'RESCHEDULE_SALES_ORDER':
      return [{ label: 'Schedule line date', value: change(dateText(p, 'old_date'), dateText(p, 'new_date')) }];
    default:
      return [];
  }
}
