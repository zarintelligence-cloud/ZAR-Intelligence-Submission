/* 3D agent orbit on a 2D canvas: perspective projection, drag-to-orbit, depth sorting,
   selection, and an animation queue that replays capability calls as request/response pulses. */
window.ZAR = window.ZAR || {};
(function (Z) {
  const TAU = Math.PI * 2;
  const ELEV = [0.50, -0.22, 0.28, -0.48, 0.12, -0.34, 0.44, -0.06];

  class Orbit {
    constructor(canvas, opts = {}) {
      this.c = canvas; this.ctx = canvas.getContext("2d");
      this.onSelect = opts.onSelect || (() => {}); this.onStatus = opts.onStatus || (() => {});
      this.yaw = 0.35; this.pitch = 0.5; this.auto = true; this.speed = 1;
      this.nodes = [{ ...Z.AGENTS.polaris, center: true, p: [0, 0, 0] }];
      Z.ORDER.forEach((k, i) => {
        const th = i / Z.ORDER.length * TAU, e = ELEV[i % ELEV.length];
        this.nodes.push({ ...Z.AGENTS[k], p: [Math.cos(th) * Math.cos(e), Math.sin(e), Math.sin(th) * Math.cos(e)], glow: 0, busy: 0 });
      });
      this.stars = Array.from({ length: 140 }, () => [Math.random(), Math.random(), Math.random() * 1.2 + .2, Math.random()]);
      this.particles = []; this.queue = []; this.running = false; this.hover = null; this.selected = null; this.caption = null;
      this._bind(); this._resize(); new ResizeObserver(() => this._resize()).observe(canvas);
      requestAnimationFrame(t => this._frame(t));
    }
    _resize() { const r = this.c.getBoundingClientRect(), d = devicePixelRatio || 1; this.c.width = r.width * d; this.c.height = r.height * d; this.w = r.width; this.h = r.height; this.ctx.setTransform(d, 0, 0, d, 0, 0); }
    _bind() {
      let drag = null, moved = 0;
      this.c.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY]; moved = 0; this.c.setPointerCapture(e.pointerId); });
      this.c.addEventListener("pointermove", e => {
        const r = this.c.getBoundingClientRect();
        if (drag) {
          const dx = e.clientX - drag[0], dy = e.clientY - drag[1]; moved += Math.abs(dx) + Math.abs(dy);
          this.yaw += dx * 0.008; this.pitch = Math.max(-1.1, Math.min(1.1, this.pitch + dy * 0.006)); drag = [e.clientX, e.clientY]; this.auto = false;
        }
        this.hover = this._hit(e.clientX - r.left, e.clientY - r.top); this.c.style.cursor = this.hover ? "pointer" : drag ? "grabbing" : "grab";
      });
      this.c.addEventListener("pointerup", e => {
        const r = this.c.getBoundingClientRect(); drag = null;
        if (moved < 6) { const n = this._hit(e.clientX - r.left, e.clientY - r.top); this.select(n ? n.key : null); }
      });
      this.c.addEventListener("pointerleave", () => { this.hover = null; });
      this.c.tabIndex = 0;
      this.c.addEventListener("keydown", e => {
        const keys = this.nodes.map(n => n.key); let i = keys.indexOf(this.selected);
        if (e.key === "ArrowRight" || e.key === "ArrowDown") { this.select(keys[(i + 1) % keys.length]); e.preventDefault(); }
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") { this.select(keys[(i - 1 + keys.length) % keys.length]); e.preventDefault(); }
        if (e.key === "Escape") this.select(null);
      });
    }
    reset() { this.yaw = 0.35; this.pitch = 0.5; this.auto = true; }
    select(key) { this.selected = key; this.onSelect(key); }
    _proj(p) {
      const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
      let x = p[0] * cy - p[2] * sy, z = p[0] * sy + p[2] * cy, y = p[1];
      const y2 = y * cp - z * sp, z2 = y * sp + z * cp;
      const R = Math.min(this.w * 0.36, this.h * 0.47), s = 3.2 / (3.2 + z2);
      return { x: this.w / 2 + x * R * s, y: this.h / 2 + 10 + y2 * R * s * 0.95, s, z: z2 };
    }
    _hit(x, y) {
      let best = null;
      for (const n of this.nodes) { if (!n.sp) continue; const d = Math.hypot(x - n.sp.x, y - n.sp.y); if (d < n.sp.r + 6 && (!best || n.sp.z < best.sp.z)) best = n; }
      return best;
    }
    node(key) { return this.nodes.find(n => n.key === key); }
    /* ---- activity ---- */
    enqueue(runs) { this.queue.push(...runs); if (!this.running) this._drain(); }
    idle() { return new Promise(res => { const t = () => (!this.running && !this.queue.length) ? res() : setTimeout(t, 80); t(); }); }
    async _drain() {
      this.running = true;
      while (this.queue.length) {
        const run = this.queue.shift();
        let burst = 0; while (this.queue.length && this.queue[0].capability === run.capability && run.capability === "roster_management") { this.queue.shift(); burst++; }
        await this._animate(run, burst);
      }
      this.running = false; this.caption = null;
      this.onStatus("idle");
    }
    async _animate(run, burst) {
      const n = this.node(run.capability); if (!n) return;
      const k = 1 / this.speed, a = Z.agentOf(run.capability);
      const label = burst ? `${a.name} × ${burst + 1}` : a.name;
      this.onStatus(`Polaris → ${label}: ${run.capability}`, a.color);
      await this._pulse("polaris", run.capability, "#f0c75e", 520 * k);
      n.busy = 1; this.caption = { key: run.capability, text: `${run.capability} · ${run.status}${burst ? ` ×${burst + 1}` : ""}`, status: run.status };
      await Z.sleep(260 * k);
      this.onStatus(`${label} → Polaris: ${run.status}${run.summary ? " · " + run.summary : ""}`, a.color);
      await this._pulse(run.capability, "polaris", a.color, 460 * k);
      n.busy = 0; n.glow = 1;
    }
    _pulse(from, to, color, ms) {
      return new Promise(res => this.particles.push({ from, to, color, t0: performance.now(), ms, done: res }));
    }
    /* ---- drawing ---- */
    _frame(t) {
      const g = this.ctx, w = this.w, h = this.h;
      if (this.auto) this.yaw += 0.0012;
      g.clearRect(0, 0, w, h);
      const bg = g.createRadialGradient(w / 2, h / 2, 10, w / 2, h / 2, Math.max(w, h) * .7);
      bg.addColorStop(0, "#17181d"); bg.addColorStop(1, "#0b0c0f"); g.fillStyle = bg; g.fillRect(0, 0, w, h);
      for (const s of this.stars) { const x = ((s[0] + this.yaw * 0.02 * s[2]) % 1 + 1) % 1 * w; g.fillStyle = `rgba(200,210,235,${0.15 + 0.35 * s[3]})`; g.fillRect(x, s[1] * h, s[2], s[2]); }
      // orbit rings
      for (const [e, a] of [[0, .14], [0.3, .06], [-0.3, .06]]) {
        g.beginPath();
        for (let i = 0; i <= 96; i++) { const th = i / 96 * TAU, p = this._proj([Math.cos(th) * Math.cos(e), Math.sin(e), Math.sin(th) * Math.cos(e)]); i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y); }
        g.strokeStyle = `rgba(160,170,200,${a})`; g.lineWidth = 1; g.stroke();
      }
      const center = this.nodes[0];
      for (const n of this.nodes) { const p = this._proj(n.p); n.sp = { ...p, r: (n.center ? 30 : 20) * p.s }; }
      // spokes
      for (const n of this.nodes.slice(1)) {
        const active = n.busy || this.caption?.key === n.key || this.selected === n.key;
        g.beginPath(); g.moveTo(center.sp.x, center.sp.y); g.lineTo(n.sp.x, n.sp.y);
        g.setLineDash(active ? [] : [3, 5]); g.strokeStyle = active ? n.color + "cc" : `rgba(150,160,185,${0.10 + 0.14 * n.sp.s})`;
        g.lineWidth = active ? 1.6 : 1; g.stroke(); g.setLineDash([]);
        const mx = (center.sp.x + n.sp.x) / 2, my = (center.sp.y + n.sp.y) / 2; g.fillStyle = n.color + "88"; g.beginPath(); g.arc(mx, my, 1.8, 0, TAU); g.fill();
      }
      // particles
      const now = performance.now();
      this.particles = this.particles.filter(p => {
        const a = this.node(p.from).sp, b = this.node(p.to).sp, u = Math.min(1, (now - p.t0) / p.ms), e = u < .5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
        for (let i = 0; i < 6; i++) { const v = Math.max(0, e - i * .035); const x = a.x + (b.x - a.x) * v, y = a.y + (b.y - a.y) * v;
          g.fillStyle = p.color; g.globalAlpha = 1 - i / 6; g.beginPath(); g.arc(x, y, 3.2 - i * .4, 0, TAU); g.fill(); }
        g.globalAlpha = 1; if (u >= 1) { p.done(); return false; } return true;
      });
      // nodes back-to-front
      for (const n of [...this.nodes].sort((a, b) => b.sp.z - a.sp.z)) this._node(g, n, t);
      requestAnimationFrame(tt => this._frame(tt));
    }
    _node(g, n, t) {
      const { x, y, r, s } = n.sp, sel = this.selected === n.key, hov = this.hover === n, depth = Math.max(.45, Math.min(1, s));
      n.glow = Math.max(0, (n.glow || 0) - 0.012);
      const pulse = n.busy ? .5 + .5 * Math.sin(t / 120) : 0, rr = r * (hov ? 1.08 : 1);
      g.globalAlpha = depth;
      // saucer ring
      g.beginPath(); g.ellipse(x, y + rr * .95, rr * 1.35, rr * .32, 0, 0, TAU); g.strokeStyle = n.color + "44"; g.lineWidth = 1.2; g.stroke();
      // halo
      const halo = g.createRadialGradient(x, y, rr * .4, x, y, rr * (2 + pulse + n.glow));
      halo.addColorStop(0, n.color + (n.center ? "55" : "30")); halo.addColorStop(1, n.color + "00");
      g.fillStyle = halo; g.beginPath(); g.arc(x, y, rr * (2 + pulse + n.glow), 0, TAU); g.fill();
      // body
      const body = g.createRadialGradient(x - rr * .3, y - rr * .35, rr * .1, x, y, rr);
      body.addColorStop(0, n.center ? "#6b5520" : "#3a3f4c"); body.addColorStop(1, n.center ? "#2b220c" : "#1b1e25");
      g.fillStyle = body; g.beginPath(); g.arc(x, y, rr, 0, TAU); g.fill();
      g.lineWidth = sel ? 2.4 : 1.4; g.strokeStyle = sel ? "#f0c75e" : n.color + "bb"; g.stroke();
      if (sel) { g.beginPath(); g.arc(x, y, rr + 5 + 2 * Math.sin(t / 300), 0, TAU); g.strokeStyle = "#f0c75e55"; g.lineWidth = 1; g.stroke(); }
      g.fillStyle = n.color; g.font = `${Math.round(rr * (n.center ? .95 : .9))}px system-ui, "Segoe UI Symbol", "DejaVu Sans", sans-serif`;
      g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(n.glyph, x, y + 1);
      g.fillStyle = "#eef0f4"; g.font = `600 ${Math.round(12.5 + 1.5 * s)}px ${getComputedStyle(document.body).fontFamily}`;
      g.fillText(n.name, x, y + rr + 18);
      g.fillStyle = n.center ? "#b69be8" : "#8d919c"; g.font = `600 ${n.center ? 10 : 9.5}px ${getComputedStyle(document.body).fontFamily}`;
      g.fillText(n.role.toUpperCase(), x, y + rr + 33);
      if (this.caption && this.caption.key === n.key) {
        const txt = this.caption.text; g.font = `11px ${getComputedStyle(document.body).fontFamily}`;
        const tw = g.measureText(txt).width + 16, bx = x - tw / 2, by = y - rr - 34;
        g.fillStyle = "#0e0f12ee"; g.strokeStyle = n.color; g.lineWidth = 1;
        g.beginPath(); g.roundRect ? g.roundRect(bx, by, tw, 22, 6) : g.rect(bx, by, tw, 22); g.fill(); g.stroke();
        g.fillStyle = this.caption.status === "error" ? "#f07178" : this.caption.status === "needs_input" ? "#f2a65a" : "#e9eaef";
        g.fillText(txt, x, by + 11.5);
      }
      g.globalAlpha = 1;
    }
  }
  Z.Orbit = Orbit;
})(window.ZAR);
