import type { CSSProperties, ReactNode } from 'react';
import { trackSpotlight } from '../lib/spotlight';
import { Badge } from './Badge';

/** Urutan stagger untuk kelas `.enter` (animation.md §4.1). */
const order = (i: number) => ({ '--i': i }) as CSSProperties;

/** Pratinjau skeleton berkilau: menandai area yang isinya dibuat di task berikutnya. Tanpa angka. */
function SkeletonPreview() {
  return (
    <div className="skeleton-preview spotlight" onMouseMove={trackSpotlight}>
      <div className="skeleton-preview__row">
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton-tile enter" style={order(3 + i)}>
            <span className="shimmer shimmer--label" />
            <span className="shimmer shimmer--value" />
            <span className="shimmer shimmer--line" />
          </div>
        ))}
      </div>
      <div className="skeleton-tile skeleton-tile--wide enter" style={order(6)}>
        <span className="shimmer shimmer--label" />
        <span className="shimmer shimmer--block" />
      </div>
      <p className="skeleton-preview__caption">This page is under construction.</p>
    </div>
  );
}

interface PlaceholderPageProps {
  title: string;
  description: string;
  simulated?: boolean;
  children?: ReactNode;
}

// Halaman kosong sementara (C-01); isi tiap halaman dibuat di task C-04..C-16.
export function PlaceholderPage({ title, description, simulated, children }: PlaceholderPageProps) {
  return (
    <section>
      <div className="page-header enter">
        <h1>{title}</h1>
        {simulated && <Badge variant="SIMULATED" />}
      </div>
      <p className="page-description enter" style={order(1)}>
        {description}
      </p>
      <div className="enter" style={order(2)}>
        {children ?? <SkeletonPreview />}
      </div>
    </section>
  );
}
