import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** Chip panah bulat di kanan (design.md §5.1). */
  arrow?: boolean;
  /** Run sedang berjalan: spinner + tombol non-aktif (animation.md §4.5). */
  loading?: boolean;
  children: ReactNode;
}

function ArrowIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </svg>
  );
}

export function Button({
  variant = 'primary',
  arrow = false,
  loading = false,
  disabled,
  className,
  type = 'button',
  children,
  ...rest
}: ButtonProps) {
  const classes = ['btn', `btn--${variant}`, arrow && !loading ? 'btn--arrow' : '', className ?? '']
    .filter(Boolean)
    .join(' ');

  return (
    <button type={type} className={classes} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading && <span className="ring-spinner" aria-hidden="true" />}
      <span>{children}</span>
      {arrow && !loading && (
        <span className="btn__arrow">
          <ArrowIcon />
        </span>
      )}
    </button>
  );
}
