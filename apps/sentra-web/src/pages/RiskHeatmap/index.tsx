import { useMemo, useRef, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import type { GeoJsonObject } from 'geojson';
import { ComposableMap, Geographies, Geography, Graticule, Marker, Sphere } from 'react-simple-maps';
import topology from '../../assets/countries-110m.json';
import { useHeatmapCountries } from '../../api/hooks';
import type { HeatmapCountry, Level } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { topoIdFor } from '../../lib/countryCodes';
import { formatIDRShort, formatPercent, formatScore } from '../../lib/format';
import { labelAttributes, placeLabels } from '../../lib/labelPlacement';
import { centroid, decodeCountries, type CountryShape, type Topology } from '../../lib/topo';
import { useScrollReveal } from '../../lib/useScrollReveal';
import './risk-heatmap.css';

type ColorMode = 'risk' | 'exposure';

// Geografi (TopoJSON lokal) didekode sekali; dipakai untuk menaruh penanda di titik tengah negara.
const SHAPES: CountryShape[] = decodeCountries(topology as unknown as Topology);

const RISK_COLORS = {
  high: '#c83e4d',
  medium: '#e28a2b',
  low: '#626c7a',
  noCase: '#9bc7ee',
  noSupplier: '#e5eaf0',
};

const EXPOSURE_COLORS: Record<1 | 2 | 3 | 4, string> = {
  1: '#d5e8fa',
  2: '#91c2eb',
  3: '#438ecb',
  4: '#145a91',
};

const EXPOSURE_MARKER_COLORS: Record<1 | 2 | 3 | 4, string> = {
  1: '#5b9bd1',
  2: '#2f78b7',
  3: '#145a91',
  4: '#0b3d6b',
};

const LEVEL_RANK: Record<Level, number> = { HIGH: 3, MEDIUM: 2, LOW: 1 };

const LEGEND_ITEMS: Record<ColorMode, { color: string; label: string; key: string }[]> = {
  risk: [
    { key: 'high', color: RISK_COLORS.high, label: 'HIGH' },
    { key: 'medium', color: RISK_COLORS.medium, label: 'MEDIUM' },
    { key: 'low', color: RISK_COLORS.low, label: 'LOW' },
    { key: 'no-case', color: RISK_COLORS.noCase, label: 'No open case' },
    { key: 'no-supplier', color: RISK_COLORS.noSupplier, label: 'No supplier' },
  ],
  exposure: [
    { key: 'tier-1', color: EXPOSURE_COLORS[1], label: 'Tier 1 · < 5%' },
    { key: 'tier-2', color: EXPOSURE_COLORS[2], label: 'Tier 2 · 5–< 15%' },
    { key: 'tier-3', color: EXPOSURE_COLORS[3], label: 'Tier 3 · 15–< 40%' },
    { key: 'tier-4', color: EXPOSURE_COLORS[4], label: 'Tier 4 · ≥ 40%' },
    { key: 'no-supplier', color: RISK_COLORS.noSupplier, label: 'No supplier' },
  ],
};

function countryForTopoId(id: string | number | undefined, countries: Map<string, HeatmapCountry>) {
  return id === undefined ? undefined : countries.get(String(id));
}

function fillForCountry(country: HeatmapCountry | undefined, mode: ColorMode): string {
  if (!country) return RISK_COLORS.noSupplier;
  if (mode === 'exposure') return EXPOSURE_COLORS[country.exposure_tier];

  switch (country.highest_level) {
    case 'HIGH': return RISK_COLORS.high;
    case 'MEDIUM': return RISK_COLORS.medium;
    case 'LOW': return RISK_COLORS.low;
    default: return RISK_COLORS.noCase;
  }
}

function markerColor(country: HeatmapCountry, mode: ColorMode): string {
  if (mode === 'exposure') return EXPOSURE_MARKER_COLORS[country.exposure_tier];
  switch (country.highest_level) {
    case 'HIGH': return RISK_COLORS.high;
    case 'MEDIUM': return RISK_COLORS.medium;
    case 'LOW': return RISK_COLORS.low;
    default: return '#2f78b7';
  }
}

function highestCaseScore(country: HeatmapCountry): number | null {
  if (!country.open_cases.length) return null;
  return Math.max(...country.open_cases.map((item) => item.score));
}

/** "HIGH 79.7" atau "No open case" — teks yang menyertai warna level (tidak pernah warna saja). */
function riskText(country: HeatmapCountry): string {
  if (!country.highest_level) return 'No open case';
  const score = highestCaseScore(country);
  return score === null ? country.highest_level : `${country.highest_level} ${formatScore(score)}`;
}

/** Teks label penanda di peta: "Indonesia · HIGH 79.7" (mode risiko) atau "Indonesia · 82.9%" (mode eksposur). */
function markerCaption(country: HeatmapCountry, mode: ColorMode): string {
  return `${country.name} · ${mode === 'risk' ? riskText(country) : formatPercent(country.share)}`;
}

const order = (i: number) => ({ '--reveal-i': i }) as CSSProperties;

function HeatmapLegend({ mode }: { mode: ColorMode }) {
  return (
    <div className="heatmap-legend" aria-label={mode === 'risk' ? 'Risk level legend' : 'PO exposure legend'}>
      {LEGEND_ITEMS[mode].map((item) => (
        <span className="heatmap-legend__item" key={item.key}>
          <i className="heatmap-legend__swatch" style={{ backgroundColor: item.color }} aria-hidden="true" />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function CountryTooltip({ country }: { country: HeatmapCountry }) {
  const score = highestCaseScore(country);
  return (
    <div className="heatmap-tooltip" role="status" aria-live="polite">
      <strong>{country.name}</strong>
      <span>Sites (T1/T2): {country.sites_t1} / {country.sites_t2}</span>
      <span>PO exposure: {formatIDRShort(country.po_exposure)}</span>
      <span>Share: {formatPercent(country.share)}</span>
      <span>Open cases: {country.open_cases.length}</span>
      <span>
        Highest level:{' '}
        {country.highest_level ? `${country.highest_level}${score === null ? '' : ` · ${formatScore(score)}`}` : 'No open case'}
      </span>
    </div>
  );
}

/** Ringkasan di hero: semua angka dari EP-16; UI hanya memformat dan menghitung jumlah untuk tampilan. */
function HeatmapKpis({ totalPoExposure, countries }: { totalPoExposure: number | null; countries: HeatmapCountry[] | null }) {
  const withCases = countries?.filter((country) => country.open_cases.length > 0).length ?? null;
  const top = countries
    ?.map((country) => country.highest_level)
    .filter((level): level is Level => level !== null)
    .sort((a, b) => LEVEL_RANK[b] - LEVEL_RANK[a])[0] ?? null;

  return (
    <ul className="heatmap-kpis" aria-label="Exposure summary">
      <li
        className="heatmap-kpi heatmap-kpi--primary"
        title="Value of all open purchase order lines (quantity × net price) to suppliers in these countries, whether or not a risk case affects them. This is not the Dashboard's Revenue at risk."
      >
        <span className="heatmap-kpi__label">Open PO exposure</span>
        <strong className="heatmap-kpi__value">{totalPoExposure === null ? '—' : formatIDRShort(totalPoExposure)}</strong>
        <span className="heatmap-kpi__hint">All open PO lines, at risk or not</span>
      </li>
      <li className="heatmap-kpi">
        <span className="heatmap-kpi__label">Supplier countries</span>
        <strong className="heatmap-kpi__value">{countries ? countries.length : '—'}</strong>
        <span className="heatmap-kpi__hint">With active supplier sites</span>
      </li>
      <li className="heatmap-kpi">
        <span className="heatmap-kpi__label">Countries with open cases</span>
        <strong className="heatmap-kpi__value">{withCases ?? '—'}</strong>
        <span className="heatmap-kpi__hint">Need attention</span>
      </li>
      <li className="heatmap-kpi">
        <span className="heatmap-kpi__label">Highest open level</span>
        <strong className="heatmap-kpi__value heatmap-kpi__value--badge">
          {countries ? (top ? <Badge variant={top}>{top}</Badge> : <Badge variant="NEUTRAL">None</Badge>) : '—'}
        </strong>
        <span className="heatmap-kpi__hint">Across open cases</span>
      </li>
    </ul>
  );
}

/** Ranking negara (selalu terlihat saat tidak ada negara yang dipilih): urut share, batang = share dari API. */
function CountryRanking({ countries, onSelect }: { countries: HeatmapCountry[]; onSelect: (country: HeatmapCountry) => void }) {
  const ranked = [...countries].sort((a, b) => b.share - a.share);
  return (
    <aside className="heatmap-ranking card" aria-label="Countries by exposure">
      <div className="heatmap-ranking__header">
        <p className="heatmap-details__eyebrow">Supplier footprint</p>
        <h2>Countries by exposure</h2>
      </div>
      <ol className="heatmap-ranking__list">
        {ranked.map((country, index) => (
          <li key={country.country} data-reveal="right" style={order(index + 1)}>
            <button type="button" className="heatmap-ranking__item" onClick={() => onSelect(country)} aria-label={`Show ${country.name} details`}>
              <span className="heatmap-ranking__rank" aria-hidden="true">{index + 1}</span>
              <span className="heatmap-ranking__main">
                <span className="heatmap-ranking__name">{country.name}</span>
                <span className="heatmap-ranking__meta">
                  {formatIDRShort(country.po_exposure)} · {country.sites_t1}/{country.sites_t2} sites
                </span>
              </span>
              {country.highest_level ? (
                <Badge variant={country.highest_level}>{riskText(country)}</Badge>
              ) : (
                <Badge variant="NEUTRAL">No open case</Badge>
              )}
              <span className="heatmap-ranking__bar" aria-hidden="true">
                <span style={{ width: `${country.share}%`, background: fillForCountry(country, 'risk') }} />
              </span>
              <span className="heatmap-ranking__share">{formatPercent(country.share)}</span>
            </button>
          </li>
        ))}
      </ol>
      <p className="heatmap-ranking__hint">Select a country, here or on the map, to view its supplier sites and open cases.</p>
    </aside>
  );
}

function CountryDetails({ country, onClose }: { country: HeatmapCountry; onClose: () => void }) {
  return (
    <aside className="heatmap-details card" aria-label={`${country.name} details`}>
      <div className="heatmap-details__header">
        <div>
          <p className="heatmap-details__eyebrow">Country details</p>
          <h2>{country.name} <span>({country.country})</span></h2>
        </div>
        <button type="button" className="heatmap-details__close" onClick={onClose} aria-label="Close country details">
          ×
        </button>
      </div>

      <div className="heatmap-details__summary">
        <div><span>Sites (T1/T2)</span><strong>{country.sites_t1} / {country.sites_t2}</strong></div>
        <div><span>PO exposure</span><strong>{formatIDRShort(country.po_exposure)}</strong></div>
        <div><span>Share</span><strong>{formatPercent(country.share)}</strong></div>
        <div><span>Open cases</span><strong>{country.open_cases.length}</strong></div>
      </div>

      <section className="heatmap-details__section" aria-labelledby="heatmap-sites-heading">
        <div className="heatmap-details__section-heading">
          <h3 id="heatmap-sites-heading">Supplier sites</h3>
          <Link to={`/network?country=${encodeURIComponent(country.country)}`}>
            View in Supplier network <span aria-hidden="true">→</span>
          </Link>
        </div>
        {country.sites.length ? (
          <ul className="heatmap-site-list">
            {country.sites.map((site) => (
              <li key={site.site_id}>
                <Link to={`/network?country=${encodeURIComponent(country.country)}&search=${encodeURIComponent(site.site_id)}`}>
                  <span className="heatmap-site-list__id">{site.site_id}</span>
                  <span className="heatmap-site-list__name">{site.supplier_name}</span>
                  <span className="heatmap-site-list__meta">Tier {site.tier} · {site.kabupaten}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : <p className="heatmap-empty">No supplier site details are available.</p>}
      </section>

      <section className="heatmap-details__section" aria-labelledby="heatmap-cases-heading">
        <h3 id="heatmap-cases-heading">Open cases</h3>
        {country.open_cases.length ? (
          <ul className="heatmap-case-list">
            {country.open_cases.map((item) => (
              <li key={item.case_id}>
                <Link to={`/cases/${encodeURIComponent(item.case_id)}`}>
                  <span className="heatmap-case-list__title">{item.title}</span>
                  <span className="heatmap-case-list__meta">
                    <Badge variant={item.level}>{item.level}</Badge>
                    <span>Score {formatScore(item.score)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : <p className="heatmap-empty">No open cases.</p>}
      </section>
    </aside>
  );
}

function HeatmapTable({ countries, onSelect }: { countries: HeatmapCountry[]; onSelect: (country: HeatmapCountry) => void }) {
  return (
    <section className="heatmap-table-panel card" aria-labelledby="heatmap-table-heading">
      <div className="heatmap-table-panel__heading">
        <div>
          <p className="heatmap-details__eyebrow">Supplier footprint</p>
          <h2 id="heatmap-table-heading">Country exposure</h2>
        </div>
        <p>{countries.length} countries with supplier sites</p>
      </div>
      <div className="heatmap-table-scroll" role="region" aria-label="Country exposure table" tabIndex={0}>
        <table className="heatmap-table">
          <thead>
            <tr>
              <th scope="col">Country</th>
              <th scope="col">Sites (T1/T2)</th>
              <th scope="col">Open PO lines</th>
              <th scope="col">PO exposure</th>
              <th scope="col">Share</th>
              <th scope="col">Open cases</th>
              <th scope="col">Highest level</th>
            </tr>
          </thead>
          <tbody>
            {countries.map((country) => (
              <tr key={country.country}>
                <td>
                  <button
                    type="button"
                    className="heatmap-table__country"
                    onClick={() => onSelect(country)}
                    aria-label={`Show ${country.name} details`}
                  >
                    <span>{country.name}</span><small>{country.country}</small>
                  </button>
                </td>
                <td className="heatmap-table__number">{country.sites_t1} / {country.sites_t2}</td>
                <td className="heatmap-table__number">{country.open_po_lines.toLocaleString('en-US')}</td>
                <td className="heatmap-table__number">{formatIDRShort(country.po_exposure)}</td>
                <td className="heatmap-table__number heatmap-table__share">
                  <span>{formatPercent(country.share)}</span>
                  <i aria-hidden="true"><b style={{ width: `${country.share}%` }} /></i>
                </td>
                <td className="heatmap-table__number">{country.open_cases.length}</td>
                <td>
                  {country.highest_level ? (
                    <span className="heatmap-table__level">
                      <Badge variant={country.highest_level}>{country.highest_level}</Badge>
                      {highestCaseScore(country) !== null && <span>{formatScore(highestCaseScore(country)!)}</span>}
                    </span>
                  ) : <span className="heatmap-table__no-case">No open case</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function RiskHeatmapPage() {
  const heatmap = useHeatmapCountries();
  const pageRef = useRef<HTMLElement>(null);
  const [colorMode, setColorMode] = useState<ColorMode>('risk');
  const [selectedCountryCode, setSelectedCountryCode] = useState<string | null>(null);
  const [hoveredCountryCode, setHoveredCountryCode] = useState<string | null>(null);

  // Kartu dan baris muncul saat di-scroll; elemen baru diamati ulang setelah data dimuat.
  useScrollReveal(pageRef, [heatmap.data, selectedCountryCode]);

  const countriesByTopoId = useMemo(() => {
    const entries = heatmap.data?.countries.flatMap((country) => {
      const topoId = topoIdFor(country.country, country.name, SHAPES);
      return topoId ? [[topoId, country] as const] : [];
    }) ?? [];
    return new Map(entries);
  }, [heatmap.data?.countries]);

  // Penanda di titik tengah tiap negara supplier (lng/lat dari geografi lokal).
  const markers = useMemo(() => (heatmap.data?.countries ?? []).flatMap((country) => {
    const shape = SHAPES.find((item) => item.id === topoIdFor(country.country, country.name, SHAPES));
    if (!shape) return [];
    const [lng, lat] = centroid(shape);
    return [{ country, lng, lat }];
  }), [heatmap.data?.countries]);

  // Sisi label dihitung ulang saat mode warna berubah karena teksnya ikut berubah; negara dengan share terbesar diprioritaskan.
  const labelSides = useMemo(() => {
    const ranked = [...markers].sort((a, b) => b.country.share - a.country.share);
    const placements = placeLabels(ranked.map(({ country, lng, lat }) => ({
      id: country.country,
      lng,
      lat,
      text: markerCaption(country, colorMode),
    })));
    return new Map(placements.map((item) => [item.id, item.side]));
  }, [markers, colorMode]);

  const selectedCountry = heatmap.data?.countries.find((country) => country.country === selectedCountryCode) ?? null;
  const hoveredCountry = heatmap.data?.countries.find((country) => country.country === hoveredCountryCode) ?? null;
  const hasSide = Boolean(heatmap.data && heatmap.data.countries.length > 0);

  return (
    <section className="risk-heatmap-page" ref={pageRef}>
      {/* 1 · Hero: judul + ringkasan eksposur */}
      <header className="heatmap-hero hero-card hero-card--animated enter">
        <div className="heatmap-hero__intro">
          <p className="eyebrow">Supplier risk map</p>
          <div className="page-header risk-heatmap-page__header">
            <h1>Risk heatmap</h1>
            {heatmap.data?.simulated_cases_present && <Badge variant="SIMULATED" />}
          </div>
          <p className="heatmap-hero__description">Explore country-level supplier risk and open purchase order exposure.</p>
        </div>
        <HeatmapKpis
          totalPoExposure={heatmap.data?.total_po_exposure ?? null}
          countries={heatmap.data?.countries ?? null}
        />
      </header>

      {/* 2 · Mode warna */}
      <div className="heatmap-toolbar card" data-reveal>
        <div>
          <span className="heatmap-toolbar__label">Color by</span>
          <div className="heatmap-mode-toggle" role="group" aria-label="Color map by" data-mode={colorMode}>
            <span className="heatmap-mode-toggle__thumb" aria-hidden="true" />
            <button type="button" aria-pressed={colorMode === 'risk'} onClick={() => setColorMode('risk')}>Risk level</button>
            <button type="button" aria-pressed={colorMode === 'exposure'} onClick={() => setColorMode('exposure')}>PO exposure</button>
          </div>
        </div>
        {heatmap.isFetching && !heatmap.isPending && <span className="heatmap-refresh" role="status">Updating…</span>}
      </div>

      {/* 3 · Peta + ranking / detail negara */}
      <div className={`heatmap-main${hasSide ? ' heatmap-main--with-side' : ''}`}>
        <section className="heatmap-map-panel card" aria-label="World supplier risk map" data-reveal="zoom">
          <div className="heatmap-map-heading">
            <div className="heatmap-map-heading__title">
              <span className="heatmap-map-heading__icon" aria-hidden="true">◎</span>
              <div>
                <p className="heatmap-details__eyebrow">Geographic overview</p>
                <h2>Global supplier risk</h2>
              </div>
            </div>
            <span className="heatmap-map-heading__meta">{markers.length} supplier countries mapped</span>
          </div>
          <HeatmapLegend mode={colorMode} />
          {heatmap.isPending ? (
            <p className="heatmap-state" role="status">Loading country risk data…</p>
          ) : heatmap.isError || !heatmap.data ? (
            <div className="heatmap-state heatmap-state--error" role="alert">
              <p>Country risk data could not be loaded.</p>
              <Button variant="secondary" onClick={() => void heatmap.refetch()}>Try again</Button>
            </div>
          ) : (
            <>
              <div className="heatmap-map-canvas">
                <ComposableMap
                  projection="geoEqualEarth"
                  projectionConfig={{ scale: 155 }}
                  width={960}
                  height={500}
                  role="group"
                  aria-label="World map. Select a colored country for details."
                >
                  <defs>
                    <radialGradient id="heatmap-ocean" cx="50%" cy="45%" r="75%">
                      <stop offset="0%" stopColor="#ffffff" />
                      <stop offset="65%" stopColor="#eef4fb" />
                      <stop offset="100%" stopColor="#dbe8f6" />
                    </radialGradient>
                  </defs>
                  <Sphere fill="url(#heatmap-ocean)" stroke="#cfdcec" strokeWidth={0.8} />
                  <Graticule stroke="#d3deeb" strokeWidth={0.35} />
                  {/* TopoJSON lokal (tanpa CDN); tipe pustaka hanya mendeklarasikan GeoJSON, runtime menerima Topology */}
                  <Geographies geography={topology as unknown as GeoJsonObject}>
                    {({ geographies }) => geographies.map((geo, index) => {
                      const country = countryForTopoId(geo.id, countriesByTopoId);
                      const selected = country?.country === selectedCountryCode;
                      const accessibleName = country
                        ? `${country.name}: ${country.highest_level ?? 'No open case'}, ${formatPercent(country.share)} PO exposure share`
                        : 'No supplier sites';
                      return (
                        <Geography
                          key={geo.rsmKey}
                          geography={geo}
                          className={`heatmap-geography${selected ? ' is-selected' : ''}${country ? ' has-supplier' : ''}`}
                          role={country ? 'button' : undefined}
                          tabIndex={country ? 0 : -1}
                          aria-label={accessibleName}
                          aria-pressed={country ? selected : undefined}
                          onMouseEnter={() => setHoveredCountryCode(country?.country ?? null)}
                          onMouseLeave={() => setHoveredCountryCode(null)}
                          onFocus={() => setHoveredCountryCode(country?.country ?? null)}
                          onBlur={() => setHoveredCountryCode(null)}
                          onClick={() => country && setSelectedCountryCode(country.country)}
                          onKeyDown={(event) => {
                            if (country && (event.key === 'Enter' || event.key === ' ')) {
                              event.preventDefault();
                              setSelectedCountryCode(country.country);
                            }
                          }}
                          style={{
                            fill: fillForCountry(country, colorMode),
                            stroke: selected ? '#14253d' : '#ffffff',
                            strokeWidth: selected ? 1.8 : 0.55,
                            outline: 'none',
                            cursor: country ? 'pointer' : 'default',
                            '--i': index,
                          } as CSSProperties}
                        />
                      );
                    })}
                  </Geographies>
                  {markers.map(({ country, lng, lat }) => {
                    const color = markerColor(country, colorMode);
                    const pulse = country.open_cases.length > 0;
                    return (
                      <Marker key={country.country} coordinates={[lng, lat]} className="heatmap-marker">
                        {pulse && <circle className="heatmap-marker__pulse" r={6} fill={color} />}
                        {pulse && <circle className="heatmap-marker__pulse heatmap-marker__pulse--late" r={6} fill={color} />}
                        <circle className="heatmap-marker__dot" r={4.5} fill={color} stroke="#ffffff" strokeWidth={1.6} />
                        <text className="heatmap-marker__label" {...labelAttributes(labelSides.get(country.country) ?? 'top')} aria-hidden="true">
                          {markerCaption(country, colorMode)}
                        </text>
                      </Marker>
                    );
                  })}
                </ComposableMap>
                {hoveredCountry && <CountryTooltip country={hoveredCountry} />}
              </div>
              <p className="heatmap-map-hint">Select a highlighted country to view supplier sites and open cases.</p>
            </>
          )}
        </section>

        {hasSide && heatmap.data && (
          selectedCountry
            ? <CountryDetails country={selectedCountry} onClose={() => setSelectedCountryCode(null)} />
            : <CountryRanking countries={heatmap.data.countries} onSelect={(country) => setSelectedCountryCode(country.country)} />
        )}
      </div>

      {/* 4 · Tabel */}
      {heatmap.data && (
        <div data-reveal>
          <HeatmapTable
            countries={heatmap.data.countries}
            onSelect={(country) => setSelectedCountryCode(country.country)}
          />
        </div>
      )}
    </section>
  );
}
