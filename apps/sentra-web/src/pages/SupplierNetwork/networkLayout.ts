// Tata letak graf Supplier network (FR-05): 4 kolom Tier-2 → Tier-1 → Part → Product.
// Murni geometri tampilan; tidak ada angka bisnis. Urutan node dipilih agar garis saling silang sesedikit mungkin.
import type { NetworkEdge, NetworkNode } from '../../api/types';

export type LaneKey = 'tier2' | 'tier1' | 'part' | 'product';

export interface LaneSpec {
  key: LaneKey;
  step: number;
  title: string;
  caption: string;
  x: number;
  width: number;
}

export const LANE_HEADER_H = 56;
export const LANE_PAD = 12;
const LANE_BOTTOM_PAD = 14;

export const SITE_H = 80;
export const SITE_PITCH = 90;
export const PART_H = 40;
export const PART_PITCH = 46;
export const PRODUCT_H = 84;
export const PRODUCT_PITCH = 108;

// Jarak Tier-2 → Tier-1 lebih lebar karena edge-nya membawa label komponen dan buffer_days.
export const LANES: LaneSpec[] = [
  { key: 'tier2', step: 1, title: 'Tier-2 suppliers', caption: 'Component makers', x: 0, width: 252 },
  { key: 'tier1', step: 2, title: 'Tier-1 suppliers', caption: 'Part suppliers', x: 432, width: 252 },
  { key: 'part', step: 3, title: 'Parts', caption: 'Purchased parts', x: 748, width: 268 },
  { key: 'product', step: 4, title: 'Products', caption: 'Finished motorcycles', x: 1080, width: 252 },
];
export const GRAPH_WIDTH = 1332;

export interface LaidOutNode {
  id: string;
  lane: LaneKey;
  laneIndex: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LaneLayout {
  spec: LaneSpec;
  count: number;
  height: number;
}

export interface NetworkLayout {
  lanes: LaneLayout[];
  nodes: LaidOutNode[];
  height: number;
}

type SiteNode = Extract<NetworkNode, { type: 'site' }>;

const isSite = (node: NetworkNode): node is SiteNode => node.type === 'site';

/** Indeks terkecil dari daftar tujuan; node tanpa tautan diletakkan paling akhir. */
function firstIndex(ids: string[], order: Map<string, number>): number {
  const indexes = ids.map((id) => order.get(id)).filter((value): value is number => value !== undefined);
  return indexes.length ? Math.min(...indexes) : Number.MAX_SAFE_INTEGER;
}

export function layoutNetwork(visible: NetworkNode[], edges: NetworkEdge[]): NetworkLayout {
  const visibleIds = new Set(visible.map((node) => node.id));
  const linked = edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to));

  const tier1 = visible.filter(isSite).filter((node) => node.tier !== '2');
  const tier1Order = new Map(tier1.map((node, index) => [node.id, index]));

  const tier2 = visible
    .filter(isSite)
    .filter((node) => node.tier === '2')
    .map((node, index) => ({
      node,
      index,
      sort: firstIndex(
        linked.filter((edge) => edge.type === 'tier2_to_tier1' && edge.from === node.id).map((edge) => edge.to),
        tier1Order,
      ),
    }))
    .sort((a, b) => a.sort - b.sort || a.index - b.index)
    .map((item) => item.node);

  const parts = visible
    .filter((node) => node.type === 'part')
    .map((node, index) => ({
      node,
      index,
      sort: firstIndex(
        linked.filter((edge) => edge.type === 'supplies' && edge.to === node.id).map((edge) => edge.from),
        tier1Order,
      ),
    }))
    .sort((a, b) => a.sort - b.sort || a.index - b.index)
    .map((item) => item.node);

  const products = visible.filter((node) => node.type === 'product');

  const columns: { lane: LaneKey; items: NetworkNode[]; height: number; pitch: number }[] = [
    { lane: 'tier2', items: tier2, height: SITE_H, pitch: SITE_PITCH },
    { lane: 'tier1', items: tier1, height: SITE_H, pitch: SITE_PITCH },
    { lane: 'part', items: parts, height: PART_H, pitch: PART_PITCH },
    { lane: 'product', items: products, height: PRODUCT_H, pitch: PRODUCT_PITCH },
  ];

  const contentHeight = Math.max(
    SITE_PITCH,
    ...columns.map((column) => column.items.length * column.pitch),
  );
  const laneHeight = LANE_HEADER_H + contentHeight + LANE_BOTTOM_PAD;

  const nodes: LaidOutNode[] = [];
  columns.forEach((column, laneIndex) => {
    const spec = LANES[laneIndex];
    // Produk (sedikit) dipusatkan secara vertikal; kolom lain mulai dari atas.
    const offset = column.lane === 'product' ? (contentHeight - column.items.length * column.pitch) / 2 : 0;
    column.items.forEach((node, index) => {
      nodes.push({
        id: node.id,
        lane: column.lane,
        laneIndex,
        x: spec.x + LANE_PAD,
        y: LANE_HEADER_H + offset + index * column.pitch,
        width: spec.width - LANE_PAD * 2,
        height: column.height,
      });
    });
  });

  return {
    lanes: columns.map((column, laneIndex) => ({
      spec: LANES[laneIndex],
      count: column.items.length,
      height: laneHeight,
    })),
    nodes,
    height: laneHeight,
  };
}

/** Jumlah site per jenis sorotan (data `highlight` dari API; hanya dikelompokkan untuk legenda). */
export function highlightCounts(nodes: NetworkNode[]): { affected: number; viaTier2: number; confirmed: number } {
  const sites = nodes.filter(isSite);
  return {
    affected: sites.filter((node) => node.highlight === 'affected').length,
    viaTier2: sites.filter((node) => node.highlight === 'via_tier2').length,
    confirmed: sites.filter((node) => node.highlight === 'confirmed_no_impact').length,
  };
}

/** Tinggi bingkai graf agar skala fit-to-width sekitar 1:1 di layar lebar dan tidak terlalu kecil di 1366 px. */
export function graphFrameHeight(layoutHeight: number, containerWidth: number): number {
  const width = containerWidth > 0 ? containerWidth : GRAPH_WIDTH;
  const scale = Math.min(1.15, Math.max(0.55, width / GRAPH_WIDTH));
  return Math.round(layoutHeight * scale + 16);
}
