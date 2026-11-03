import type { ReactNode } from 'react';

export type BadgeVariant = 'HIGH' | 'MEDIUM' | 'LOW' | 'SIMULATED' | 'LIVE' | 'NEUTRAL';

interface BadgeProps {
  variant: BadgeVariant;
  /** Teks badge. Default = nama varian; badge tidak pernah hanya warna. */
  children?: ReactNode;
  title?: string;
}

export function Badge({ variant, children, title }: BadgeProps) {
  const label = children ?? (variant === 'NEUTRAL' ? '-' : variant);
  return (
    <span className={`badge badge--${variant.toLowerCase()}`} title={title}>
      {label}
    </span>
  );
}
