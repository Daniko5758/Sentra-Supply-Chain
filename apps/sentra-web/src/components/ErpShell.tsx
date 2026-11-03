import type { ReactNode } from 'react';
import { Badge } from './Badge';
import './erp-shell.css';

interface ErpShellProps {
  /** Nama "aplikasi" di bilah judul, mis. `Approve ERP actions`. */
  title: string;
  /** Isi area toolbar (opsional), mis. jumlah aksi yang menunggu. */
  toolbar?: ReactNode;
  children: ReactNode;
}

/** C-51: bingkai gaya SAP Fiori untuk ERP log, Approvals, dan hasil eksekusi SAJA (apps/sentra-web/CLAUDE.md bagian 3).
 *  Semua gaya di erp-shell.css berawalan `.erp-shell`, jadi halaman lain tidak berubah. Tanpa logo SAP; hanya teks. */
export function ErpShell({ title, toolbar, children }: ErpShellProps) {
  return (
    <section className="erp-shell" aria-label={`${title} — SAP S/4HANA mock (SIMULATED)`}>
      <div className="erp-shell__bar">
        <span className="erp-shell__brand">SAP S/4HANA — Mock</span>
        <Badge variant="SIMULATED" />
        <span className="erp-shell__title">{title}</span>
      </div>
      {toolbar && <div className="erp-shell__toolbar">{toolbar}</div>}
      <div className="erp-shell__content">{children}</div>
    </section>
  );
}
