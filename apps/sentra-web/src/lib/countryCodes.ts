// EP-16 memakai ISO 3166-1 alpha-2; TopoJSON memakai ISO 3166-1 numeric.
// Tabel geografi umum (bukan data demo); negara yang tidak ada di tabel dicocokkan lewat nama.
export const TOPO_ID_BY_COUNTRY: Record<string, string> = {
  AU: '036',
  BD: '050',
  BR: '076',
  CA: '124',
  CN: '156',
  DE: '276',
  FR: '250',
  GB: '826',
  ID: '360',
  IN: '356',
  IT: '380',
  JP: '392',
  KH: '116',
  KR: '410',
  LA: '418',
  MM: '104',
  MX: '484',
  MY: '458',
  PH: '608',
  SG: '702',
  TH: '764',
  TW: '158',
  US: '840',
  VN: '704',
};

/** Cari id TopoJSON untuk kode alpha-2; fallback ke nama negara bila kode tidak ada di tabel. */
export function topoIdFor(
  alpha2: string,
  name: string,
  shapes: { id: string; name: string }[],
): string | undefined {
  const byCode = TOPO_ID_BY_COUNTRY[alpha2.toUpperCase()];
  if (byCode) return byCode;
  return shapes.find((shape) => shape.name.toLowerCase() === name.toLowerCase())?.id;
}
