// Globe risiko 3D (three.js). Dimuat lazy dari GlobeSection agar three.js tidak masuk bundel utama.
// Semua isi globe berasal dari EP-16 (negara, level, share, kasus terbuka) + TopoJSON lokal.
// Status: three.js menunggu persetujuan DS di docs/DECISIONS.md (branch role-c/dashboard-3d).
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import topology from '../../assets/countries-110m.json';
import type { HeatmapCountry } from '../../api/types';
import { Badge } from '../../components/Badge';
import { topoIdFor } from '../../lib/countryCodes';
import { centroid, containsPoint, decodeCountries, type CountryShape, type Topology } from '../../lib/topo';
import { GLOBE_COLORS, levelColor, riskText } from './globeColors';

const SHAPES: CountryShape[] = decodeCountries(topology as unknown as Topology);
const DEG = Math.PI / 180;
const AUTO_COUNTRY_INTERVAL_MS = 4600;

/** Titik daratan (grid ~1.5°) dengan negaranya; dihitung sekali per sesi. */
let landDotsCache: { lng: number; lat: number; countryId: string }[] | null = null;
function landDots() {
  if (landDotsCache) return landDotsCache;
  const dots: { lng: number; lat: number; countryId: string }[] = [];
  const step = 1.5;
  for (let lat = -56; lat <= 78; lat += step) {
    const lngStep = step / Math.max(0.3, Math.cos(lat * DEG));
    for (let lng = -180; lng < 180; lng += lngStep) {
      const shape = SHAPES.find((s) => containsPoint(s, [lng, lat]));
      if (shape) dots.push({ lng, lat, countryId: shape.id });
    }
  }
  landDotsCache = dots;
  return dots;
}

function lngLatToVector(lng: number, lat: number, radius = 1): THREE.Vector3 {
  const phi = (90 - lat) * DEG;
  const theta = (lng + 180) * DEG;
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

/** Rotasi grup agar titik (lng, lat) menghadap kamera (+Z). */
function rotationFacing(lng: number, lat: number): { x: number; y: number } {
  const v = lngLatToVector(lng, lat);
  return { y: Math.atan2(-v.x, v.z), x: lat * DEG * 0.85 };
}

function dotTexture(): THREE.Texture {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext('2d')!;
  ctx.beginPath();
  ctx.arc(32, 32, 28, 0, Math.PI * 2);
  ctx.fillStyle = '#ffffff';
  ctx.fill();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

interface Marker {
  country: HeatmapCountry;
  lng: number;
  lat: number;
  shape: CountryShape;
}

interface GlobeCanvasProps {
  countries: HeatmapCountry[];
  /** Kode alpha-2 negara yang dipilih di daftar; globe berputar ke negara itu. */
  focus: string | null;
  /** Increments on every selection so choosing the current country again restores focus. */
  focusRequest: number;
  zoomLevel: number;
  onFocusCountry: (countryCode: string) => void;
  onClearFocus: () => void;
}

export default function GlobeCanvas({ countries, focus, focusRequest, zoomLevel, onFocusCountry, onClearFocus }: GlobeCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const labelRefs = useRef(new Map<string, HTMLButtonElement>());
  const targetRef = useRef<{ x: number; y: number } | null>(null);
  const focusRef = useRef(focus);
  const zoomLevelRef = useRef(zoomLevel);
  const focusCountryRef = useRef(onFocusCountry);
  const clearFocusRef = useRef(onClearFocus);
  focusRef.current = focus;
  zoomLevelRef.current = zoomLevel;
  focusCountryRef.current = onFocusCountry;
  clearFocusRef.current = onClearFocus;

  const markers = useMemo<Marker[]>(
    () =>
      countries.flatMap((country) => {
        const id = topoIdFor(country.country, country.name, SHAPES);
        const shape = SHAPES.find((s) => s.id === id);
        if (!shape) return [];
        const [lng, lat] = centroid(shape);
        return [{ country, lng, lat, shape }];
      }),
    [countries],
  );

  // Kunci data: scene hanya dibangun ulang bila isi yang ditampilkan berubah (bukan tiap polling).
  const dataKey = JSON.stringify(
    markers.map((m) => [m.country.country, m.country.highest_level, m.country.share, m.country.open_cases.length]),
  );

  useEffect(() => {
    const marker = markers.find((m) => m.country.country === focus);
    if (marker) {
      targetRef.current = rotationFacing(marker.lng, marker.lat);
    }
    // markers berubah hanya bersama dataKey, jadi cukup bergantung pada dataKey
  }, [focus, focusRequest, dataKey]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.prepend(renderer.domElement);
    renderer.domElement.setAttribute('aria-hidden', 'true');

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
    camera.position.set(0, 0, 4.1);

    scene.add(new THREE.HemisphereLight('#ffffff', '#c9d8ea', 2.4));
    const sun = new THREE.DirectionalLight('#ffffff', 1.4);
    sun.position.set(-3, 2, 4);
    scene.add(sun);

    // Globe hanya diputar oleh drag pengguna atau fokus negara yang dipilih.
    const globe = new THREE.Group();
    scene.add(globe);

    // Bola dasar + atmosfer (fresnel)
    const earth = new THREE.Mesh(
      new THREE.SphereGeometry(1, 72, 72),
      new THREE.MeshPhongMaterial({ color: '#eef3fa', shininess: 12, specular: '#ffffff' }),
    );
    globe.add(earth);
    const atmosphere = new THREE.Mesh(
      new THREE.SphereGeometry(1.14, 64, 64),
      new THREE.ShaderMaterial({
        transparent: true,
        side: THREE.BackSide,
        depthWrite: false,
        uniforms: { glow: { value: new THREE.Color('#0a6ed1') } },
        vertexShader: `varying vec3 vNormal;
          void main() { vNormal = normalize(normalMatrix * normal);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
        fragmentShader: `uniform vec3 glow; varying vec3 vNormal;
          void main() { float i = pow(0.68 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 3.2);
          gl_FragColor = vec4(glow, clamp(i, 0.0, 1.0) * 0.55); }`,
      }),
    );
    scene.add(atmosphere);

    // Titik daratan, diwarnai level risiko negara supplier
    const byTopoId = new Map(markers.map((m) => [m.shape.id, m.country]));
    const dots = landDots();
    const positions = new Float32Array(dots.length * 3);
    const colors = new Float32Array(dots.length * 3);
    const color = new THREE.Color();
    dots.forEach((dot, i) => {
      const supplier = byTopoId.get(dot.countryId);
      lngLatToVector(dot.lng, dot.lat, supplier ? 1.006 : 1.003).toArray(positions, i * 3);
      color.set(supplier ? levelColor(supplier.highest_level) : GLOBE_COLORS.noSupplier).toArray(colors, i * 3);
    });
    const dotGeometry = new THREE.BufferGeometry();
    dotGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    dotGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const sprite = dotTexture();
    const dotMaterial = new THREE.PointsMaterial({
      size: 0.022,
      map: sprite,
      vertexColors: true,
      transparent: true,
      alphaTest: 0.4,
      opacity: 0,
    });
    globe.add(new THREE.Points(dotGeometry, dotMaterial));

    // Per negara supplier: garis batas, batang (tinggi = share), tutup, cincin berdenyut
    const up = new THREE.Vector3(0, 1, 0);
    const forward = new THREE.Vector3(0, 0, 1);
    const bars: { mesh: THREE.Mesh; cap: THREE.Mesh; normal: THREE.Vector3; height: number }[] = [];
    const rings: { mesh: THREE.Mesh; phase: number }[] = [];
    const anchors = new Map<string, { normal: THREE.Vector3; top: THREE.Vector3 }>();

    for (const marker of markers) {
      const hex = levelColor(marker.country.highest_level);
      const lineMaterial = new THREE.LineBasicMaterial({ color: hex, transparent: true, opacity: 0.85 });
      for (const [outer] of marker.shape.polygons) {
        const points = outer.map(([lng, lat]) => lngLatToVector(lng, lat, 1.008));
        globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), lineMaterial));
      }

      const normal = lngLatToVector(marker.lng, marker.lat).normalize();
      const height = 0.08 + (marker.country.share / 100) * 0.55;
      const barGeometry = new THREE.CylinderGeometry(0.016, 0.016, 1, 20);
      barGeometry.translate(0, 0.5, 0);
      const barMaterial = new THREE.MeshPhongMaterial({ color: hex, emissive: hex, emissiveIntensity: 0.35 });
      const bar = new THREE.Mesh(barGeometry, barMaterial);
      bar.quaternion.setFromUnitVectors(up, normal);
      bar.position.copy(normal);
      bar.scale.y = 0.0001;
      globe.add(bar);

      const cap = new THREE.Mesh(new THREE.SphereGeometry(0.03, 20, 20), barMaterial);
      cap.position.copy(normal);
      globe.add(cap);
      bars.push({ mesh: bar, cap, normal, height });
      anchors.set(marker.country.country, { normal, top: normal.clone().multiplyScalar(1 + height + 0.05) });

      if (marker.country.open_cases.length > 0) {
        for (const phase of [0, 0.5]) {
          const ring = new THREE.Mesh(
            new THREE.RingGeometry(0.035, 0.045, 48),
            new THREE.MeshBasicMaterial({ color: hex, transparent: true, side: THREE.DoubleSide, depthWrite: false }),
          );
          ring.quaternion.setFromUnitVectors(forward, normal);
          ring.position.copy(normal.clone().multiplyScalar(1.01));
          globe.add(ring);
          rings.push({ mesh: ring, phase });
        }
      }
    }

    // Gerak otomatis hanya beralih di antara negara yang muncul pada data API.
    const orderedMarkers = markers.slice().sort((a, b) => b.country.share - a.country.share);
    const autoTargets = orderedMarkers.map((marker) => rotationFacing(marker.lng, marker.lat));
    let autoTargetIndex = 0;
    let nextAutoTargetAt = 0;
    const start = autoTargets[0] ?? { x: 0.3, y: 0 };
    globe.rotation.set(start.x, start.y, 0);

    // Interaksi: drag untuk memutar
    const pointer = { active: false, x: 0, y: 0, startX: 0, startY: 0, moved: false };
    const canvas = renderer.domElement;
    const raycaster = new THREE.Raycaster();
    const pointerPosition = new THREE.Vector2();
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      pointer.active = true;
      pointer.x = e.clientX;
      pointer.y = e.clientY;
      pointer.startX = e.clientX;
      pointer.startY = e.clientY;
      pointer.moved = false;
      targetRef.current = null;
      clearFocusRef.current();
      canvas.setPointerCapture(e.pointerId);
    };
    const onMove = (e: PointerEvent) => {
      if (!pointer.active) return;
      if (Math.hypot(e.clientX - pointer.startX, e.clientY - pointer.startY) > 6) pointer.moved = true;
      globe.rotation.y += (e.clientX - pointer.x) * 0.006;
      globe.rotation.x = THREE.MathUtils.clamp(globe.rotation.x + (e.clientY - pointer.y) * 0.004, -0.9, 1.1);
      pointer.x = e.clientX;
      pointer.y = e.clientY;
    };
    const selectCountryAt = (clientX: number, clientY: number) => {
      const bounds = canvas.getBoundingClientRect();
      if (bounds.width === 0 || bounds.height === 0) return;
      pointerPosition.x = ((clientX - bounds.left) / bounds.width) * 2 - 1;
      pointerPosition.y = -((clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointerPosition, camera);
      scene.updateMatrixWorld(true);
      const [hit] = raycaster.intersectObject(earth, false);
      if (!hit) return;

      const point = globe.worldToLocal(hit.point.clone()).normalize();
      const lat = 90 - (Math.acos(THREE.MathUtils.clamp(point.y, -1, 1)) / DEG);
      let lng = (Math.atan2(point.z, -point.x) / DEG) - 180;
      if (lng < -180) lng += 360;
      if (lng > 180) lng -= 360;

      const shape = SHAPES.find((item) => containsPoint(item, [lng, lat]));
      const marker = shape && markers.find((item) => item.shape.id === shape.id);
      if (marker) focusCountryRef.current(marker.country.country);
    };
    const onUp = (e: PointerEvent) => {
      if (!pointer.active) return;
      const shouldSelectCountry = !pointer.moved;
      pointer.active = false;
      if (shouldSelectCountry) selectCountryAt(e.clientX, e.clientY);
    };
    const onCancel = () => {
      pointer.active = false;
    };
    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('pointercancel', onCancel);

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = container;
      if (w === 0 || h === 0) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);
    resize();

    // Loop render: hanya berjalan saat globe terlihat dan tab aktif.
    // Intro menumbuhkan batang; rotasi otomatis hanya berpindah antarnegara supplier.
    let frame = 0;
    let visible = false;
    let t0: number | null = null;
    let cameraZ = camera.position.z;
    const world = new THREE.Vector3();
    const toCamera = new THREE.Vector3();
    const rotateTowards = (target: { x: number; y: number }) => {
      if (reduceMotion) {
        globe.rotation.set(target.x, target.y, 0);
        return;
      }
      const dy = Math.atan2(Math.sin(target.y - globe.rotation.y), Math.cos(target.y - globe.rotation.y));
      globe.rotation.y += dy * 0.08;
      globe.rotation.x += (target.x - globe.rotation.x) * 0.08;
    };

    const render = (now: number) => {
      t0 ??= now;
      const elapsed = (now - t0) / 1000;

      // Kamera menyesuaikan posisi kartu saat scroll; globe tetap diam sampai pengguna berinteraksi.
      const box = container.getBoundingClientRect();
      const offset = reduceMotion ? 0 : THREE.MathUtils.clamp(
        (box.top + box.height / 2 - window.innerHeight / 2) / window.innerHeight,
        -1,
        1,
      );
      const cameraTargetZ = (reduceMotion ? 4.1 : 3.75 + Math.abs(offset) * 0.7) - zoomLevelRef.current * 0.34;
      cameraZ += (cameraTargetZ - cameraZ) * (reduceMotion ? 1 : 0.08);
      camera.position.z = cameraZ;
      const progress = reduceMotion ? 1 : easeOutCubic(Math.min(1, elapsed / 1.8));

      dotMaterial.opacity = progress;
      if (!reduceMotion && elapsed < 1.8) {
        globe.scale.setScalar(0.86 + 0.14 * progress);
      }
      for (const { mesh, cap, normal, height } of bars) {
        mesh.scale.y = Math.max(0.0001, height * progress);
        cap.position.copy(normal).multiplyScalar(1 + height * progress);
      }
      for (const { mesh, phase } of rings) {
        const t = reduceMotion ? 0.4 : (elapsed * 0.6 + phase) % 1;
        mesh.scale.setScalar(1 + t * 2.6);
        (mesh.material as THREE.MeshBasicMaterial).opacity = 0.75 * (1 - t);
      }

      const selectedTarget = targetRef.current;
      if (selectedTarget && focusRef.current && !pointer.active) {
        rotateTowards(selectedTarget);
        nextAutoTargetAt = now + AUTO_COUNTRY_INTERVAL_MS;
      } else if (pointer.active || focusRef.current) {
        nextAutoTargetAt = now + AUTO_COUNTRY_INTERVAL_MS;
      } else if (!reduceMotion && autoTargets.length > 1) {
        if (nextAutoTargetAt === 0) nextAutoTargetAt = now + AUTO_COUNTRY_INTERVAL_MS;
        if (now >= nextAutoTargetAt) {
          autoTargetIndex = (autoTargetIndex + 1) % autoTargets.length;
          nextAutoTargetAt = now + AUTO_COUNTRY_INTERVAL_MS;
        }
        rotateTowards(autoTargets[autoTargetIndex]);
      }

      // Label HTML mengikuti ujung batang; disembunyikan saat di sisi belakang globe
      scene.updateMatrixWorld();
      // Ukuran CSS asli (tidak terpengaruh zoom/transform induk), sama dengan satuan translate label.
      const rect = { width: container.clientWidth, height: container.clientHeight };
      for (const [code, anchor] of anchors) {
        const label = labelRefs.current.get(code);
        if (!label) continue;
        world.copy(anchor.top).applyMatrix4(globe.matrixWorld);
        toCamera.copy(camera.position).sub(world).normalize();
        const facing = anchor.normal.clone().transformDirection(globe.matrixWorld).dot(toCamera);
        world.project(camera);
        const x = ((world.x + 1) / 2) * rect.width;
        const y = ((1 - world.y) / 2) * rect.height;
        const opacity = THREE.MathUtils.clamp((facing - 0.15) * 4, 0, 1) * progress;
        label.style.transform = `translate(-50%, -100%) translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
        label.style.opacity = opacity.toFixed(2);
        label.style.pointerEvents = opacity > 0.5 ? 'auto' : 'none';
      }

      renderer.render(scene, camera);
      frame = visible && !document.hidden ? requestAnimationFrame(render) : 0;
    };

    const restart = () => {
      if (!frame && visible && !document.hidden) frame = requestAnimationFrame(render);
    };
    const intersection = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      restart();
    });
    intersection.observe(container);
    document.addEventListener('visibilitychange', restart);
    // Satu frame awal agar kanvas tidak kosong; loop berikutnya dijalankan oleh IntersectionObserver.
    renderer.render(scene, camera);

    return () => {
      cancelAnimationFrame(frame);
      intersection.disconnect();
      resizeObserver.disconnect();
      document.removeEventListener('visibilitychange', restart);
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('pointercancel', onCancel);
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh;
        mesh.geometry?.dispose();
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
        (Array.isArray(material) ? material : material ? [material] : []).forEach((m) => m.dispose());
      });
      sprite.dispose();
      renderer.dispose();
      canvas.remove();
    };
    // Scene dibangun ulang hanya bila dataKey berubah
  }, [dataKey]);

  return (
    <div
      ref={containerRef}
      className="globe-canvas"
      role="group"
      aria-label="Interactive 3D globe of supplier countries coloured by risk level. It cycles through supplier countries. Select a country to hold focus, or drag to rotate."
    >
      {markers.map(({ country }) => (
        <button
          key={country.country}
          type="button"
          className="globe-label"
          aria-label={`Focus ${country.name} on globe`}
          aria-pressed={focus === country.country}
          onClick={() => onFocusCountry(country.country)}
          ref={(el) => {
            if (el) labelRefs.current.set(country.country, el);
            else labelRefs.current.delete(country.country);
          }}
          style={{ opacity: 0 }}
        >
          <span className="globe-label__name">{country.name}</span>
          {country.highest_level ? (
            <Badge variant={country.highest_level}>{riskText(country)}</Badge>
          ) : (
            <Badge variant="NEUTRAL">No open case</Badge>
          )}
        </button>
      ))}
    </div>
  );
}
