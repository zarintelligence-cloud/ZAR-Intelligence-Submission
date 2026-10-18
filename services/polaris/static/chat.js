/* Customer conversation component used by both Mission Control and the Store 6 widget. */
window.ZAR = window.ZAR || {};
(function (Z) {
  const E = Z.esc;
  class Chat {
    constructor(root, opts = {}) {
      this.root = root; this.tenant = opts.tenant || "store6"; this.onUpdate = opts.onUpdate || (() => {});
      this.onBusy = opts.onBusy || (() => {}); this.greeting = opts.greeting || "Tell us about your team, event date and colours, and attach your logo.";
      this.session = null; this.logo = null; this.busy = false; this.err = "";
      root.innerHTML = `<div class="chat"><div class="msgs scroll" aria-live="polite"></div><div class="dock"></div></div>`;
      this.msgs = root.querySelector(".msgs"); this.dock = root.querySelector(".dock"); this.render();
    }
    async load(id) { try { this.session = await Z.api(`/v1/sessions/${id}`); } catch (e) { this.session = null; } this.render(); this.onUpdate(this.session); }
    reset() { this.session = null; this.logo = null; this.render(); this.onUpdate(null); }
    async _do(fn) {
      if (this.busy) return; this.busy = true; this.err = ""; this.onBusy(true); this.render();
      try { const s = await fn(); if (s && s.session_id) this.session = s; }
      catch (e) { this.err = e.message; }
      finally { this.busy = false; this.onBusy(false); this.render(); this.onUpdate(this.session); }
    }
    start(message, b64, mime = "image/png", extra = {}) {
      return this._do(() => Z.api("/v1/sessions", { tenant_id: this.tenant, message, logo_b64: b64, logo_mime: mime, ...extra }));
    }
    send(event, data = {}, key) { return this._do(() => Z.api(`/v1/sessions/${this.session.session_id}/events`, { event, data, idempotency_key: key })); }
    token() { return this.session?.roster_path?.split("/").pop(); }
    async fillRoster(entries) {
      return this._do(async () => {
        const tok = this.token(); const info = await Z.api(`/v1/roster/${tok}`);
        if (!entries) {
          const d = await Z.api("/v1/demo/scenarios"); entries = [];
          for (let i = info.submitted; i < info.expected; i++) entries.push({ name: d.names[i % d.names.length], size: d.sizes[i % d.sizes.length], number: info.needs.includes("numbers") ? String(i + 1) : "" });
        }
        await Z.api(`/v1/roster/${tok}/entries:batch`, entries);
        return Z.api(`/v1/sessions/${this.session.session_id}`);
      });
    }
    render() {
      const s = this.session;
      this.msgs.innerHTML = s ? s.messages.map(m => `<div class="msg ${m.role}">${E(m.text)}</div>`).join("")
        : `<div class="msg assistant">Hi! I'm Polaris, the ZAR front desk for ${E(this.tenant === "store6" ? "Store 6" : this.tenant)}. ${E(this.greeting)}</div>`;
      this.msgs.scrollTop = 1e9;
      const ev = s?.allowed_events || [], d = this.dock; let h = "";
      const busy = this.busy ? "disabled" : "";
      if(s?.synthetic) h += `<p class="s-needs_input">Database demo: generated sample artwork, short Player labels and a deadline relative to today. Original history is unchanged.</p>`;
      if (!s) {
        h += `<label class="muted" for="brief">Your brief</label><textarea id="brief" rows="3" placeholder="e.g. 40 cricket jerseys for our company league on 15 Nov, navy and gold, names on the back, budget 25k"></textarea>
          <div class="muted">Logo - pick a sample or upload your own</div>
          <div class="logos" role="group" aria-label="Sample logos"></div>
          <div class="row"><input type="file" accept="image/png,image/jpeg,image/webp" aria-label="Upload logo" style="flex:1">
          <button class="primary" data-a="start" ${busy}>Send brief</button></div>`;
      } else {
        if (ev.includes("customer_reply")) h += `<div class="row"><input data-i="reply" placeholder="Type your answer" aria-label="Your answer" style="flex:1"><button class="primary" data-a="reply" ${busy}>Send</button></div>`;
        if (s.state === "awaiting_approval") h += `<div class="proofs">${s.proofs.map(p => `<button class="proof" data-a="proof" data-p="${p.proof_id}" ${busy}><img src="${Z.assetUrl(p.url)}" alt="Proof ${p.proof_id}: ${E(p.body)} body, ${E(p.trim)} trim"><div><b>${p.proof_id}</b> ${E(p.body)} / ${E(p.trim)}</div>${p.warnings.map(w => `<div class="s-needs_input">${E(w)}</div>`).join("")}</button>`).join("")}</div>`;
        if (s.state === "collecting") {
          const r = s.roster_status || { submitted: 0, expected: s.spec?.quantity || 0 };
          h += `<div class="row" style="justify-content:space-between"><span>Roster <b>${r.submitted}/${r.expected}</b></span><a href="${s.roster_path}" target="_blank" rel="noopener">Open team roster link</a></div>
            <div class="bar"><i style="width:${Math.min(100, 100 * r.submitted / Math.max(1, r.expected))}%"></i></div>
            ${(r.problems || []).map(p => `<div class="s-needs_input">${E(p)}</div>`).join("")}
            <div class="row"><button data-a="fill" ${busy}>Fill with sample team</button><button class="primary" data-a="close" ${busy}>Close roster and quote</button></div>`;
        }
        if (ev.includes("retry")) h += `<button class="primary" data-a="retry" ${busy}>Retry failed step</button>`;
        if (ev.includes("handoff_retry")) h += `<button data-a="handoff" ${busy}>Retry store handoff</button>`;
        if (ev.includes("cancel")) h += `<button data-a="cancel" ${busy}>Cancel request</button>`;
        if (ev.includes("confirmed") && s.quote?.quote?.method !== s.artwork?.print_method) h += `<p class="s-needs_input">Review the updated ${E(s.quote.quote.method)} proof before confirming.</p><div class="proofs">${s.proofs.filter(p => p.proof_id === s.selected_proof).map(p => `<a href="${Z.assetUrl(p.url)}" target="_blank"><img src="${Z.assetUrl(p.url)}" alt="Updated approved proof"></a>`).join("")}</div>`;
        if (ev.includes("confirmed")) h += `<div class="row"><button class="primary" data-a="confirm" ${busy}>Confirm order</button></div>`;
        if (ev.includes("change_request")) h += `<div class="row"><input data-i="change" placeholder="Anything to change? e.g. move delivery to 20 Dec" aria-label="Change request" style="flex:1"><button data-a="change" ${busy}>Request change</button></div>`;
        if (s.state === "confirmed") h += `<div class="row"><span class="chip"><span class="dot" style="background:var(--ok)"></span>Order ${E(s.handoff?.order_ref || "")} · ${s.handoff?.delivered ? (s.handoff?.mode === "sandbox" ? "Saved in local sandbox" : "Sent to Store 6") : "Store handoff pending"}</span><a href="/v1/sessions/${s.session_id}/production-spec" download>Download production specification</a><button data-a="new">Start a new order</button></div>`;
        if (s.state === "cancelled") h += `<div class="row"><button data-a="new">Start a new order</button></div>`;
      }
      if (this.busy) h += `<div class="muted">Working…</div>`;
      if (this.err) h += `<div class="s-error" role="alert">${E(this.err)}</div>`;
      d.innerHTML = h; this._wire();
    }
    async _wire() {
      const d = this.dock, q = s => d.querySelector(s);
      const logos = q(".logos"), file = q('input[type="file"]');
      if (file) file.onchange = async () => { const f = file.files[0]; if (f) this.logo = { kind: "upload", b64: await Z.blobToB64(f), mime: f.type }; logos?.querySelectorAll("button").forEach(x => x.classList.remove("sel")); };
      this._wireButtons();
      if (logos) {
        Z._logoList = Z._logoList || Z.api("/v1/demo/scenarios").then(d => d.logos).catch(() => ({}));
        const L = await Z._logoList;
        if (!logos.isConnected) return;                 // dock re-rendered while loading
        logos.innerHTML = Object.entries(L).map(([k, v]) => `<button data-logo="${k}" title="${E(v)}" aria-label="${E(v)}" class="${this.logo?.kind === k ? "sel" : ""}"><img src="/v1/demo/logos/${k}.png" alt=""></button>`).join("");
        logos.querySelectorAll("button").forEach(b => b.onclick = async () => {
          const blob = await (await fetch(`/v1/demo/logos/${b.dataset.logo}.png`)).blob();
          this.logo = { kind: b.dataset.logo, b64: await Z.blobToB64(blob), mime: "image/png" };
          logos.querySelectorAll("button").forEach(x => x.classList.toggle("sel", x === b));
        });
      }
    }
    _wireButtons() {
      const d = this.dock, q = s => d.querySelector(s);
      d.querySelectorAll("[data-a]").forEach(b => b.onclick = () => {
        const a = b.dataset.a;
        if (a === "start") { const t = q("#brief").value.trim(); if (t.length < 3) { this.err = "Describe what your team needs."; return this.render(); } if (!this.logo) { this.err = "Pick a sample logo or upload yours."; return this.render(); } this.start(t, this.logo.b64, this.logo.mime); }
        if (a === "reply") this.send("customer_reply", { text: q('[data-i="reply"]').value });
        if (a === "proof") this.send("proof_chosen", { proof_id: b.dataset.p });
        if (a === "fill") this.fillRoster();
        if (a === "close") this.send("roster_complete");
        if (a === "confirm") this.send("confirmed", {}, "confirm-" + this.session.session_id);
        if (a === "change") this.send("change_request", { text: q('[data-i="change"]').value });
        if (a === "retry") this.send("retry");
        if (a === "handoff") this.send("handoff_retry");
        if (a === "cancel") this.send("cancel");
        if (a === "new") this.reset();
      });
      d.querySelectorAll("[data-i]").forEach(i => i.onkeydown = e => { if (e.key === "Enter") i.nextElementSibling.click(); });
    }
  }
  Z.Chat = Chat;

  Z.renderSheet = function (el, s) {
    if (!s) { el.innerHTML = `<p class="muted">No order selected. Start a brief or run a demo scenario.</p>`; return; }
    const sp = s.spec || {}, a = s.artwork, f = s.fulfillment, k = s.quote, q = k?.quote, E = Z.esc;
    const row = (t, v) => v || v === 0 ? `<dt>${t}</dt><dd>${E(v)}</dd>` : "";
    let h = `<div class="row" style="justify-content:space-between"><span class="chip"><span class="dot"></span>${E(Z.stateLabel(s.state))}</span><span class="mono dim">${E(s.session_id)}</span></div><h4>Requirements · Rosetta</h4><dl>`;
    h += row("Garment", sp.garment_type) + row("Quantity", sp.quantity) + row("Needed by", sp.deadline) + row("Colours", (sp.colours || []).join(", "))
      + row("Sleeve", sp.sleeve || (s.assumptions?.sleeve ? `${s.assumptions.sleeve} (assumed)` : "")) + row("Personalisation", (sp.personalisation || []).join(", "))
      + row("Budget", sp.budget ? Z.money(sp.budget) : "") + `</dl>`;
    if ((s.dropped_fields || []).length) h += `<p class="s-needs_input">Dropped as ungrounded: ${s.dropped_fields.map(d => `${E(d.field)}=${E(d.value)}`).join(", ")}</p>`;
    if (a) h += `<h4>Artwork · Spectra</h4><dl>${row("Print method", a.print_method)}${row("Rule", a.rule_id)}${row("Allowed", (a.allowed_methods || []).join(", "))}${row("Logo", `${a.measured.width_px}×${a.measured.height_px}px, ${a.measured.colour_count} colours${a.measured.gradient_suspected ? ", gradient" : ""}`)}</dl><p class="muted">${E(a.reasons[0])}</p>${a.file_problems.map(p => `<p class="s-needs_input">${E(p)}</p>`).join("")}`;
    if (f) { const c = f.status === "on_track" ? "var(--ok)" : f.status === "at_risk" ? "var(--warn)" : "var(--bad)";
      h += `<h4>Delivery · Horizon</h4><div class="bar"><i style="width:${Math.round(f.p_on_time * 100)}%;background:${c}"></i></div><p class="muted">${Z.pct(f.p_on_time)} on time · ${f.days_available} days available · p50 ${f.days_p50} d · p90 ${f.days_p90} d</p>${(f.advice || []).map(x => `<p class="s-needs_input">${E(x)}</p>`).join("")}`; }
    if (s.roster_status) h += `<h4>Roster · Pleiades</h4><p>${s.roster_status.submitted}/${s.roster_status.expected} ${s.roster_status.size_breakdown ? "· " + Object.entries(s.roster_status.size_breakdown).map(([k, v]) => `${k} ${v}`).join(", ") : ""}</p>`;
    if (q) h += `<h4>Quote · Kepler</h4><div class="big">${Z.money(q.total, q.currency)}</div><p class="muted">${q.quantity} × ${q.unit_price} ${q.setup_fee ? `+ ${q.setup_fee} setup ` : ""}(${E(q.method)}) · ${E(k.reason)}</p>`;
    if (s.handoff) h += `<h4>Handoff · Store 6</h4><p>${s.handoff.delivered ? `Delivered as <b>${E(s.handoff.order_ref)}</b> (${E(s.handoff.mode)})` : `<span class="s-error">${E(s.handoff.error)}</span>`}</p>`;
    el.innerHTML = `<div class="sheet">${h}</div>`;
  };
})(window.ZAR);
