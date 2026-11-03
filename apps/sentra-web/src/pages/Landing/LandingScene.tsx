import { useEffect, useRef } from 'react';
import * as THREE from 'three';

type Point = readonly [number, number, number];

const NETWORK_POINTS: Point[] = [
  [-2.1, 0.35, -0.25],
  [-1.62, 1.1, 0.22],
  [-1.08, -0.92, 0.12],
  [-0.72, 0.18, 0.62],
  [-0.22, 1.22, -0.18],
  [0.05, -0.36, 0.94],
  [0.62, 0.45, 0.16],
  [1.12, -0.72, -0.14],
  [1.55, 1.03, 0.34],
  [2.14, 0.14, -0.24],
  [0.96, 1.42, -0.35],
  [-1.3, -1.32, -0.42],
];

const NETWORK_LINKS = [
  [0, 1], [0, 3], [1, 4], [1, 3], [2, 3], [2, 11], [3, 4], [3, 5],
  [4, 6], [4, 10], [5, 6], [5, 7], [6, 7], [6, 8], [7, 9], [8, 9], [8, 10],
] as const;

const SIGNAL_LINKS = [[0, 3], [1, 4], [2, 3], [3, 5], [4, 6], [5, 6], [6, 8], [7, 9], [8, 10]] as const;
const PARTICLE_COUNT = 240;

function disposeScene(scene: THREE.Scene) {
  scene.traverse((object) => {
    const visual = object as THREE.Mesh<THREE.BufferGeometry, THREE.Material | THREE.Material[]>;
    visual.geometry?.dispose();
    const materials = Array.isArray(visual.material) ? visual.material : visual.material ? [visual.material] : [];
    materials.forEach((material) => material.dispose());
  });
}

/** Decorative Three.js scene for the public landing page. It has no business data or user input. */
export default function LandingScene() {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    // JSDOM does not provide a useful WebGL lifecycle; keep the CSS fallback in component tests.
    if (!container || !canvas || import.meta.env.MODE === 'test' || typeof window.WebGLRenderingContext === 'undefined') return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, canvas, powerPreference: 'high-performance' });
    } catch {
      return;
    }

    container.dataset.webgl = 'ready';
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(37, 1, 0.1, 100);
    camera.position.set(0, 0.15, 7.25);

    const root = new THREE.Group();
    root.position.set(-0.08, 0, 0);
    scene.add(root);

    const haze = new THREE.Mesh(
      new THREE.SphereGeometry(2.52, 36, 36),
      new THREE.MeshBasicMaterial({ color: '#0d5f94', transparent: true, opacity: 0.075, side: THREE.BackSide }),
    );
    root.add(haze);

    const core = new THREE.Group();
    const coreMesh = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.82, 2),
      new THREE.MeshBasicMaterial({ color: '#6ce7ed', transparent: true, opacity: 0.16, wireframe: true }),
    );
    core.add(coreMesh);
    const coreShell = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.51, 2),
      new THREE.MeshBasicMaterial({ color: '#61c8ff', transparent: true, opacity: 0.1 }),
    );
    core.add(coreShell);
    root.add(core);

    const orbitA = new THREE.Mesh(
      new THREE.TorusGeometry(1.35, 0.012, 8, 96),
      new THREE.MeshBasicMaterial({ color: '#66d9ea', transparent: true, opacity: 0.58 }),
    );
    orbitA.rotation.set(0.65, -0.42, 0.08);
    root.add(orbitA);
    const orbitB = new THREE.Mesh(
      new THREE.TorusGeometry(1.78, 0.008, 8, 96),
      new THREE.MeshBasicMaterial({ color: '#5d95ef', transparent: true, opacity: 0.42 }),
    );
    orbitB.rotation.set(-0.92, 0.43, 0.28);
    root.add(orbitB);

    const pointPositions = new Float32Array(NETWORK_POINTS.length * 3);
    NETWORK_POINTS.forEach((point, index) => pointPositions.set(point, index * 3));
    const pointGeometry = new THREE.BufferGeometry();
    pointGeometry.setAttribute('position', new THREE.BufferAttribute(pointPositions, 3));
    const pointMaterial = new THREE.PointsMaterial({
      color: '#a9f5f3',
      size: 0.07,
      transparent: true,
      opacity: 0.93,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    root.add(new THREE.Points(pointGeometry, pointMaterial));

    const linkPositions = new Float32Array(NETWORK_LINKS.length * 6);
    const linkColors = new Float32Array(NETWORK_LINKS.length * 6);
    const dimColor = new THREE.Color('#2c719f');
    const brightColor = new THREE.Color('#68e6df');
    NETWORK_LINKS.forEach(([from, to], index) => {
      const offset = index * 6;
      linkPositions.set(NETWORK_POINTS[from], offset);
      linkPositions.set(NETWORK_POINTS[to], offset + 3);
      const highlight = from === 5 || to === 5 || from === 6 || to === 6;
      const lineColor = highlight ? brightColor : dimColor;
      lineColor.toArray(linkColors, offset);
      lineColor.toArray(linkColors, offset + 3);
    });
    const linkGeometry = new THREE.BufferGeometry();
    linkGeometry.setAttribute('position', new THREE.BufferAttribute(linkPositions, 3));
    linkGeometry.setAttribute('color', new THREE.BufferAttribute(linkColors, 3));
    root.add(new THREE.LineSegments(
      linkGeometry,
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.78, depthWrite: false, blending: THREE.AdditiveBlending }),
    ));

    const activeNode = new THREE.Mesh(
      new THREE.SphereGeometry(0.145, 20, 20),
      new THREE.MeshBasicMaterial({ color: '#7affeb', transparent: true, opacity: 0.92 }),
    );
    activeNode.position.fromArray(NETWORK_POINTS[5]);
    root.add(activeNode);
    const activeHalo = new THREE.Mesh(
      new THREE.TorusGeometry(0.24, 0.009, 8, 48),
      new THREE.MeshBasicMaterial({ color: '#90f7f2', transparent: true, opacity: 0.75 }),
    );
    activeHalo.position.copy(activeNode.position);
    activeHalo.rotation.x = Math.PI * 0.52;
    root.add(activeHalo);

    // A single THREE.Points draw call keeps the ambient particle field inexpensive.
    const particles = new Float32Array(PARTICLE_COUNT * 3);
    const particleColors = new Float32Array(PARTICLE_COUNT * 3);
    const particleColorNear = new THREE.Color('#69e5e2');
    const particleColorFar = new THREE.Color('#4b8dde');
    const particleColor = new THREE.Color();
    for (let index = 0; index < PARTICLE_COUNT; index += 1) {
      const theta = index * 2.39996;
      const radius = 1.7 + ((index * 43) % 100) / 74;
      const height = (((index * 29) % 100) / 100 - 0.5) * 4.2;
      particles[index * 3] = Math.cos(theta) * radius;
      particles[index * 3 + 1] = height;
      particles[index * 3 + 2] = Math.sin(theta) * radius * 0.46 - 0.6;
      particleColor.copy(particleColorFar).lerp(particleColorNear, ((index * 17) % 100) / 100).toArray(particleColors, index * 3);
    }
    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute('position', new THREE.BufferAttribute(particles, 3));
    particleGeometry.setAttribute('color', new THREE.BufferAttribute(particleColors, 3));
    const particleMaterial = new THREE.PointsMaterial({
      vertexColors: true,
      size: 0.029,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.62,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const particleField = new THREE.Points(particleGeometry, particleMaterial);
    root.add(particleField);

    const pulsePositions = new Float32Array(SIGNAL_LINKS.length * 3);
    const pulseGeometry = new THREE.BufferGeometry();
    const pulseAttribute = new THREE.BufferAttribute(pulsePositions, 3);
    pulseAttribute.setUsage(THREE.DynamicDrawUsage);
    pulseGeometry.setAttribute('position', pulseAttribute);
    const signalPulses = new THREE.Points(
      pulseGeometry,
      new THREE.PointsMaterial({
        color: '#d1fff7',
        size: 0.105,
        sizeAttenuation: true,
        transparent: true,
        opacity: 0.94,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    root.add(signalPulses);

    const updateSignalPulses = (elapsed: number) => {
      SIGNAL_LINKS.forEach(([fromIndex, toIndex], index) => {
        const from = NETWORK_POINTS[fromIndex];
        const to = NETWORK_POINTS[toIndex];
        const progress = (elapsed * 0.24 + index / SIGNAL_LINKS.length) % 1;
        const easedProgress = progress * progress * (3 - 2 * progress);
        const offset = index * 3;
        pulsePositions[offset] = THREE.MathUtils.lerp(from[0], to[0], easedProgress);
        pulsePositions[offset + 1] = THREE.MathUtils.lerp(from[1], to[1], easedProgress);
        pulsePositions[offset + 2] = THREE.MathUtils.lerp(from[2], to[2], easedProgress);
      });
      pulseAttribute.needsUpdate = true;
    };
    updateSignalPulses(0);

    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const pointer = { x: 0, y: 0, targetX: 0, targetY: 0 };
    const onPointerMove = (event: PointerEvent) => {
      const bounds = container.getBoundingClientRect();
      if (bounds.width === 0 || bounds.height === 0) return;
      pointer.targetX = ((event.clientX - bounds.left) / bounds.width - 0.5) * 0.72;
      pointer.targetY = ((event.clientY - bounds.top) / bounds.height - 0.5) * 0.42;
    };
    const onPointerLeave = () => {
      pointer.targetX = 0;
      pointer.targetY = 0;
    };
    container.addEventListener('pointermove', onPointerMove);
    container.addEventListener('pointerleave', onPointerLeave);

    const resize = () => {
      const { width, height } = container.getBoundingClientRect();
      if (width === 0 || height === 0) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    // The scene fills a fixed hero panel; window resize is sufficient and avoids a canvas/ResizeObserver feedback loop.
    window.addEventListener('resize', resize);
    resize();

    let isVisible = true;
    const observer = new IntersectionObserver(([entry]) => {
      isVisible = entry.isIntersecting;
      if (isVisible && !frame) frame = requestAnimationFrame(render);
    });
    observer.observe(container);

    let frame = 0;
    const clock = new THREE.Clock();
    const render = () => {
      const elapsed = clock.getElapsedTime();
      pointer.x += (pointer.targetX - pointer.x) * 0.055;
      pointer.y += (pointer.targetY - pointer.y) * 0.055;

      if (!reducedMotion) {
        root.rotation.y = elapsed * 0.09 + pointer.x;
        root.rotation.x = pointer.y * 0.72;
        root.position.y = Math.sin(elapsed * 0.72) * 0.075;
        core.rotation.set(elapsed * 0.35, elapsed * -0.24, elapsed * 0.13);
        orbitA.rotation.z = elapsed * 0.18;
        orbitB.rotation.z = -elapsed * 0.13;
        activeHalo.scale.setScalar(1 + Math.sin(elapsed * 2.2) * 0.08);
        activeNode.scale.setScalar(1 + Math.sin(elapsed * 2.2) * 0.11);
        particleField.rotation.y = -elapsed * 0.04;
        updateSignalPulses(elapsed);
      }

      renderer.render(scene, camera);
      frame = isVisible && !document.hidden && !reducedMotion ? requestAnimationFrame(render) : 0;
    };
    render();

    const wake = () => {
      if (isVisible && !document.hidden && !frame && !reducedMotion) frame = requestAnimationFrame(render);
    };
    document.addEventListener('visibilitychange', wake);

    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('visibilitychange', wake);
      observer.disconnect();
      window.removeEventListener('resize', resize);
      container.removeEventListener('pointermove', onPointerMove);
      container.removeEventListener('pointerleave', onPointerLeave);
      disposeScene(scene);
      renderer.dispose();
      delete container.dataset.webgl;
    };
  }, []);

  return (
    <div ref={containerRef} className="landing-scene" aria-hidden="true">
      <canvas ref={canvasRef} className="landing-scene__canvas" />
      <div className="landing-scene__fallback">
        <span className="landing-scene__fallback-core" />
        <span className="landing-scene__fallback-ring landing-scene__fallback-ring--one" />
        <span className="landing-scene__fallback-ring landing-scene__fallback-ring--two" />
      </div>
    </div>
  );
}
