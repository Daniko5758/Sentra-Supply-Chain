import { lazy, Suspense, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../../api/client';
import { useHeatmapCountries } from '../../api/hooks';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatIDRShort, formatPercent } from '../../lib/format';
import { webglAvailable } from '../../lib/webgl';
import { GLOBE_COLORS, levelColor, riskText } from './globeColors';
import '../../styles/dashboard-globe.css';

// three.js hanya diunduh saat kartu ini tampil (chunk terpisah).
const GlobeCanvas = lazy(() => import('./GlobeCanvas'));

const MAX_GLOBE_ZOOM = 3;
const COUNTRY_FOCUS_ZOOM = MAX_GLOBE_ZOOM;

const LEGEND = [
  { label: 'HIGH', color: GLOBE_COLORS.HIGH },
  { label: 'MEDIUM', color: GLOBE_COLORS.MEDIUM },
  { label: 'LOW', color: GLOBE_COLORS.LOW },
  { label: 'No open case', color: GLOBE_COLORS.noCase },
  { label: 'No supplier', color: GLOBE_COLORS.noSupplier },
];

function GlobeLoading() {
  return (
    <div className="globe-loading" role="status">
      <span className="orbit" aria-hidden="true" />
      <span>Loading 3D view…</span>
    </div>
  );
}

/** Kartu "Global risk view": daftar negara (EP-16) + globe 3D. Daftar adalah padanan teks dari globe. */
export function GlobeSection() {
  const heatmap = useHeatmapCountries();
  const [focus, setFocus] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const [zoomLevel, setZoomLevel] = useState(0);
  // Tanpa WebGL: tampilkan teks pengganti dan jangan unduh chunk three.js sama sekali.
  const [supported] = useState(webglAvailable);
  const countries = heatmap.data?.countries.slice().sort((a, b) => b.share - a.share) ?? [];

  const focusCountry = (countryCode: string) => {
    setFocus(countryCode);
    setZoomLevel(COUNTRY_FOCUS_ZOOM);
    setFocusRequest((request) => request + 1);
  };

  return (
    <section className="dashboard-card dashboard-globe" data-reveal aria-labelledby="globe-title">
      <div className="dashboard-card__heading">
        <div>
          <p className="dashboard-eyebrow">Supplier countries · 3D</p>
          <h2 id="globe-title">Global risk view</h2>
        </div>
        <div className="dashboard-globe__heading-meta">
          {heatmap.data?.simulated_cases_present && <Badge variant="SIMULATED" />}
          <Link to="/heatmap">Open heatmap <span aria-hidden="true">→</span></Link>
        </div>
      </div>

      {heatmap.isError && (
        <div className="dashboard-error" role="alert">
          <p>{heatmap.error instanceof ApiError ? heatmap.error.message : 'The request failed. Please try again.'}</p>
          <Button variant="secondary" onClick={() => void heatmap.refetch()}>Try again</Button>
        </div>
      )}

      <div className="dashboard-globe__body">
        <div className="dashboard-globe__panel">
          {heatmap.isLoading && <p className="dashboard-empty">Loading country exposure…</p>}
          {heatmap.data && countries.length === 0 && <p className="dashboard-empty">No country exposure to show.</p>}
          <ul className="globe-country-list">
            {countries.map((country, i) => (
              <li key={country.country} data-reveal="left" style={{ '--reveal-i': i + 2 } as CSSProperties}>
                <button
                  type="button"
                  className="globe-country"
                  aria-pressed={focus === country.country}
                  onClick={() => focusCountry(country.country)}
                >
                  <span className="globe-country__swatch" style={{ background: levelColor(country.highest_level) }} aria-hidden="true" />
                  <span className="globe-country__main">
                    <span className="globe-country__name">{country.name}</span>
                    <span className="globe-country__meta">
                      {formatPercent(country.share)} share · {formatIDRShort(country.po_exposure)}
                    </span>
                  </span>
                  {country.highest_level ? (
                    <Badge variant={country.highest_level}>{riskText(country)}</Badge>
                  ) : (
                    <Badge variant="NEUTRAL">No open case</Badge>
                  )}
                </button>
              </li>
            ))}
          </ul>
          <ul className="globe-legend" aria-label="Legend">
            {LEGEND.map((item) => (
              <li key={item.label}>
                <span className="globe-legend__swatch" style={{ background: item.color }} aria-hidden="true" />
                {item.label}
              </li>
            ))}
          </ul>
          <p className="globe-hint">The globe cycles through supplier countries. Drag to rotate manually, or select a country to keep it in focus.</p>
        </div>

        <div className="dashboard-globe__stage">
          {!supported && (
            <div className="globe-fallback" role="status">
              <p>The 3D view is not available on this device.</p>
              <Link to="/heatmap">Open the Risk heatmap</Link>
            </div>
          )}
          {supported && heatmap.data && (
            <>
              <Suspense fallback={<GlobeLoading />}>
                <GlobeCanvas
                  countries={heatmap.data.countries}
                  focus={focus}
                  focusRequest={focusRequest}
                  zoomLevel={zoomLevel}
                  onFocusCountry={focusCountry}
                  onClearFocus={() => setFocus(null)}
                />
              </Suspense>
              <div className="globe-zoom-controls" role="group" aria-label="Globe zoom controls">
                <Button
                  variant="secondary"
                  className="globe-zoom-button"
                  aria-label="Zoom out"
                  title="Zoom out"
                  disabled={zoomLevel === 0}
                  onClick={() => setZoomLevel((level) => Math.max(0, level - 1))}
                >
                  −
                </Button>
                <Button
                  variant="secondary"
                  className="globe-zoom-reset"
                  aria-label="Reset zoom"
                  title="Reset zoom"
                  disabled={zoomLevel === 0}
                  onClick={() => setZoomLevel(0)}
                >
                  Reset
                </Button>
                <Button
                  variant="secondary"
                  className="globe-zoom-button"
                  aria-label="Zoom in"
                  title="Zoom in"
                  disabled={zoomLevel === MAX_GLOBE_ZOOM}
                  onClick={() => setZoomLevel((level) => Math.min(MAX_GLOBE_ZOOM, level + 1))}
                >
                  +
                </Button>
              </div>
            </>
          )}
          {supported && heatmap.isLoading && <GlobeLoading />}
        </div>
      </div>
    </section>
  );
}
