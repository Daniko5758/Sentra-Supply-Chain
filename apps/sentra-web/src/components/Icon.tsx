// Ikon SVG lokal (tanpa CDN). Garis 1.75px, gaya outline.
export type IconName =
  | 'dashboard'
  | 'schedule'
  | 'signal'
  | 'globe'
  | 'network'
  | 'cases'
  | 'inbox'
  | 'whatif'
  | 'approve'
  | 'log'
  | 'status'
  | 'bell'
  | 'sparkle'
  | 'arrow';

const PATHS: Record<IconName, string[]> = {
  dashboard: ['M3 3h7v9H3z', 'M14 3h7v5h-7z', 'M14 12h7v9h-7z', 'M3 16h7v5H3z'],
  schedule: ['M3 5h18v16H3z', 'M3 10h18', 'M8 3v4', 'M16 3v4', 'M7 14h3', 'M7 17h6'],
  signal: [
    'M4.9 19.1a10 10 0 0 1 0-14.2',
    'M19.1 4.9a10 10 0 0 1 0 14.2',
    'M7.8 16.2a6 6 0 0 1 0-8.4',
    'M16.2 7.8a6 6 0 0 1 0 8.4',
    'M12 10.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z',
  ],
  globe: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M3 12h18', 'M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z'],
  network: ['M5 5h4v4H5z', 'M15 5h4v4h-4z', 'M10 15h4v4h-4z', 'M7 9v2.5h10V9', 'M12 11.5V15'],
  cases: ['M3 7h18v13H3z', 'M8 7V4h8v3', 'M3 12h18'],
  inbox: ['M3 13l3-8h12l3 8v6H3z', 'M3 13h5l1.5 2.5h5L16 13h5'],
  whatif: ['M9 9a3 3 0 1 1 4.2 2.7c-.8.4-1.2 1-1.2 1.8V14', 'M12 18h.01', 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z'],
  approve: ['M20 6 9 17l-5-5'],
  log: ['M6 3h9l4 4v14H6z', 'M14 3v5h5', 'M9 12h7', 'M9 16h7'],
  status: ['M3 12h4l2-5 4 10 2-5h6'],
  bell: ['M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9', 'M13.73 21a2 2 0 0 1-3.46 0'],
  sparkle: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z', 'M19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z'],
  arrow: ['M5 12h14', 'm12 5 7 7-7 7'],
};

interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
}

export function Icon({ name, size = 18, className }: IconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
