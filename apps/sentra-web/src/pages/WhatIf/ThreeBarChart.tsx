import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { SVGObject, SVGRenderer } from 'three/addons/renderers/SVGRenderer.js';

export interface ThreeBarChartDatum {
  label: string;
  value: number;
}

interface ThreeBarChartProps {
  data: ThreeBarChartDatum[];
  color: string;
  label: string;
  formatValue: (value: number) => string;
  formatExactValue?: (value: number) => string;
  replayKey?: number;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
const CHART_HEIGHT = 284;
const BASELINE = 42;
const PLOT_HEIGHT = 190;
const BAR_ANIMATION_MS = 720;
const BAR_STAGGER_MS = 70;

type AnimatedBar = {
  mesh: THREE.Group;
  materials: THREE.MeshBasicMaterial[];
  valueText: SVGElement;
  valueTag: SVGObject;
  x: number;
  height: number;
};

function addLine(scene: THREE.Scene, points: THREE.Vector3[], color: string, opacity = 1) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity });
  scene.add(new THREE.Line(geometry, material));
}

function makeText(
  text: string,
  options: { anchor?: 'start' | 'middle' | 'end'; color?: string; size?: number; weight?: number } = {},
) {
  const element = document.createElementNS(SVG_NS, 'text');
  element.textContent = text;
  element.setAttribute('text-anchor', options.anchor ?? 'middle');
  element.setAttribute('dominant-baseline', 'central');
  element.setAttribute('fill', options.color ?? '#637286');
  element.setAttribute('font-family', 'Inter, Arial, sans-serif');
  element.setAttribute('font-size', String(options.size ?? 12));
  if (options.weight) element.setAttribute('font-weight', String(options.weight));
  return element;
}

function makeFace(points: [number, number][], z: number, material: THREE.MeshBasicMaterial) {
  const shape = new THREE.Shape();
  points.forEach(([x, y], index) => {
    if (index === 0) shape.moveTo(x, y);
    else shape.lineTo(x, y);
  });
  shape.closePath();
  const face = new THREE.Mesh(new THREE.ShapeGeometry(shape), material);
  face.position.z = z;
  return face;
}

function addText(scene: THREE.Scene, text: string, position: THREE.Vector3, options = {}) {
  const tag = new SVGObject(makeText(text, options));
  tag.position.copy(position);
  scene.add(tag);
  return tag;
}

function disposeScene(scene: THREE.Scene) {
  scene.traverse((object) => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach((material) => material.dispose());
    }
  });
  scene.clear();
}

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

export default function ThreeBarChart({
  data,
  color,
  label,
  formatValue,
  formatExactValue = formatValue,
  replayKey = 0,
}: ThreeBarChartProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const barsRef = useRef<AnimatedBar[]>([]);
  const [activeLabel, setActiveLabel] = useState<string | null>(null);
  const activeIndex = Math.max(0, data.findIndex((item) => item.label === activeLabel));

  useEffect(() => {
    barsRef.current.forEach(({ materials, valueText }, index) => {
      const selected = index === activeIndex;
      const base = new THREE.Color(color);
      const dimmed = base.clone().lerp(new THREE.Color('#ffffff'), 0.34);
      const faceShades = [0, -0.16, 0.16];
      materials.forEach((material, face) => {
        material.color.copy(selected ? base : dimmed).offsetHSL(0, 0, faceShades[face] ?? 0);
      });
      valueText.setAttribute('fill', selected ? '#182b40' : '#7a8796');
      valueText.setAttribute('font-weight', selected ? '700' : '500');
    });
    const mount = mountRef.current;
    if (mount && barsRef.current.length) {
      // SVGRenderer is owned by the chart effect and re-rendered after data updates.
      mount.dataset.activeIndex = String(activeIndex);
      mount.dispatchEvent(new Event('whatif-highlight'));
    }
  }, [activeIndex, color]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return undefined;

    const scene = new THREE.Scene();
    const renderer = new SVGRenderer();
    renderer.setQuality('high');
    renderer.setPrecision(2);
    renderer.domElement.setAttribute('role', 'img');
    renderer.domElement.setAttribute('aria-label', label);
    renderer.domElement.setAttribute('focusable', 'false');
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = `${CHART_HEIGHT}px`;
    renderer.domElement.style.overflow = 'visible';
    mount.replaceChildren(renderer.domElement);

    const camera = new THREE.OrthographicCamera(0, 1, CHART_HEIGHT, 0, 0.1, 1000);
    camera.position.set(0, 0, 100);
    const bars: AnimatedBar[] = [];
    let width = 0;
    let disposed = false;
    let settled = false;
    let frame = 0;
    let startedAt: number | null = null;
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const isHidden = () => document.visibilityState === 'hidden';

    const render = () => {
      if (!disposed) renderer.render(scene, camera);
    };

    const applyHighlight = () => {
      const index = Number(mount.dataset.activeIndex ?? 0);
      bars.forEach(({ materials, valueText }, barIndex) => {
        const selected = barIndex === index;
        const base = new THREE.Color(color).lerp(new THREE.Color('#ffffff'), selected ? 0 : 0.34);
        const shades = [0, -0.16, 0.16];
        materials.forEach((material, face) => {
          material.color.copy(base).offsetHSL(0, 0, shades[face] ?? 0);
        });
        valueText.setAttribute('fill', selected ? '#182b40' : '#7a8796');
        valueText.setAttribute('font-weight', selected ? '700' : '500');
      });
    };

    const draw = () => {
      if (disposed) return;
      const nextWidth = Math.max(mount.clientWidth, 360);
      if (nextWidth === width && scene.children.length) {
        render();
        return;
      }
      if (width !== 0) settled = true;
      width = nextWidth;
      disposeScene(scene);
      bars.length = 0;
      barsRef.current = bars;
      renderer.setSize(width, CHART_HEIGHT);
      camera.left = 0;
      camera.right = width;
      camera.top = CHART_HEIGHT;
      camera.bottom = 0;
      camera.updateProjectionMatrix();

      const left = 84;
      const right = width - 22;
      const plotWidth = right - left;
      const maxValue = Math.max(0, ...data.map((item) => item.value));
      const tickCount = 4;
      const tickStep = maxValue > 0 ? maxValue / tickCount : 0;

      for (let tick = 0; tick <= tickCount; tick += 1) {
        const y = BASELINE + (PLOT_HEIGHT * tick) / tickCount;
        addLine(
          scene,
          [new THREE.Vector3(left, y, -2), new THREE.Vector3(right, y, -2)],
          tick === 0 ? '#a5b1be' : '#e8edf2',
          tick === 0 ? 1 : 0.86,
        );
        const tickText = addText(
          scene,
          formatValue(tickStep * tick),
          new THREE.Vector3(left - 12, y, 2),
          { anchor: 'end', size: 12, color: tick === 0 ? '#5f7083' : '#8794a3' },
        );
        tickText.node.setAttribute('aria-hidden', 'true');
      }

      const step = data.length ? plotWidth / data.length : plotWidth;
      const barWidth = Math.min(52, step * 0.48);
      const depth = Math.min(14, barWidth * 0.26);
      data.forEach((item, index) => {
        const x = left + step * (index + 0.5);
        const height = maxValue > 0 ? Math.max(0, item.value / maxValue) * PLOT_HEIGHT : 0;
        const half = barWidth / 2;
        const slant = depth * 0.52;
        const materials = Array.from({ length: 3 }, () => new THREE.MeshBasicMaterial({ color }));
        const mesh = new THREE.Group();
        mesh.add(
          makeFace([[-half, 0], [half, 0], [half, height], [-half, height]], 0.1, materials[0]),
          makeFace([[half, 0], [half + depth, slant], [half + depth, height + slant], [half, height]], 0.3, materials[1]),
          makeFace([[-half, height], [half, height], [half + depth, height + slant], [-half + depth, height + slant]], 0.2, materials[2]),
        );
        mesh.position.set(x, BASELINE, 0);
        mesh.scale.y = 0.001;
        mesh.visible = item.value > 0;
        scene.add(mesh);

        const valueText = makeText(formatValue(item.value), {
          size: 12,
          color: '#182b40',
          weight: 700,
        });
        valueText.setAttribute('opacity', '0');
        const valueTag = new SVGObject(valueText);
        valueTag.position.set(x, BASELINE + height + 18, 5);
        scene.add(valueTag);
        const category = addText(scene, item.label, new THREE.Vector3(x, 20, 4), {
          size: 12,
          color: '#52657b',
          weight: 600,
        });
        category.node.setAttribute('aria-hidden', 'true');
        bars.push({ mesh, materials, valueText, valueTag, x, height });
      });

      applyHighlight();
      if (settled) setProgress(Number.POSITIVE_INFINITY);
      render();
    };

    const setProgress = (elapsed: number) => {
      bars.forEach(({ mesh, valueText, valueTag, x, height }, index) => {
        const progress = clamp01((elapsed - index * BAR_STAGGER_MS) / BAR_ANIMATION_MS);
        const eased = 1 - (1 - progress) ** 3;
        mesh.scale.y = Math.max(eased, 0.001);
        mesh.position.set(x, BASELINE, 0);
        valueText.setAttribute('opacity', String(eased));
        valueTag.position.set(x, BASELINE + height * eased + 18, 5);
      });
    };

    const finishAnimation = () => {
      settled = true;
      if (frame && typeof window.cancelAnimationFrame === 'function') window.cancelAnimationFrame(frame);
      setProgress(Number.POSITIVE_INFINITY);
      render();
    };

    const animate = (timestamp: number) => {
      if (disposed || settled) return;
      if (startedAt === null) startedAt = timestamp;
      const elapsed = timestamp - startedAt;
      setProgress(elapsed);
      render();
      if (elapsed < BAR_ANIMATION_MS + Math.max(0, bars.length - 1) * BAR_STAGGER_MS) {
        frame = window.requestAnimationFrame(animate);
      } else {
        settled = true;
      }
    };

    draw();
    const onHighlight = () => {
      applyHighlight();
      render();
    };
    const onVisibilityChange = () => {
      if (isHidden()) finishAnimation();
    };
    mount.addEventListener('whatif-highlight', onHighlight);
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(draw);
    observer?.observe(mount);
    window.addEventListener('resize', draw);
    window.addEventListener('beforeprint', finishAnimation);
    document.addEventListener('visibilitychange', onVisibilityChange);
    const onMotionChange = () => {
      if (reduceMotion?.matches) finishAnimation();
    };
    reduceMotion?.addEventListener?.('change', onMotionChange);

    if (reduceMotion?.matches || isHidden() || typeof window.requestAnimationFrame !== 'function') {
      finishAnimation();
    } else {
      frame = window.requestAnimationFrame(animate);
    }

    return () => {
      disposed = true;
      if (frame && typeof window.cancelAnimationFrame === 'function') window.cancelAnimationFrame(frame);
      observer?.disconnect();
      mount.removeEventListener('whatif-highlight', onHighlight);
      window.removeEventListener('resize', draw);
      window.removeEventListener('beforeprint', finishAnimation);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      reduceMotion?.removeEventListener?.('change', onMotionChange);
      disposeScene(scene);
      barsRef.current = [];
      mount.replaceChildren();
    };
  }, [color, data, formatValue, label, replayKey]);

  return (
    <div className="whatif-chart-display">
      <div className="whatif-three-chart" ref={mountRef} />
      <div className="whatif-chart-values" aria-label="Chart values">
        <p className="whatif-chart-values__hint">Select a value to highlight it</p>
        <div className="whatif-chart-values__list">
          {data.map((item, index) => (
            <button
              className="whatif-chart-value"
              type="button"
              key={`${item.label}-${index}`}
              aria-pressed={index === activeIndex}
              title={`${item.label}: ${formatExactValue(item.value)}`}
              onClick={() => setActiveLabel(item.label)}
              onFocus={() => setActiveLabel(item.label)}
            >
              <span className="whatif-chart-value__marker" style={{ backgroundColor: color }} aria-hidden="true" />
              <span className="whatif-chart-value__label">{item.label}</span>
              <strong>{formatValue(item.value)}</strong>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
