import type { CaseSite, Impact } from '../../api/types';

export function ActiveBackupSites({ sites }: { sites: CaseSite[] }) {
  const activeSites = sites.filter((site) => site.backup_ramp_confirmed !== null);
  if (!activeSites.length) return null;

  return (
    <section className="case-backup-sites card" aria-label="Active backup sites">
      {activeSites.map((site) => (
        <p key={site.site_id}>
          Backup site active: {site.site_id} → {site.backup_site ?? 'Not specified'}, ready in {site.backup_ramp_confirmed} days
        </p>
      ))}
    </section>
  );
}

export function CountryDisruptionSummary({ disruption }: { disruption: Impact['disruption'] }) {
  if (!disruption) return null;

  return (
    <section className="case-country-disruption card" aria-label="Country disruption">
      <p>Country disruption: {disruption.country} · {disruption.category} · Stated downtime: {disruption.downtime_days} days · Sites: {disruption.sites.join(', ')}</p>
    </section>
  );
}
