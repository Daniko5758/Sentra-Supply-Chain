// EP-10 di mode mock: alert yang dibuka di sesi ini tetap `read`, seperti `alert.read_by` di backend.
// Hilang saat reload atau Reset demo data. Tidak ada angka baru: jumlah belum dibaca dihitung dari fixture alerts.json.
import type { Alert, Dashboard } from '../api/types';
import alertsFixture from './alerts.json';

const readIds = new Set<string>();

export function resetMockAlertReads(): void {
  readIds.clear();
}

export function markMockAlertRead(alertId: string): void {
  readIds.add(alertId);
}

/** GET /api/alerts: alert yang sudah dibuka di sesi ini menjadi `read: true`. */
export function mockAlerts(alerts: Alert[]): Alert[] {
  return alerts.map((alert) => (readIds.has(alert.alert_id) ? { ...alert, read: true } : alert));
}

/** GET /api/dashboard: `unread_alerts` berkurang sebanyak alert fixture yang belum dibaca lalu dibuka di sesi ini. */
export function mockDashboard(dashboard: Dashboard): Dashboard {
  const opened = (alertsFixture as unknown as Alert[]).filter((alert) => !alert.read && readIds.has(alert.alert_id)).length;
  return { ...dashboard, unread_alerts: Math.max(0, dashboard.unread_alerts - opened) };
}
