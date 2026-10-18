/* Mission Control: live orbit, conversation, trace timeline, inspector, demo autopilot, ops views. */
(function (Z) {
  const $ = id => document.getElementById(id), E = Z.esc, TENANT = "store6";
  const st = { session: null, trace: null, seen: new Set(), selStep: null, selRun: null, agent: null, rev: -1, tab: "summary", pilot: null, list: [] };

  /* ---------- summaries used by orbit captions and status line ---------- */
  function summary(r) {
    const d = r.response?.data || {}, q = r.response?.questions || [];
    switch (r.capability) {
      case "requirements_analysis": return q.length ? `${q.length} question${q.length > 1 ? "s" : ""}` : "spec complete";
      case "artwork_analysis": return `${d.print_method} (${d.rule_id})`;
      case "proof_generation": return `${(d.proofs || []).length} proofs`;
      case "roster_management": return d.token ? "roster created" : d.submitted != null ? `${d.submitted}/${d.expected}${d.ready ? " ready" : ""}` : r.status;
      case "pricing": return d.quote ? Z.money(d.quote.total) : "";
      case "fulfillment_confidence": return d.p_on_time != null ? `${Z.pct(d.p_on_time)} on time` : "";
      case "order_handoff": return d.order_ref || r.error || "";
      default: return r.model_used || "";
    }
  }

  /* ---------- orbit ---------- */
  const orbit = new Z.Orbit($("orbit"), {
    onSelect: key => { st.agent = key; if (key) { st.selRun = null; st.selStep = null; } renderInspector(); renderTimeline(); },
    onStatus: (txt, color) => {
      if (txt === "idle") { setStatus(idleText(), "#f0c75e"); return; }
      setStatus(txt, color);
    },
  });
  function setStatus(t, c) { $("status").textContent = t; $("sdot").style.background = c || "#f0c75e"; }
  function idleText() {
    const s = st.session; if (!s) return "Ready for a custom-order brief.";
    return { asking: "Waiting for the customer's answer.", awaiting_approval: "Proofs sent - waiting for the customer's pick.",
      collecting: "Collecting the team roster.", awaiting_confirmation: "Quote sent - waiting for confirmation.",
      confirmed: `Order confirmed · ${s.handoff?.delivered ? (s.handoff.mode === "sandbox" ? "saved in local sandbox" : "delivered to Store 6") : "handoff pending"}.`, cancelled: "Order cancelled." }[s.state] || Z.stateLabel(s.state);
  }
  $("resetv").onclick = () => orbit.reset();

  /* ---------- chat ---------- */
  const chat = new Z.Chat($("p-conv"), { tenant: TENANT, onUpdate: s => onSession(s, true) });
  document.querySelectorAll(".subtabs button").forEach(b => b.onclick = () => {
    document.querySelectorAll(".subtabs button").forEach(x => x.setAttribute("aria-selected", x === b));
    $("p-conv").hidden = b.dataset.p !== "conv"; $("p-sheet").hidden = b.dataset.p !== "sheet";
  });

  async function onSession(s, animate) {
    const changed = (s?.session_id || null) !== (st.session?.session_id || null);
    st.session = s; Z.renderSheet($("sheet"), s);
    if (changed) { st.seen = new Set(); st.selStep = st.selRun = null; st.rev = -1; loadList(); }
    if (!s) { st.trace = null; renderTimeline(); renderInspector(); setStatus(idleText()); return; }
    st.rev = s.rev; await refreshTrace(!!animate);
    const item = st.list.find(x => x.session_id === s.session_id); if(item) item.state = s.state;
    syncPicker(); if (!orbit.running) setStatus(idleText());
  }
  async function refreshTrace(animate) {
    if (!st.session) return;
    try { st.trace = await Z.api(`/v1/sessions/${st.session.session_id}/trace`); } catch (e) { return; }
    const fresh = [];
    for (const step of st.trace.steps) for (const r of step.runs) if (!st.seen.has(r.request_id)) { st.seen.add(r.request_id); fresh.push({ ...r, summary: summary(r) }); }
    if (animate && fresh.length) orbit.enqueue(fresh);
    renderTimeline(); renderInspector();
  }
  async function openSession(id, animate) {
    const s = id ? await Z.api(`/v1/sessions/${id}`).catch(() => null) : null;
    chat.session = s; chat.render(); st.session = null; await onSession(s, animate ? "all" : false);
  }
  $("replay").onclick = () => { if (!st.trace) return; const runs = st.trace.steps.flatMap(s => s.runs.map(r => ({ ...r, summary: summary(r) }))); orbit.enqueue(runs); };

  /* ---------- session picker + live follow ---------- */
  async function loadList() { st.list = await Z.api(`/v1/sessions?tenant_id=${TENANT}`).catch(() => []); syncPicker(); return st.list; }
  function syncPicker() {
    const cur = st.session?.session_id, opts = st.list.map(x => `<option value="${x.session_id}" ${x.session_id === cur ? "selected" : ""}>${E((x.title || "").slice(0, 38))} · ${E(Z.stateLabel(x.state))}</option>`);
    $("sessionpick").innerHTML = `<option value="">New order…</option>` + opts.join("");
    if (cur && !st.list.some(x => x.session_id === cur)) $("sessionpick").insertAdjacentHTML("beforeend", `<option value="${cur}" selected>Current order</option>`);
  }
  $("sessionpick").onchange = e => { $("follow").checked = false; e.target.value ? openSession(e.target.value) : chat.reset(); };
  setInterval(async () => {
    if (chat.busy || st.pilot) return;
    if ($("follow").checked) {
      const list = await loadList(); const top = list[0];
      if (top && top.session_id !== st.session?.session_id && Date.now() - new Date(top.updated_at) < 120000) { await openSession(top.session_id, true); return; }
    }
    if (st.session) {
      const s = await Z.api(`/v1/sessions/${st.session.session_id}`).catch(() => null);
      if (s && s.rev !== st.rev) { chat.session = s; chat.render(); await onSession(s, true); }
    }
  }, 1600);

  /* ---------- timeline ---------- */
  function groups() {
    const out = []; if (!st.trace) return out;
    for (const s of st.trace.steps) {
      const last = out[out.length - 1];
      if (s.event === "roster_entry" && !s.message && last && last.group && last.steps[0].event === "roster_entry") last.steps.push(s);
      else out.push({ group: s.event === "roster_entry" && !s.message, steps: [s] });
    }
    return out;
  }
  function runChip(r) { const a = Z.agentOf(r.capability); return `<button class="run ${st.selRun === r.request_id ? "sel" : ""}" data-run="${r.request_id}" title="${E(r.capability)}"><i style="background:${a.color}"></i>${E(a.name)} <span class="s-${r.status}">${r.status}</span> <span class="dim">${Math.round(r.latency_ms)}ms</span></button>`; }
  function renderTimeline() {
    const el = $("timeline"); const G = groups();
    if (!st.trace) { el.innerHTML = `<p class="muted" style="padding:8px">Every workflow step, capability call and decision for the selected order appears here.</p>`; $("tcount").textContent = ""; return; }
    const runs = st.trace.steps.reduce((n, s) => n + s.runs.length, 0);
    $("tcount").textContent = `${st.trace.steps.length} steps · ${runs} agent calls`;
    const filt = st.agent && st.agent !== "polaris" ? st.agent : null;
    el.innerHTML = G.map(g => {
      const s = g.steps[0], last = g.steps[g.steps.length - 1];
      let rs = g.steps.flatMap(x => x.runs); if (filt) rs = rs.filter(r => r.capability === filt);
      if (filt && !rs.length) return "";
      const head = g.group && g.steps.length > 1
        ? `<span class="seq">#${s.seq}-${last.seq}</span><span class="ev">roster_entry × ${g.steps.length}</span><span class="tr">collecting</span><span class="dim">add_roster_entry</span>`
        : `<span class="seq">#${s.seq}</span><span class="ev">${E(s.event)}</span>${s.origin === "auto" ? `<span class="chip">auto</span>` : ""}<span class="tr">${E(s.from)} → ${E(s.to)}</span><span class="dim">${E(s.handler || "")} · ${Math.round(s.duration_ms || 0)}ms</span>${s.error ? `<span class="s-error">${E(s.error)}</span>` : ""}`;
      const shown = g.group ? rs.slice(0, 6) : rs;
      const decs = g.steps.flatMap(x => x.decisions).map(d => `<button class="dec" data-dec="${d.decision_id}">Decision · ${E(d.goal)} = ${E(d.action)} (${E(d.engine)})</button>`).join("");
      return `<div class="step ${st.selStep === s.step_id ? "sel" : ""}"><div class="sh" data-step="${s.step_id}" tabindex="0">${head}</div>
        <div class="runs">${shown.map(runChip).join("")}${rs.length > shown.length ? `<span class="chip">+${rs.length - shown.length} more</span>` : ""}${decs}</div>
        ${!g.group && s.message ? `<div class="said">${E(s.message.slice(0, 220))}${s.message.length > 220 ? "…" : ""}</div>` : ""}</div>`;
    }).join("") || `<p class="muted" style="padding:8px">No calls to ${E(Z.agentOf(filt).name)} in this order yet.</p>`;
    el.querySelectorAll("[data-run]").forEach(b => b.onclick = () => { st.selRun = b.dataset.run; st.selStep = null; st.agent = null; orbit.selected = runById(st.selRun)?.capability; st.tab = "summary"; renderTimeline(); renderInspector(); });
    el.querySelectorAll("[data-step]").forEach(b => { const f = () => { st.selStep = b.dataset.step; st.selRun = null; st.agent = null; orbit.selected = "polaris"; renderTimeline(); renderInspector(); }; b.onclick = f; b.onkeydown = e => e.key === "Enter" && f(); });
    el.querySelectorAll("[data-dec]").forEach(b => b.onclick = () => { const step = st.trace.steps.find(s => s.decisions.some(d => d.decision_id === b.dataset.dec)); st.selStep = step.step_id; st.selRun = null; st.agent = null; st.tab = "decisions"; renderTimeline(); renderInspector(); });
  }
  const runById = id => st.trace?.steps.flatMap(s => s.runs).find(r => r.request_id === id);
  const stepOfRun = r => st.trace.steps.find(s => s.step_id === r.step_id);

  /* ---------- inspector ---------- */
  function actionBox(step, run) {
    const items = [];
    items.push(`Event <b>${E(step.event)}</b> ${step.origin === "auto" ? "(emitted automatically)" : "(from the customer/channel)"} arrived in state <b>${E(step.from)}</b>.`);
    items.push(`Polaris ran workflow handler <span class="mono">${E(step.handler)}</span>${run ? `, which called <b>${E(Z.agentOf(run.capability).name)}</b> (<span class="mono">${E(run.capability)}</span>) → <span class="s-${run.status}">${run.status}</span>` : ""}.`);
    if (step.decisions.length) step.decisions.forEach(d => items.push(`Decision recorded: <b>${E(d.goal)} = ${E(d.action)}</b> by ${E(d.engine)} - ${E(d.reasons[0] || "")}`));
    items.push(step.from === step.to ? `State stayed <b>${E(step.to)}</b>.` : `State moved <b>${E(step.from)} → ${E(step.to)}</b>.`);
    if (step.emitted) items.push(`Emitted follow-up event <b>${E(step.emitted)}</b>.`);
    if (step.message) items.push(`Customer was told: “${E(step.message.slice(0, 300))}${step.message.length > 300 ? "…" : ""}”`);
    if (step.error) items.push(`<span class="s-error">Error: ${E(step.error)} - state kept, customer can retry.</span>`);
    return `<div class="action"><b>Action taken</b><ol>${items.map(i => `<li>${i}</li>`).join("")}</ol></div>`;
  }
  function tabs(list) {
    return `<div class="itabs" role="tablist">${list.map(([k, t]) => `<button data-t="${k}" aria-selected="${st.tab === k}">${t}</button>`).join("")}</div>`;
  }
  function renderInspector() {
    const el = $("inspector"), crumb = $("icrumb");
    if (st.selRun && runById(st.selRun)) {
      const r = runById(st.selRun), a = Z.agentOf(r.capability), step = stepOfRun(r);
      crumb.textContent = `${a.name} · ${r.capability}`;
      if (!["summary", "request", "response", "debug", "decisions"].includes(st.tab)) st.tab = "summary";
      const resp = { ...r.response }; const dbg = resp.debug || {}; delete resp.debug;
      let body = "";
      if (st.tab === "summary") body = `${actionBox(step, r)}<dl class="kv"><dt>Agent</dt><dd><span style="color:${a.color}">${a.glyph}</span> ${E(a.name)} - ${E(r.provider)}</dd><dt>Capability</dt><dd class="mono">${E(r.capability)}</dd>
          <dt>Status</dt><dd class="s-${r.status}">${r.status}${r.error ? " · " + E(r.error) : ""}</dd><dt>Latency</dt><dd>${r.latency_ms} ms</dd><dt>Model</dt><dd>${E(r.model_used || "none")}</dd>
          <dt>Confidence</dt><dd>${r.confidence ?? "-"}</dd><dt>Questions</dt><dd>${(r.response.questions || []).map(E).join("<br>") || "-"}</dd><dt>Evidence</dt><dd>${(r.response.evidence || []).map(e => `<span class="chip">${E(e.key)} · ${E(e.source)} · ${e.confidence}</span>`).join(" ") || "-"}</dd>
          <dt>Request ID</dt><dd class="mono dim">${E(r.request_id)}</dd><dt>Release</dt><dd class="mono dim">${E(r.release.release_id)} · core ${E(r.release["zar-core"])}</dd></dl>`;
      if (st.tab === "request") body = `<p class="muted">CapabilityRequest as sent by Polaris (binaries redacted).</p><pre class="json">${Z.json(r.request)}</pre>`;
      if (st.tab === "response") body = `<p class="muted">CapabilityResult returned by ${E(a.name)}.</p><pre class="json">${Z.json(resp)}</pre>`;
      if (st.tab === "debug") { const p = dbg.prompt; const rest = { ...dbg }; delete rest.prompt;
        delete rest.model_called;
        body = (p ? `<p class="muted">Prompt formed for the model${dbg.model_called === false ? " - <b>not sent</b>: offline mode used the deterministic baseline" : " and sent to " + E(r.model_used)}</p><pre class="prompt">${E(p)}</pre>` : "") + `<p class="muted">Internal reasoning trace</p><pre class="json">${Z.json(rest)}</pre>`; }
      if (st.tab === "decisions") body = step.decisions.length ? step.decisions.map(d => `<pre class="json">${Z.json(d)}</pre>`).join("") : `<p class="muted">No decision was recorded in this step.</p>`;
      el.innerHTML = tabs([["summary", "Summary & action"], ["request", "Request"], ["response", "Response"], ["debug", "Debug"], ["decisions", `Decisions (${step.decisions.length})`]]) + body;
    } else if (st.selStep && st.trace) {
      const s = st.trace.steps.find(x => x.step_id === st.selStep); crumb.textContent = `Step #${s.seq} · ${s.event}`;
      if (!["summary", "decisions", "raw"].includes(st.tab)) st.tab = "summary";
      let body = st.tab === "summary" ? actionBox(s) + `<div class="runs" style="margin:0">${s.runs.map(runChip).join("")}</div>`
        : st.tab === "decisions" ? (s.decisions.map(d => `<pre class="json">${Z.json(d)}</pre>`).join("") || `<p class="muted">No decisions in this step.</p>`)
        : `<pre class="json">${Z.json({ ...s, runs: s.runs.map(r => r.request_id), decisions: s.decisions.map(d => d.decision_id) })}</pre>`;
      el.innerHTML = tabs([["summary", "Action taken"], ["decisions", `Decisions (${s.decisions.length})`], ["raw", "Step record"]]) + body;
      el.querySelectorAll("[data-run]").forEach(b => b.onclick = () => { st.selRun = b.dataset.run; st.selStep = null; st.tab = "summary"; renderTimeline(); renderInspector(); });
    } else if (st.agent) {
      const a = Z.AGENTS[st.agent]; crumb.textContent = a.name;
      const runs = st.trace ? st.trace.steps.flatMap(s => s.runs).filter(r => r.capability === st.agent) : [];
      const steps = st.trace ? st.trace.steps.length : 0;
      el.innerHTML = `<div class="agenthead"><div class="glyph" style="color:${a.color};border-color:${a.color}">${a.glyph}</div><div><h3 style="margin:0">${E(a.name)} <span class="muted" style="font-weight:400">· ${E(a.role)}</span></h3><div class="mono dim">${E(a.key === "polaris" ? "orchestrator" : a.key)}</div></div></div>
        <p>${E(a.desc)}</p><dl class="kv"><dt>How it works</dt><dd>${E(a.kind)}</dd><dt>Model</dt><dd>${E(a.model)}</dd><dt>Policy</dt><dd class="mono">${E(a.policy)}</dd>
        <dt>In this order</dt><dd>${a.key === "polaris" ? `${steps} workflow steps` : `${runs.length} call(s)${runs.filter(r => r.status === "error").length ? `, ${runs.filter(r => r.status === "error").length} error(s)` : ""}`}</dd></dl>
        ${runs.length ? `<p class="muted" style="margin-top:12px">Calls (newest first) - select one for request, response and action</p><div class="runs" style="margin:0">${runs.slice(-24).reverse().map(runChip).join("")}</div>` : ""}`;
      el.querySelectorAll("[data-run]").forEach(b => b.onclick = () => { st.selRun = b.dataset.run; st.tab = "summary"; renderTimeline(); renderInspector(); });
    } else {
      crumb.textContent = "";
      el.innerHTML = `<p class="muted">Select an agent in the orbit, a step or an agent call in the trace to see the request that was formed, the response, the model prompt and the action Polaris took.</p>`;
    }
    el.querySelectorAll("[data-t]").forEach(b => b.onclick = () => { st.tab = b.dataset.t; renderInspector(); });
  }
  $("dl").onclick = () => { if (!st.trace) return; const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify({ session: st.session, trace: st.trace }, null, 2)], { type: "application/json" })); a.download = `zar-trace-${st.session.session_id}.json`; a.click(); };

  /* ---------- views ---------- */
  function show(v) {
    document.querySelectorAll("nav.tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.v === v));
    document.querySelectorAll(".view").forEach(x => x.classList.toggle("on", x.id === "v-" + v));
    ({ orders: renderOrders, agents: renderAgents, system: renderSystem, scenarios: renderScenarios })[v]?.();
  }
  document.querySelectorAll("nav.tabs button").forEach(b => b.onclick = () => show(b.dataset.v));

  async function renderOrders() {
    const list = await loadList();
    $("otable").innerHTML = list.map(x => `<tr data-id="${x.session_id}"><td>${E(x.title || x.session_id)}<div class="mono dim">${x.session_id}${x.scenario ? " · demo: " + E(x.scenario) : ""}</div></td><td>${E(Z.stateLabel(x.state))}</td><td>${E(x.garment || "")}</td><td>${x.quantity || ""}</td><td>${x.total ? Z.money(x.total, x.currency) : ""}</td><td>${Z.ago(x.updated_at)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No orders yet.</td></tr>`;
    $("otable").querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = () => { $("follow").checked = false; show("live"); openSession(tr.dataset.id); });
    const inbox = await Z.api(`/v1/handoffs?tenant_id=${TENANT}`).catch(() => []);
    $("inbox").innerHTML = inbox.map(h => { const q = h.production_spec.quote, sp = h.production_spec.spec;
      return `<div class="card"><h3>${E(h.order_ref)} <span class="chip">${E(h.mode)}</span></h3><p>${sp.quantity} × ${E(sp.garment_type)} · ${E(h.production_spec.print_method)} · needed by ${E(sp.deadline)}</p>
        <p class="big" style="font-size:20px">${Z.money(q.total, q.currency)}</p><p class="muted">${Object.entries(h.production_spec.size_breakdown).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>
        ${(h.next_actions || []).length ? `<p class="muted">Next in Store 6: ${h.next_actions.map(E).join(" → ")}</p>` : ""}
        <div class="row"><button data-open="${h.session_id}">Open order</button><button data-json="${h.session_id}">Payload</button></div><pre class="json" data-p="${h.session_id}" hidden></pre></div>`; }).join("") || `<p class="muted">Nothing received yet - confirm an order to see it arrive here.</p>`;
    $("inbox").querySelectorAll("[data-open]").forEach(b => b.onclick = () => { $("follow").checked = false; show("live"); openSession(b.dataset.open); });
    $("inbox").querySelectorAll("[data-json]").forEach(b => b.onclick = () => { const p = $("inbox").querySelector(`[data-p="${b.dataset.json}"]`); const h = inbox.find(x => x.session_id === b.dataset.json); p.innerHTML = Z.json(h); p.hidden = !p.hidden; });
  }
  async function renderAgents() {
    const m = await Z.api(`/v1/ops/metrics?tenant_id=${TENANT}`).catch(() => ({ capabilities: [] }));
    const by = Object.fromEntries(m.capabilities.map(c => [c.capability, c]));
    $("acards").innerHTML = ["polaris", ...Z.ORDER].map(k => { const a = Z.AGENTS[k], c = by[k];
      const stats = k === "polaris" ? `<div><b>${m.sessions ?? 0}</b>orders</div><div><b>${m.runs ?? 0}</b>agent calls</div><div><b>${m.decisions ?? 0}</b>decisions</div><div><b>${(m.states || {}).confirmed || 0}</b>confirmed</div>`
        : `<div><b>${c?.calls || 0}</b>calls</div><div><b>${c?.needs_input || 0}</b>needs input</div><div><b>${c?.errors || 0}</b>errors</div><div><b>${c?.avg_ms ?? 0}</b>avg ms</div>`;
      return `<div class="card"><div class="agenthead"><div class="glyph" style="color:${a.color};border-color:${a.color}">${a.glyph}</div><div><h3>${E(a.name)} <span class="muted" style="font-weight:400">· ${E(a.role)}</span></h3><div class="mono dim">${E(k === "polaris" ? "orchestrator" : k)}</div></div></div>
        <p>${E(a.desc)}</p><p class="muted">${E(a.kind)} · policy <span class="mono">${E(a.policy)}</span></p><div class="stats">${stats}</div>
        ${c ? `<p class="muted">Models: ${Object.entries(c.models).map(([x, n]) => `${E(x)} ×${n}`).join(", ")} · p95 ${c.p95_ms} ms</p>` : ""}
        <div class="row"><button data-agent="${k}">Inspect in orbit</button></div></div>`; }).join("");
    $("acards").querySelectorAll("[data-agent]").forEach(b => b.onclick = () => { show("live"); orbit.select(b.dataset.agent); });
  }
  let policies = null;
  async function renderSystem() {
    const r = await Z.api("/v1/releases/current");
    const rows = o => Object.entries(o).map(([k, v]) => `<dt>${E(k)}</dt><dd class="mono">${E(v)}</dd>`).join("");
    $("sys").innerHTML = `<div class="card"><h3>Release manifest</h3><dl class="kv">${rows(r.manifest)}</dl><p class="${r.compatibility.length ? "s-error" : "s-ok"}">${r.compatibility.length ? E(r.compatibility.join("; ")) : "All components compatible"}</p></div>
      <div class="card"><h3>Runtime</h3><dl class="kv">${rows(r.runtime)}</dl><p class="muted">Offline mode uses deterministic baselines; set ZAR_LLM_MODE=gemini for live Gemini calls.</p><div class="row"><button id="reseed">Reset demo data</button></div></div>
      <div class="card"><h3>Retail Pack</h3><dl class="kv">${rows({ id: r.domains["retail.custom_apparel"].id, version: r.domains["retail.custom_apparel"].version, lifecycle: r.domains["retail.custom_apparel"].lifecycle })}</dl><p class="muted">${r.domains["retail.custom_apparel"].release_notes.map(E).join("<br>")}</p></div>`;
    $("reseed").onclick = async () => { try { await Z.api("/v1/demo/reseed", {}); chat.reset(); await loadList(); alert("Demo data reset."); } catch (e) { alert(e.message); } };
    policies = policies || await Z.api("/v1/ops/policies");
    $("polpick").innerHTML = Object.keys(policies).map(k => `<option>${k}</option>`).join("");
    const showP = () => $("poltext").textContent = policies[$("polpick").value]; $("polpick").onchange = showP; showP();
  }

  /* ---------- demo autopilot ---------- */
  let scen = null;
  async function renderScenarios() {
    scen = scen || await Z.api("/v1/demo/scenarios");
    $("scards").innerHTML = scen.scenarios.map(s => `<div class="card"><div class="row" style="justify-content:space-between;margin:0"><h3>${E(s.title)}</h3><img src="/v1/demo/logos/${s.logo}.png" alt="${E(scen.logos[s.logo])}" width="40" height="40" style="background:#fff;border-radius:6px;object-fit:contain"></div>
      <p>${E(s.shows)}</p><p class="muted">“${E(s.brief)}”</p><p class="dim">${s.steps.length} scripted steps after the brief</p>
      <div class="row"><button class="primary" data-run="${s.id}">Run autopilot</button><button data-stepper="${s.id}">Step through</button></div></div>`).join("");
    $("scards").querySelectorAll("[data-run]").forEach(b => b.onclick = () => pilot(b.dataset.run, false));
    $("scards").querySelectorAll("[data-stepper]").forEach(b => b.onclick = () => pilot(b.dataset.stepper, true));
  }
  function narr(title, text, stepMode, last) {
    const n = $("narr"); n.hidden = false;
    n.innerHTML = `<div class="t">${E(title)}</div><p>${E(text)}</p><div class="row">${stepMode && !last ? `<button class="primary" id="nnext">Next step</button>` : ""}<button id="nstop">${last ? "Close" : "Stop demo"}</button></div>`;
    return new Promise(res => { const nx = $("nnext"); if (nx) nx.onclick = () => res("next"); $("nstop").onclick = () => { st.pilot = null; n.hidden = true; res("stop"); }; if (!stepMode || last) setTimeout(() => res("auto"), last ? 1e9 : 50); });
  }
  async function pilot(id, stepMode) {
    const sc = scen.scenarios.find(s => s.id === id), speed = +$("speed").value; orbit.speed = speed;
    show("live"); $("follow").checked = false; st.pilot = id; chat.reset(); orbit.select(null);
    const title = n => `${sc.title} · step ${n}/${sc.steps.length + 1}`;
    let r = await narr(title(1), `The customer sends the brief from the Store 6 widget: “${sc.brief}”`, stepMode);
    if (r === "stop" || !st.pilot) return;
    const blob = await (await fetch(`/v1/demo/logos/${sc.logo}.png`)).blob();
    await chat.start(sc.brief, await Z.blobToB64(blob), "image/png", { scenario: sc.id, title: sc.title });
    await orbit.idle(); await Z.sleep(900 / speed);
    for (let i = 0; i < sc.steps.length && st.pilot; i++) {
      const step = sc.steps[i];
      r = await narr(title(i + 2), step.say, stepMode); if (r === "stop" || !st.pilot) return;
      if (step.do === "reply") await chat.send("customer_reply", { text: step.text });
      if (step.do === "change") await chat.send("change_request", { text: step.text });
      if (step.do === "choose_proof") await chat.send("proof_chosen", { proof_id: step.proof_id });
      if (step.do === "confirm") await chat.send("confirmed", {}, "confirm-" + chat.session.session_id);
      if (step.do === "roster" || step.do === "roster_update") {
        const n = step.do === "roster" ? step.count : step.index + 1;
        let entries = Array.from({ length: n }, (_, k) => ({ name: scen.names[k % scen.names.length], size: scen.sizes[k % scen.sizes.length], number: step.numbers || step.do === "roster_update" ? String(k + 1) : "" }));
        if (step.duplicate) entries[step.duplicate.index].number = step.duplicate.number;
        if (step.do === "roster_update") entries = [{ ...entries[step.index], number: step.number }];
        await chat.fillRoster(entries);
      }
      await orbit.idle(); await Z.sleep(900 / speed);
    }
    if (st.pilot) { st.pilot = null; await narr(`${sc.title} · complete`, `${sc.shows} Select any agent or trace entry to inspect what happened.`, false, true); }
  }

  /* ---------- boot ---------- */
  $("admin-login").onclick = () => { const token = prompt("Admin access token (blank clears access)"); if (token !== null) { token ? sessionStorage.setItem("zar_admin", token) : sessionStorage.removeItem("zar_admin"); location.reload(); } };
  Z.api("/healthz").then(h => $("rel").textContent = `${h.mode === "mock" ? "OFFLINE BASELINE" : "GEMINI"} · ${h.store} · ${h.release.release_id}`);
  renderTimeline(); renderInspector(); Z.renderSheet($("sheet"), null);
  loadList().then(list => { const params = new URLSearchParams(location.search), wanted = params.get("session"); if(params.has("new")){$("follow").checked=false;chat.reset();}else if(wanted){$("follow").checked=false;openSession(wanted,true);}else if (list[0]) openSession(list[0].session_id); });
  const q = new URLSearchParams(location.search); if (q.get("scenario")) renderScenarios().then(() => pilot(q.get("scenario"), q.get("mode") === "step"));
})(window.ZAR);
