// Pembaca TopoJSON kecil untuk globe 3D: dekode arc, uji titik-dalam-poligon, titik tengah negara.
// Hanya geografi (src/assets/countries-110m.json); tidak ada data bisnis di sini.

export type LngLat = [number, number];
export type Ring = LngLat[];
/** Satu poligon = [ring luar, ...lubang]. */
export type Polygon = Ring[];

export interface CountryShape {
  /** ISO 3166-1 numeric, sesuai `id` di TopoJSON. */
  id: string;
  name: string;
  polygons: Polygon[];
  bbox: [number, number, number, number];
}

interface TopoGeometry {
  type: 'Polygon' | 'MultiPolygon' | null;
  arcs?: number[][] | number[][][];
  id?: string | number;
  properties?: { name?: string };
}

export interface Topology {
  transform: { scale: [number, number]; translate: [number, number] };
  arcs: number[][][];
  objects: { countries: { geometries: TopoGeometry[] } };
}

/** Dekode semua negara di `objects.countries` menjadi koordinat [lng, lat]. */
export function decodeCountries(topology: Topology): CountryShape[] {
  const { scale, translate } = topology.transform;
  const arcs: Ring[] = topology.arcs.map((arc) => {
    let x = 0;
    let y = 0;
    return arc.map(([dx, dy]) => {
      x += dx;
      y += dy;
      return [x * scale[0] + translate[0], y * scale[1] + translate[1]] as LngLat;
    });
  });

  const arcPoints = (index: number): Ring => (index >= 0 ? arcs[index] : arcs[~index].slice().reverse());
  const toRing = (indexes: number[]): Ring =>
    indexes.flatMap((index, k) => (k === 0 ? arcPoints(index) : arcPoints(index).slice(1)));

  const shapes: CountryShape[] = [];
  for (const geometry of topology.objects.countries.geometries) {
    if (!geometry.type || !geometry.arcs || geometry.id === undefined) continue;
    const polygons: Polygon[] =
      geometry.type === 'Polygon'
        ? [(geometry.arcs as number[][]).map(toRing)]
        : (geometry.arcs as number[][][]).map((polygon) => polygon.map(toRing));

    let minLng = 180;
    let minLat = 90;
    let maxLng = -180;
    let maxLat = -90;
    for (const polygon of polygons) {
      for (const [lng, lat] of polygon[0]) {
        minLng = Math.min(minLng, lng);
        maxLng = Math.max(maxLng, lng);
        minLat = Math.min(minLat, lat);
        maxLat = Math.max(maxLat, lat);
      }
    }
    shapes.push({
      id: String(geometry.id),
      name: geometry.properties?.name ?? String(geometry.id),
      polygons,
      bbox: [minLng, minLat, maxLng, maxLat],
    });
  }
  return shapes;
}

/** Ray casting pada satu ring. */
function inRing([lng, lat]: LngLat, ring: Ring): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** True bila titik ada di ring luar salah satu poligon dan tidak di lubangnya. */
export function containsPoint(shape: CountryShape, point: LngLat): boolean {
  const [lng, lat] = point;
  const [minLng, minLat, maxLng, maxLat] = shape.bbox;
  if (lng < minLng || lng > maxLng || lat < minLat || lat > maxLat) return false;
  return shape.polygons.some(
    ([outer, ...holes]) => inRing(point, outer) && !holes.some((hole) => inRing(point, hole)),
  );
}

/** Luas bertanda (shoelace) ring dalam derajat persegi. */
function ringArea(ring: Ring): number {
  let area = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    area += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
  }
  return area / 2;
}

/** Titik tengah berbobot luas dari poligon terbesar (cukup untuk menaruh penanda di globe). */
export function centroid(shape: CountryShape): LngLat {
  const outer = shape.polygons
    .map((polygon) => polygon[0])
    .reduce((best, ring) => (Math.abs(ringArea(ring)) > Math.abs(ringArea(best)) ? ring : best));
  const area = ringArea(outer);
  if (area === 0) return outer[0];
  let cx = 0;
  let cy = 0;
  for (let i = 0, j = outer.length - 1; i < outer.length; j = i++) {
    const cross = outer[j][0] * outer[i][1] - outer[i][0] * outer[j][1];
    cx += (outer[j][0] + outer[i][0]) * cross;
    cy += (outer[j][1] + outer[i][1]) * cross;
  }
  return [cx / (6 * area), cy / (6 * area)];
}
