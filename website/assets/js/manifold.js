// Parametric wireframes, interpolated on a shared mesh. No 3D library required.
(() => {
  const canvas = document.getElementById("manifoldCanvas");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  if (!context) return; // Keep the static SVG if canvas is unavailable.

  const controls = document.getElementById("sculptureControls");
  const toggle = document.getElementById("motionToggle");
  const name = document.getElementById("surfaceName");
  const equation = document.getElementById("surfaceEquation");
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const TAU = Math.PI * 2;
  const cycleSeconds = 4;
  const morphSeconds = 2;
  const columns = 56;
  const rows = 28;
  const count = (columns + 1) * (rows + 1);
  // Use the same computed accent as the SVG fallback, rather than a separate palette.
  const accent = getComputedStyle(canvas.parentElement).color.match(/[\d.]+/g).slice(0, 3).map(Number);
  const surfaces = [
    {
      name: "Torus",
      equation: "(√(x² + y²) − R)² + z² = r²",
      color: accent,
      point(u, v) {
        const radius = 1.65 + 0.66 * Math.cos(v);
        return [radius * Math.cos(u), radius * Math.sin(u), 0.66 * Math.sin(v)];
      }
    },
    {
      name: "Sphere",
      equation: "x² + y² + z² = r²",
      color: accent,
      point(u, v) {
        const radius = 1.9;
        return [radius * Math.sin(v / 2) * Math.cos(u), radius * Math.sin(v / 2) * Math.sin(u), radius * Math.cos(v / 2)];
      }
    },
    {
      name: "Möbius strip",
      equation: "One surface. One edge.",
      color: accent,
      point(u, v) {
        const width = (v / Math.PI - 1) * 0.8;
        const radius = 1.65 + width * Math.cos(u / 2);
        return [radius * Math.cos(u), radius * Math.sin(u), width * Math.sin(u / 2)];
      }
    }
  ];

  // Precompute shape coordinates once. Only interpolation and projection run per frame.
  const meshes = surfaces.map(surface => {
    const mesh = new Float32Array(count * 3);
    for (let i = 0; i <= columns; i++) {
      for (let j = 0; j <= rows; j++) {
        mesh.set(surface.point(TAU * i / columns, TAU * j / rows), (i * (rows + 1) + j) * 3);
      }
    }
    return mesh;
  });
  const edges = [];
  for (let i = 0; i <= columns; i++) {
    for (let j = 0; j <= rows; j++) {
      const index = i * (rows + 1) + j;
      if (j < rows) edges.push([index, index + 1]);
      // Fewer cross-lines let the form read clearly even on a small screen.
      if (i < columns && j % 2 === 0) edges.push([index, index + rows + 1]);
    }
  }

  const current = new Float32Array(meshes[0]);
  const start = new Float32Array(current);
  const projected = new Float32Array(count * 3);
  const color = [...surfaces[0].color];
  let startColor = [...color];
  let target = 0;
  let morphTime = morphSeconds;
  let shapeTime = 0;
  let rotation = 0;
  let width = 1;
  let height = 1;
  let dpr = 1;
  let paused = motion.matches;
  let visible = true;
  let frameId = null;
  let previousTime = null;

  function updateToggle() {
    toggle.textContent = paused ? "Play ▷" : "Pause Ⅱ";
    toggle.setAttribute("aria-label", paused ? "Play animation" : "Pause animation");
  }

  function selectSurface(index) {
    start.set(current);
    startColor = [...color];
    target = index;
    morphTime = 0;
    shapeTime = 0;
    name.textContent = `0${target + 1} / ${surfaces[target].name}`;
    equation.textContent = surfaces[target].equation;
  }

  function interpolate() {
    const progress = Math.min(morphTime / morphSeconds, 1);
    const eased = progress * progress * (3 - 2 * progress);
    const end = meshes[target];
    for (let i = 0; i < current.length; i++) current[i] = start[i] + (end[i] - start[i]) * eased;
    for (let i = 0; i < 3; i++) color[i] = startColor[i] + (surfaces[target].color[i] - startColor[i]) * eased;
  }

  function draw() {
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);
    const ax = 0.95 + Math.sin(rotation * 0.7) * 0.25;
    const ay = -0.3 + rotation * 0.5;
    const az = -0.35 + rotation * 0.18;
    const sx = Math.sin(ax), cx = Math.cos(ax);
    const sy = Math.sin(ay), cy = Math.cos(ay);
    const sz = Math.sin(az), cz = Math.cos(az);
    const scale = Math.min(width, height) * 0.177;

    for (let i = 0; i < current.length; i += 3) {
      const x = current[i], y = current[i + 1], z = current[i + 2];
      const y1 = y * cx - z * sx, z1 = y * sx + z * cx;
      const x2 = x * cy + z1 * sy, z2 = -x * sy + z1 * cy;
      const perspective = 7 / (7 - z2);
      projected[i] = width / 2 + (x2 * cz - y1 * sz) * scale * perspective;
      projected[i + 1] = height / 2 + (x2 * sz + y1 * cz) * scale * perspective;
      projected[i + 2] = z2;
    }

    // Depth-batched strokes: dim back edges, brighter front edges, no heavy glow.
    const bins = Array.from({ length: 8 }, () => []);
    for (const edge of edges) {
      const depth = (projected[edge[0] * 3 + 2] + projected[edge[1] * 3 + 2]) / 2;
      const bin = Math.max(0, Math.min(7, Math.floor((depth + 2.7) / 5.4 * 8)));
      bins[bin].push(edge);
    }
    const rgb = color.map(Math.round).join(",");
    bins.forEach((bin, i) => {
      context.beginPath();
      for (const [a, b] of bin) {
        context.moveTo(projected[a * 3], projected[a * 3 + 1]);
        context.lineTo(projected[b * 3], projected[b * 3 + 1]);
      }
      context.strokeStyle = `rgba(${rgb},${0.12 + i * 0.095})`;
      context.lineWidth = 0.55 + i * 0.065;
      context.stroke();
    });
  }

  function resize() {
    const bounds = canvas.parentElement.getBoundingClientRect();
    width = Math.max(1, bounds.width);
    height = Math.max(1, bounds.height);
    dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    draw();
  }

  function frame(time) {
    frameId = null;
    const delta = previousTime === null ? 0 : Math.min((time - previousTime) / 1000, 0.05);
    previousTime = time;
    rotation += delta * 0.22;
    shapeTime += delta;
    if (shapeTime >= cycleSeconds) selectSurface((target + 1) % surfaces.length);
    morphTime += delta;
    interpolate();
    draw();
    frameId = requestAnimationFrame(frame);
  }

  function syncPlayback() {
    if (frameId !== null) cancelAnimationFrame(frameId);
    frameId = null;
    previousTime = null;
    if (!paused && visible && !document.hidden) frameId = requestAnimationFrame(frame);
    updateToggle();
  }

  toggle.addEventListener("click", () => {
    paused = !paused;
    syncPlayback();
  });
  motion.addEventListener("change", () => {
    paused = motion.matches;
    syncPlayback();
  });
  document.addEventListener("visibilitychange", syncPlayback);
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      syncPlayback();
    }).observe(canvas);
  }
  if ("ResizeObserver" in window) new ResizeObserver(resize).observe(canvas.parentElement);
  window.addEventListener("resize", resize);

  canvas.hidden = false;
  resize();
  canvas.parentElement.querySelector(".manifold-fallback").hidden = true;
  controls.hidden = false;
  syncPlayback();
})();
