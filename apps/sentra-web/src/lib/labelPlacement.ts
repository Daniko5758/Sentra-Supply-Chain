// Penempatan label penanda di peta agar tidak saling menabrak. Murni geometri tampilan.
// Posisi layar diperkirakan dari lng/lat (cukup untuk menghindari tabrakan; bukan untuk menggambar).

export type LabelSide = 'top' | 'bottom' | 'right' | 'left';

export interface LabelInput {
  id: string;
  lng: number;
  lat: number;
  text: string;
}

export interface LabelPlacement {
  id: string;
  side: LabelSide;
}

interface Box {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

// Perkiraan skala peta geoEqualEarth (scale 155, lebar 960): piksel per derajat.
const PX_PER_LNG = 2.3;
const PX_PER_LAT = 2.9;
const CHAR_WIDTH = 6.4;
const LABEL_HEIGHT = 13;
const GAP = 9;
const DOT_RADIUS = 7;

const SIDES: LabelSide[] = ['top', 'bottom', 'right', 'left'];

export function approximatePoint(lng: number, lat: number): { x: number; y: number } {
  return { x: lng * PX_PER_LNG * Math.cos(lat * 0.012), y: -lat * PX_PER_LAT };
}

export function labelBox(x: number, y: number, width: number, side: LabelSide): Box {
  switch (side) {
    case 'top': return { x1: x - width / 2, x2: x + width / 2, y1: y - GAP - LABEL_HEIGHT, y2: y - GAP };
    case 'bottom': return { x1: x - width / 2, x2: x + width / 2, y1: y + GAP, y2: y + GAP + LABEL_HEIGHT };
    case 'right': return { x1: x + GAP, x2: x + GAP + width, y1: y - LABEL_HEIGHT / 2, y2: y + LABEL_HEIGHT / 2 };
    case 'left': return { x1: x - GAP - width, x2: x - GAP, y1: y - LABEL_HEIGHT / 2, y2: y + LABEL_HEIGHT / 2 };
  }
}

const overlaps = (a: Box, b: Box) => a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;

export function labelWidth(text: string): number {
  return text.length * CHAR_WIDTH;
}

/**
 * `items` dalam urutan prioritas (yang penting lebih dulu). Tiap label mencoba atas, bawah, kanan, kiri
 * dan memakai sisi pertama yang tidak menabrak label lain maupun titik penanda; bila semuanya penuh, memakai atas.
 */
export function placeLabels(items: LabelInput[]): LabelPlacement[] {
  const points = items.map((item) => ({ item, ...approximatePoint(item.lng, item.lat) }));
  const dots: Box[] = points.map(({ x, y }) => ({
    x1: x - DOT_RADIUS, x2: x + DOT_RADIUS, y1: y - DOT_RADIUS, y2: y + DOT_RADIUS,
  }));
  const taken: Box[] = [];

  return points.map(({ item, x, y }, index) => {
    const width = labelWidth(item.text);
    const side = SIDES.find((candidate) => {
      const box = labelBox(x, y, width, candidate);
      return !taken.some((other) => overlaps(box, other))
        && !dots.some((dot, dotIndex) => dotIndex !== index && overlaps(box, dot));
    }) ?? 'top';
    taken.push(labelBox(x, y, width, side));
    return { id: item.id, side };
  });
}

/** Atribut SVG <text> untuk sisi tertentu (relatif terhadap titik penanda). */
export function labelAttributes(side: LabelSide): { x: number; y: number; textAnchor: 'start' | 'middle' | 'end' } {
  switch (side) {
    case 'top': return { x: 0, y: -11, textAnchor: 'middle' };
    case 'bottom': return { x: 0, y: 19, textAnchor: 'middle' };
    case 'right': return { x: 11, y: 4, textAnchor: 'start' };
    case 'left': return { x: -11, y: 4, textAnchor: 'end' };
  }
}
