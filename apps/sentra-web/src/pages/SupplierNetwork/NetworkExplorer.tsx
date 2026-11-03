import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useNetwork, useSupplierProfile } from '../../api/hooks';
import type { CaseSummary, Network, NetworkNode, SiteHighlight, SupplierMessage, SupplierProfile } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { bcpBandLabel } from '../../lib/c42';
import { formatDateTime, formatIDR, formatPercent } from '../../lib/format';
import {
  GRAPH_WIDTH,
  graphFrameHeight,
  highlightCounts,
  layoutNetwork,
  type LaneKey,
  type NetworkLayout,
} from './networkLayout';
import { SupplierBackupPanel, SupplierBcpPanel } from './SupplierBcpPanel';
import './network.css';

type HighlightTone = 'affected' | 'via-tier2' | 'confirmed' | 'path' | 'neutral';

type NetworkCardData = {
  entityType: NetworkNode['type'];
  title: string;
  subtitle: string;
  status: string;
  tone: HighlightTone;
  bcpScore?: number;
  bcpBand?: string;
  active: boolean;
  dim: boolean;
  laneIndex: number;
  onSelectSite: (siteId: string) => void;
};

type LaneData = {
  laneKey: LaneKey;
  step: number;
  title: string;
  caption: string;
  count: number;
  height: number;
};

type NetworkCardFlowNode = Node<NetworkCardData, 'networkCard'>;
type LaneFlowNode = Node<LaneData, 'lane'>;

type NetworkExplorerProps = {
  fixedCaseId?: string;
  version?: number;
  caseOptions?: CaseSummary[];
  caseOptionsError?: boolean;
  onRetryCaseOptions?: () => void;
  embedded?: boolean;
};

const COUNTRY_NAMES = new Intl.DisplayNames(['en'], { type: 'region' });

function countryLabel(country: string): string {
  return `${COUNTRY_NAMES.of(country) ?? country} (${country})`;
}

function siteHighlightLabel(highlight: SiteHighlight, caseSelected: boolean): { label: string; tone: HighlightTone } {
  switch (highlight) {
    case 'affected': return { label: 'Affected', tone: 'affected' };
    case 'via_tier2': return { label: 'Affected via Tier-2', tone: 'via-tier2' };
    case 'confirmed_no_impact': return { label: 'Confirmed – no impact', tone: 'confirmed' };
    default: return { label: caseSelected ? 'No impact' : 'No active case', tone: 'neutral' };
  }
}

function nodeSearchText(node: NetworkNode): string {
  if (node.type === 'site') {
    return `${node.id} ${node.label} ${node.tier} ${node.kabupaten} ${node.country}`.toLocaleLowerCase();
  }
  return `${node.id} ${node.label}`.toLocaleLowerCase();
}

function matchesSiteFilters(
  node: Extract<NetworkNode, { type: 'site' }>,
  country: string,
  location: string,
): boolean {
  return (country === 'all' || node.country === country)
    && (location === 'all' || node.kabupaten === location);
}

function filterNetworkNodes(network: Network, country: string, location: string, search: string): NetworkNode[] {
  const query = search.trim().toLocaleLowerCase();
  const filtersActive = country !== 'all' || location !== 'all' || Boolean(query);
  if (!filtersActive) return network.nodes;

  const siteNodes = network.nodes.filter((node): node is Extract<NetworkNode, { type: 'site' }> => node.type === 'site');
  const matchedSites = siteNodes.filter((node) => matchesSiteFilters(node, country, location)
    && (!query || nodeSearchText(node).includes(query)));
  const matchedOtherNodes = query
    ? network.nodes.filter((node) => node.type !== 'site' && nodeSearchText(node).includes(query))
    : [];
  const nodesById = new Map(network.nodes.map((node) => [node.id, node]));
  const visibleIds = new Set<string>();
  const queue: string[] = [];

  const add = (id: string) => {
    if (!visibleIds.has(id) && nodesById.has(id)) {
      visibleIds.add(id);
      queue.push(id);
    }
  };

  matchedSites.forEach((node) => add(node.id));
  matchedOtherNodes.forEach((node) => add(node.id));

  // Keep each matched site's downstream path visible without pulling unrelated sites in via shared products.
  while (queue.length) {
    const currentId = queue.shift()!;
    network.edges
      .filter((edge) => edge.from === currentId)
      .forEach((edge) => {
        const target = nodesById.get(edge.to);
        if (target?.type === 'site' && !matchesSiteFilters(target, country, location)) return;
        add(edge.to);
      });
  }

  // Searching for a part or product also shows its upstream supplier path.
  const upstreamQueue = [...matchedOtherNodes.map((node) => node.id)];
  const checkedUpstream = new Set<string>();
  while (upstreamQueue.length) {
    const currentId = upstreamQueue.shift()!;
    if (checkedUpstream.has(currentId)) continue;
    checkedUpstream.add(currentId);
    network.edges
      .filter((edge) => edge.to === currentId)
      .forEach((edge) => {
        const source = nodesById.get(edge.from);
        if (!source) return;
        if (source.type === 'site' && !matchesSiteFilters(source, country, location)) return;
        add(source.id);
        if (source.type !== 'site') upstreamQueue.push(source.id);
      });
  }

  return network.nodes.filter((node) => visibleIds.has(node.id));
}

function highlightForNode(node: NetworkNode, hasCase: boolean): { status: string; tone: HighlightTone } {
  if (node.type === 'site') {
    const result = siteHighlightLabel(node.highlight, hasCase);
    return { status: result.label, tone: result.tone };
  }
  return node.highlight
    ? { status: 'On highlighted path', tone: 'path' }
    : { status: hasCase ? 'No impact' : 'No active case', tone: 'neutral' };
}

/** Kartu node. Site = kartu penuh (klik → profil); part = baris ringkas; product = kartu. */
function NetworkCardNode({ id, data }: NodeProps<NetworkCardFlowNode>) {
  const stagger = { '--lane': data.laneIndex } as CSSProperties;
  const dim = data.dim ? ' is-dim' : '';
  return (
    <div className="network-flow-node" style={stagger}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      {data.entityType === 'site' ? (
        <button
          type="button"
          className={`network-flow-card network-flow-card--site network-flow-card--${data.tone}${data.active ? ' is-active' : ''}${dim}`}
          onClick={(event) => {
            event.stopPropagation();
            data.onSelectSite(id);
          }}
          aria-label={`View supplier profile for ${data.title}`}
          aria-pressed={data.active}
        >
          <span className="network-flow-card__top"><span className="network-flow-card__id">{id}</span><span>{data.subtitle}</span></span>
          <strong className="network-flow-card__title">{data.title}</strong>
          <span className="network-flow-card__bottom">
            <span>BCP {data.bcpScore} · {data.bcpBand}</span>
            <span className={`network-state network-state--${data.tone}`}>{data.status}</span>
          </span>
        </button>
      ) : data.entityType === 'part' ? (
        <div className={`network-flow-card network-flow-card--part network-flow-card--${data.tone}${dim}`} title={`${id} · ${data.title}`}>
          <span className="network-flow-card__id">{id}</span>
          <span className="network-flow-card__part-name">{data.title}</span>
          {data.tone === 'path' && <span className="network-state network-state--path">On path</span>}
        </div>
      ) : (
        <div className={`network-flow-card network-flow-card--product network-flow-card--${data.tone}${dim}`}>
          <span className="network-flow-card__top"><span className="network-flow-card__id">{id}</span><span>Product</span></span>
          <strong className="network-flow-card__title">{data.title}</strong>
          <span className={`network-state network-state--${data.tone}`}>{data.status}</span>
        </div>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

/** Latar kolom + header; ikut ter-zoom dan tergeser bersama graf sehingga selalu sejajar dengan node. */
function LaneNode({ data }: NodeProps<LaneFlowNode>) {
  return (
    <div className={`network-lane network-lane--${data.laneKey}`} style={{ '--lane': data.step - 1 } as CSSProperties}>
      <div className="network-lane__header">
        <span className="network-lane__step" aria-hidden="true">{data.step}</span>
        <span className="network-lane__titles">
          <strong>{data.title}</strong>
          <span>{data.caption}</span>
        </span>
        <span className="network-lane__count" aria-label={`${data.count} shown`}>{data.count}</span>
      </div>
    </div>
  );
}

const nodeTypes = { networkCard: NetworkCardNode, lane: LaneNode };

function buildFlowNodes(
  visibleNodes: NetworkNode[],
  layout: NetworkLayout,
  hasCase: boolean,
  selectedSiteId: string | null,
  onSelectSite: (siteId: string) => void,
): (LaneFlowNode | NetworkCardFlowNode)[] {
  const byId = new Map(visibleNodes.map((node) => [node.id, node]));

  const lanes: LaneFlowNode[] = layout.lanes.map((lane) => ({
    id: `lane-${lane.spec.key}`,
    type: 'lane',
    position: { x: lane.spec.x, y: 0 },
    width: lane.spec.width,
    height: lane.height,
    zIndex: -1,
    draggable: false,
    selectable: false,
    focusable: false,
    style: { pointerEvents: 'none' },
    data: {
      laneKey: lane.spec.key,
      step: lane.spec.step,
      title: lane.spec.title,
      caption: lane.spec.caption,
      count: lane.count,
      height: lane.height,
    },
  }));

  const cards: NetworkCardFlowNode[] = layout.nodes.flatMap((placed) => {
    const node = byId.get(placed.id);
    if (!node) return [];
    const highlight = highlightForNode(node, hasCase);
    const site = node.type === 'site' ? node : undefined;
    return [{
      id: node.id,
      type: 'networkCard' as const,
      position: { x: placed.x, y: placed.y },
      width: placed.width,
      height: placed.height,
      data: {
        entityType: node.type,
        title: node.label,
        subtitle: site ? `${site.kabupaten}, ${site.country}` : '',
        status: highlight.status,
        tone: highlight.tone,
        bcpScore: site?.bcp_score,
        bcpBand: site ? bcpBandLabel(site.bcp_band) : undefined,
        active: site?.id === selectedSiteId,
        // Saat kasus dipilih, node tanpa dampak diredupkan agar jalur dampak menonjol (tetap berteks).
        dim: hasCase && highlight.tone === 'neutral',
        laneIndex: placed.laneIndex,
        onSelectSite,
      },
      draggable: false,
      selectable: false,
    }];
  });

  return [...lanes, ...cards];
}

function buildFlowEdges(network: Network, visibleNodes: NetworkNode[], hasCase: boolean): Edge[] {
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  return network.edges
    .filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to))
    .map((edge) => {
      // Label hanya untuk Tier-2 → Tier-1 (komponen + buffer_days) dan jumlah per unit pada jalur sorotan.
      const label = edge.type === 'tier2_to_tier1'
        ? `${edge.component} · ${edge.buffer_days}-day buffer`
        : edge.type === 'used_in' && edge.highlight
          ? `${edge.qty_per_unit} per unit`
          : undefined;
      const color = edge.highlight ? '#c46b0b' : hasCase ? '#cfd5db' : '#b3bcc6';
      return {
        id: `${edge.from}-${edge.type}-${edge.to}`,
        source: edge.from,
        target: edge.to,
        type: 'smoothstep',
        label,
        // Garis putus-putus bergerak pada jalur dampak (animation.md §4.7).
        animated: edge.highlight,
        zIndex: edge.highlight ? 5 : 0,
        labelStyle: { fill: edge.highlight ? '#8a4b00' : '#5f6670', fontSize: 11, fontWeight: 600 },
        labelBgStyle: { fill: '#fff', fillOpacity: 0.96 },
        labelBgPadding: [6, 3] as [number, number],
        labelBgBorderRadius: 4,
        style: { stroke: color, strokeWidth: edge.highlight ? 2.4 : 1.2 },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      };
    });
}

function NetworkTable({
  network,
  nodes,
  hasCase,
  onSelectSite,
}: {
  network: Network;
  nodes: NetworkNode[];
  hasCase: boolean;
  onSelectSite: (siteId: string) => void;
}) {
  const visibleIds = new Set(nodes.map((node) => node.id));
  const edges = network.edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to));
  return (
    <div className="network-table-view">
      <div className="network-table-section">
        <h3>Network entities</h3>
        <div className="network-table-scroll">
          <table>
            <thead>
              <tr><th>Type</th><th>ID</th><th>Name</th><th>Tier</th><th>Location</th><th>Country</th><th>BCP score</th><th>Highlight</th><th>Profile</th></tr>
            </thead>
            <tbody>
              {nodes.map((node) => {
                const site = node.type === 'site' ? node : undefined;
                const highlight = highlightForNode(node, hasCase);
                return (
                  <tr key={node.id}>
                    <td>{node.type === 'site' ? 'Supplier site' : node.type === 'part' ? 'Part' : 'Product'}</td>
                    <td className="mono">{node.id}</td>
                    <td>{node.label}</td>
                    <td>{site ? `Tier ${site.tier}` : '—'}</td>
                    <td>{site?.kabupaten ?? '—'}</td>
                    <td>{site ? countryLabel(site.country) : '—'}</td>
                    <td>{site ? `${site.bcp_score} · ${bcpBandLabel(site.bcp_band)}` : '—'}</td>
                    <td><span className={`network-state network-state--${highlight.tone}`}>{highlight.status}</span></td>
                    <td>{site && <Button variant="ghost" onClick={() => onSelectSite(site.id)}>View profile</Button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <div className="network-table-section">
        <h3>Connections</h3>
        <div className="network-table-scroll">
          <table>
            <thead><tr><th>From</th><th>Relationship</th><th>To</th><th>Component / details</th><th>Path</th></tr></thead>
            <tbody>
              {edges.map((edge) => (
                <tr key={`${edge.from}-${edge.type}-${edge.to}`}>
                  <td className="mono">{edge.from}</td>
                  <td>{edge.type === 'tier2_to_tier1' ? 'Tier-2 to tier-1' : edge.type === 'supplies' ? 'Supplies' : 'Used in'}</td>
                  <td className="mono">{edge.to}</td>
                  <td>{edge.type === 'tier2_to_tier1'
                    ? `${edge.component} · ${edge.buffer_days}-day buffer`
                    : edge.type === 'used_in' ? `${edge.qty_per_unit} per unit` : '—'}</td>
                  <td>{edge.highlight ? 'Highlighted path' : 'No active impact'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/** Laci profil supplier di sisi kanan (EP-06). Esc atau × menutup. */
function SupplierProfilePanel({ siteId, onClose }: { siteId: string; onClose: () => void }) {
  const profileQuery = useSupplierProfile(siteId);
  const profile = profileQuery.data;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <aside className="network-profile" aria-label="Supplier profile">
      <div className="network-profile__header">
        <div><p className="eyebrow">Supplier site {siteId}</p><h2>Supplier profile</h2></div>
        <button type="button" className="network-profile__close" onClick={onClose} aria-label="Close supplier profile">×</button>
      </div>
      {profileQuery.isPending ? <p className="network-profile__state" role="status">Loading supplier profile…</p>
        : profileQuery.isError || !profile ? (
          <div className="network-profile__state network-profile__state--error" role="alert">
            <p>Supplier profile could not be loaded.</p>
            <Button variant="secondary" onClick={() => void profileQuery.refetch()}>Try again</Button>
          </div>
        ) : <SupplierProfileContent profile={profile} />}
    </aside>
  );
}

/** Balasan terstruktur (kontrak v1.11): jawaban dampak, ramp-up, atau ya/tidak. */
function structuredReplyText(structured: NonNullable<SupplierMessage['structured']>): string {
  if ('affected' in structured) return `${structured.affected} · ${structured.delay_days} days · ${structured.notes}`;
  if ('ramp_days' in structured) {
    const ramp = structured.ramp_days === null ? 'no ramp-up estimate' : `${structured.ramp_days} days to ramp up`;
    return `${structured.answer} · ${ramp} · ${structured.notes}`;
  }
  return `${structured.answer} · ${structured.notes}`;
}

function SupplierProfileContent({ profile }: { profile: SupplierProfile }) {
  const location = profile.kecamatan
    ? `${profile.kecamatan}, ${profile.kabupaten}`
    : profile.kabupaten;
  const planItems = [
    ['Backup site plan', profile.bcp.backup_site],
    ['Emergency plan', profile.bcp.emergency_plan],
    ['Safety stock', profile.bcp.safety_stock],
    ['Insurance', profile.bcp.insurance],
  ] as const;
  return (
    <div className="network-profile__content">
      <section className="network-profile__identity">
        <div className="network-profile__identity-top"><span className="mono">{profile.site_id}</span><Badge variant="SIMULATED" /></div>
        <h3>{profile.supplier_name}</h3>
        <p>Tier {profile.tier} · {location} · {countryLabel(profile.country)}</p>
      </section>

      <section className="network-profile__metrics" aria-label="Supplier metrics">
        <div><span>Recovery time</span><strong>{profile.ttr_days} days</strong></div>
        <div><span>On-time delivery (12 months)</span><strong>{profile.otd_12m === null ? 'Not available' : formatPercent(profile.otd_12m * 100)}</strong></div>
        <div><span>Flood hazard zone</span><strong>{profile.hazard_zone_flood ? 'Yes' : 'No'}</strong></div>
      </section>

      <SupplierBcpPanel bcp={profile.bcp} />
      <SupplierBackupPanel backup={profile.backup} />

      <section className="network-profile__section">
        <h3>Continuity measures</h3>
        <ul className="network-profile__checklist">
          {planItems.map(([label, enabled]) => <li key={label}><span>{label}</span><strong>{enabled ? 'Available' : 'Not available'}</strong></li>)}
        </ul>
        <p className="network-profile__muted">Supplier backup site: {profile.backup_site ?? 'None recorded'}</p>
      </section>

      <section className="network-profile__section">
        <h3>Parts supplied</h3>
        {profile.parts_supplied.length
          ? <div className="network-profile__chips">{profile.parts_supplied.map((part) => <span key={part} className="network-chip mono">{part}</span>)}</div>
          : <p className="network-profile__muted">No parts are listed for this site.</p>}
      </section>

      <section className="network-profile__section">
        <h3>Tier-2 suppliers</h3>
        {profile.tier2_suppliers.length ? (
          <ul className="network-profile__related-list">
            {profile.tier2_suppliers.map((supplier) => <li key={supplier.site_id}><strong>{supplier.site_id}</strong><span>{supplier.component} · {supplier.buffer_days}-day buffer</span></li>)}
          </ul>
        ) : <p className="network-profile__muted">No Tier-2 supplier is listed.</p>}
      </section>

      <section className="network-profile__section">
        <h3>Alternate sources</h3>
        {profile.alternate_sources_for_parts.length ? (
          <ul className="network-profile__related-list">
            {profile.alternate_sources_for_parts.map((source) => <li key={`${source.part_id}-${source.site_id}`}><strong>{source.part_id} → {source.site_id}</strong><span>{formatIDR(source.price)} · {source.lead_time_days}-day lead time · max {source.max_qty} units</span></li>)}
          </ul>
        ) : <p className="network-profile__muted">No alternate source is recorded.</p>}
      </section>

      <section className="network-profile__section">
        <h3>Supplier message history</h3>
        {profile.messages.length ? (
          <ol className="network-profile__messages">
            {profile.messages.map((message) => (
              <li key={message.message_id}>
                <div className="network-profile__message-meta"><strong>{message.direction === 'OUT' ? 'Sent to supplier' : 'Supplier reply'}</strong><Badge variant={message.simulated ? 'SIMULATED' : 'LIVE'} /><time dateTime={message.created_at}>{formatDateTime(message.created_at)}</time></div>
                <p>{message.body}</p>
                {message.structured && <p className="network-profile__message-structured">{structuredReplyText(message.structured)}</p>}
              </li>
            ))}
          </ol>
        ) : <p className="network-profile__muted">No supplier messages are recorded for this site.</p>}
      </section>
    </div>
  );
}

/** Lebar elemen (px) yang selalu terbaru. Memakai callback ref karena bingkai graf baru ada setelah data dimuat. */
function useElementWidth<T extends HTMLElement>() {
  const [element, setElement] = useState<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(element);
    setWidth(Math.round(element.getBoundingClientRect().width));
    return () => observer.disconnect();
  }, [element]);
  return [setElement, width] as const;
}

const OVERVIEW_TILES: { lane: LaneKey; step: number; label: string; hint: string }[] = [
  { lane: 'tier2', step: 1, label: 'Tier-2 sites', hint: 'Component makers' },
  { lane: 'tier1', step: 2, label: 'Tier-1 sites', hint: 'Part suppliers' },
  { lane: 'part', step: 3, label: 'Parts', hint: 'Purchased parts' },
  { lane: 'product', step: 4, label: 'Products', hint: 'Finished motorcycles' },
];

export function NetworkExplorer({ fixedCaseId, version, caseOptions, caseOptionsError = false, onRetryCaseOptions, embedded = false }: NetworkExplorerProps) {
  const [searchParams] = useSearchParams();
  const routeFiltersEnabled = !embedded && !fixedCaseId;
  const [selectedCaseId, setSelectedCaseId] = useState('');
  const [country, setCountry] = useState(() => routeFiltersEnabled ? searchParams.get('country') ?? 'all' : 'all');
  const [location, setLocation] = useState('all');
  const [search, setSearch] = useState(() => routeFiltersEnabled ? searchParams.get('search') ?? '' : '');
  const [tableView, setTableView] = useState(false);
  const [selectedSiteId, setSelectedSiteId] = useState<string | null>(null);
  const [frameRef, frameWidth] = useElementWidth<HTMLDivElement>();
  const activeCaseId = fixedCaseId ?? (caseOptions === undefined ? undefined : selectedCaseId || undefined);
  const networkQuery = useNetwork(activeCaseId, version);
  const network = networkQuery.data;
  const activeCase = caseOptions?.find((item) => item.case_id === activeCaseId);

  const sites = useMemo(() => network?.nodes.filter((node): node is Extract<NetworkNode, { type: 'site' }> => node.type === 'site') ?? [], [network]);
  const countries = useMemo(() => [...new Set(sites.map((site) => site.country))].sort(), [sites]);
  const locations = useMemo(() => [...new Set(sites.map((site) => site.kabupaten))].sort(), [sites]);
  const visibleNodes = useMemo(
    () => network ? filterNetworkNodes(network, country, location, search) : [],
    [network, country, location, search],
  );
  const layout = useMemo(
    () => network ? layoutNetwork(visibleNodes, network.edges) : null,
    [network, visibleNodes],
  );
  const flowNodes = useMemo(() => network && layout
    ? buildFlowNodes(visibleNodes, layout, Boolean(activeCaseId), selectedSiteId, setSelectedSiteId)
    : [], [network, layout, visibleNodes, activeCaseId, selectedSiteId]);
  const flowEdges = useMemo(() => network ? buildFlowEdges(network, visibleNodes, Boolean(activeCaseId)) : [], [network, visibleNodes, activeCaseId]);
  const frameHeight = layout ? graphFrameHeight(layout.height, frameWidth) : 0;
  const flowKey = `${activeCaseId ?? 'all'}-${version ?? 'latest'}-${country}-${location}-${search}-${frameHeight}`;
  const counts = highlightCounts(visibleNodes);
  const laneCounts = new Map((layout?.lanes ?? []).map((lane) => [lane.spec.key, lane.count]));
  const filtersActive = country !== 'all' || location !== 'all' || Boolean(search.trim());
  const hasCase = Boolean(activeCaseId);

  const clearFilters = () => {
    setCountry('all');
    setLocation('all');
    setSearch('');
  };

  return (
    <section className={`network-explorer${embedded ? ' network-explorer--embedded' : ''}`}>
      {/* 1 · Header halaman */}
      {!embedded ? (
        <header className="network-header enter">
          <div>
            <p className="eyebrow">Supply chain map</p>
            <h1>Supplier network</h1>
            <p className="network-header__description">
              Follow how Tier-2 suppliers feed Tier-1 suppliers, parts and finished products, and see which paths a risk case reaches.
            </p>
          </div>
          <Badge variant="SIMULATED" />
        </header>
      ) : (
        <div className="network-explorer__embedded-heading"><h2>Supplier network</h2><p>Impact paths for this case and snapshot.</p></div>
      )}

      {/* 2 · Ringkasan: satu kartu per kolom graf */}
      {!embedded && (
        <ol className="network-overview enter" style={{ '--i': 1 } as CSSProperties} aria-label="Network overview">
          {OVERVIEW_TILES.map((tile) => (
            <li key={tile.lane} className={`network-overview__tile network-overview__tile--${tile.lane}`}>
              <span className="network-overview__step" aria-hidden="true">{tile.step}</span>
              <span className="network-overview__label">{tile.label}</span>
              <strong className="network-overview__value">{network ? laneCounts.get(tile.lane) ?? 0 : '—'}</strong>
              <span className="network-overview__hint">{tile.hint}</span>
            </li>
          ))}
        </ol>
      )}

      {/* 3 · Cakupan dan filter */}
      <section className="network-controls enter" style={{ '--i': 2 } as CSSProperties} aria-label="Scope and filters">
        <div className="network-toolbar">
          {caseOptions !== undefined && (
            <label className="network-filter network-filter--case">
              <span>Case impact</span>
              <select value={selectedCaseId} onChange={(event) => setSelectedCaseId(event.target.value)}>
                <option value="">No case selected</option>
                {caseOptions.map((item) => <option key={item.case_id} value={item.case_id}>{item.title} · {item.level}</option>)}
              </select>
            </label>
          )}
          <label className="network-filter">
            <span>Country</span>
            <select value={country} onChange={(event) => { setCountry(event.target.value); setLocation('all'); }}>
              <option value="all">All countries</option>
              {countries.map((item) => <option key={item} value={item}>{countryLabel(item)}</option>)}
            </select>
          </label>
          <label className="network-filter">
            <span>Location</span>
            <select value={location} onChange={(event) => setLocation(event.target.value)}>
              <option value="all">All locations</option>
              {locations.filter((item) => country === 'all' || sites.some((site) => site.kabupaten === item && site.country === country))
                .map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="network-filter network-filter--search">
            <span>Search</span>
            <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Sites, parts, or products" />
          </label>
          {filtersActive && <Button variant="ghost" onClick={clearFilters}>Clear filters</Button>}
        </div>
      </section>

      {caseOptionsError && (
        <div className="network-inline-error" role="alert">
          <span>The case list could not be loaded. The unfiltered network is still available.</span>
          {onRetryCaseOptions && <Button variant="ghost" onClick={onRetryCaseOptions}>Try again</Button>}
        </div>
      )}

      {/* 4 · Peta jaringan */}
      <section className="network-graph-card enter" style={{ '--i': 3 } as CSSProperties} aria-label="Impact map">
        <div className="network-graph-card__header">
          <div className="network-graph-card__title">
            <h2>Impact map</h2>
            {hasCase && network ? (
              <span className="network-case-context">
                {activeCase && <Badge variant={activeCase.level}>{activeCase.level}</Badge>}
                <span>{activeCase?.title ?? network.case_id}</span>
                <span className="mono">{network.case_id} · Version {network.version ?? version ?? 1}</span>
              </span>
            ) : (
              <span className="network-case-context network-case-context--empty">
                No case selected — select a case to highlight its impact path.
              </span>
            )}
          </div>
          <div className="network-graph-card__actions">
            <Badge variant="SIMULATED">Mock SAP S/4HANA — SIMULATED</Badge>
            <div className="network-view-toggle" role="group" aria-label="Network display">
              <button type="button" aria-pressed={!tableView} className={!tableView ? 'is-active' : ''} onClick={() => setTableView(false)}>Graph view</button>
              <button type="button" aria-pressed={tableView} className={tableView ? 'is-active' : ''} onClick={() => setTableView(true)}>Table view</button>
            </div>
          </div>
        </div>

        <ul className="network-legend" aria-label="Network highlight legend">
          <li><i className="network-legend__swatch network-legend__swatch--affected" />Affected{hasCase && <b>{counts.affected}</b>}</li>
          <li><i className="network-legend__swatch network-legend__swatch--via" />Affected via Tier-2{hasCase && <b>{counts.viaTier2}</b>}</li>
          <li><i className="network-legend__swatch network-legend__swatch--confirmed" />Confirmed – no impact{hasCase && <b>{counts.confirmed}</b>}</li>
          <li><i className="network-legend__swatch network-legend__swatch--neutral" />{hasCase ? 'No impact' : 'No active case'}</li>
          <li className="network-legend__note">Arrows follow the supply direction, left to right. Select a site for its profile.</li>
        </ul>

        {networkQuery.isPending ? <p className="network-state-message" role="status">Loading supplier network…</p>
          : networkQuery.isError || !network ? (
            <div className="network-state-message network-state-message--error" role="alert">
              <p>Supplier network could not be loaded.</p>
              <Button variant="secondary" onClick={() => void networkQuery.refetch()}>Try again</Button>
            </div>
          ) : visibleNodes.length === 0 ? (
            <div className="network-state-message">
              <p>No network items match these filters.</p>
              <Button variant="secondary" onClick={clearFilters}>Clear filters</Button>
            </div>
          ) : tableView ? (
            <NetworkTable network={network} nodes={visibleNodes} hasCase={hasCase} onSelectSite={setSelectedSiteId} />
          ) : (
            <div className="network-flow-frame" ref={frameRef} style={{ height: frameHeight }} aria-label="Supplier network graph">
              <ReactFlow
                key={flowKey}
                nodes={flowNodes}
                edges={flowEdges}
                nodeTypes={nodeTypes}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable={false}
                // Scroll halaman tetap berfungsi di atas graf; zoom lewat tombol, geser dengan drag.
                zoomOnScroll={false}
                zoomOnDoubleClick={false}
                preventScrolling={false}
                fitView
                fitViewOptions={{ padding: 0.02, minZoom: 0.4, maxZoom: 1.15 }}
                minZoom={0.4}
                maxZoom={1.4}
                translateExtent={[[-80, -40], [GRAPH_WIDTH + 80, (layout?.height ?? 0) + 40]]}
                proOptions={{ hideAttribution: true }}
              >
                <Background color="#e6e9ee" gap={22} size={1} />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
          )}
      </section>

      {/* 5 · Laci profil supplier */}
      {selectedSiteId && <SupplierProfilePanel siteId={selectedSiteId} onClose={() => setSelectedSiteId(null)} />}
    </section>
  );
}
