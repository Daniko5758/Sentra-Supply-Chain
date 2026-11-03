import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import type { FeatureFlags, Role } from '../api/types';
import { Icon, type IconName } from './Icon';

export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** Menu hanya tampil bila flag ini menyala di GET /api/me. */
  flag?: keyof FeatureFlags;
  /** Menu aksi sensitif hanya muncul untuk peran yang berhak. */
  roles?: Role[];
}

// Urutan sesuai PRD bagian 14; menu opsional (F-10, F-12) di belakang flag.
export const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
  { to: '/schedule', label: 'Schedule', icon: 'schedule' },
  { to: '/signals', label: 'Risk signals', icon: 'signal' },
  { to: '/heatmap', label: 'Risk heatmap', icon: 'globe' },
  { to: '/network', label: 'Supplier network', icon: 'network' },
  { to: '/cases', label: 'Cases', icon: 'cases' },
  { to: '/supplier-portal', label: 'Supplier portal', icon: 'inbox' },
  { to: '/whatif', label: 'What-if', icon: 'whatif', flag: 'whatif' },
  { to: '/approvals', label: 'Approvals', icon: 'approve', flag: 'erp_execution', roles: ['SCM_MANAGER'] },
  { to: '/erp-log', label: 'ERP log', icon: 'log', flag: 'erp_execution' },
  { to: '/system-status', label: 'System status', icon: 'status' },
];

export function visibleNavItems(flags: FeatureFlags | undefined, role: Role | undefined): NavItem[] {
  return NAV_ITEMS.filter(
    (item) =>
      (!item.flag || flags?.[item.flag] === true) &&
      (!item.roles || (role !== undefined && item.roles.includes(role))),
  );
}

interface SidebarProps {
  featureFlags: FeatureFlags | undefined;
  role: Role | undefined;
}

export function Sidebar({ featureFlags, role }: SidebarProps) {
  const listRef = useRef<HTMLUListElement>(null);
  const { pathname } = useLocation();
  const [indicator, setIndicator] = useState<{ top: number; height: number } | null>(null);
  const items = visibleNavItems(featureFlags, role);

  // Penanda aktif meluncur ke menu yang dipilih (animation.md §4.14).
  useLayoutEffect(() => {
    // Ukur <li> (posisinya relatif terhadap <ul>), bukan <a> yang offsetParent-nya <li>.
    const active = listRef.current?.querySelector<HTMLElement>('a.active')?.closest('li');
    setIndicator(active ? { top: active.offsetTop, height: active.offsetHeight - 2 } : null);
  }, [pathname, items.length]);

  return (
    <nav className="app-sidebar" aria-label="Main navigation">
      <p className="eyebrow sidebar-eyebrow">Workspace</p>
      <ul className="nav-list" ref={listRef}>
        {indicator && (
          <span
            className="nav-indicator"
            aria-hidden="true"
            style={{ transform: `translateY(${indicator.top}px)`, height: indicator.height }}
          />
        )}
        {items.map((item, i) => (
          <li key={item.to} className="enter" style={{ '--i': i } as CSSProperties}>
            <NavLink to={item.to} end={item.to === '/dashboard'} className="nav-link">
              <span className="nav-link__label">
                <Icon name={item.icon} className="nav-link__icon" />
                {item.label}
              </span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
