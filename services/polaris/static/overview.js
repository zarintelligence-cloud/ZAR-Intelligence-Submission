(function (Z) {
  const $ = id => document.getElementById(id), E = Z.esc;
  let records = [], selected = null;
  const notice = text => { $("notice").textContent = text; };
  function download(name, data) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
    const a = document.createElement("a"); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  async function refresh() {
    try {
      const [o, rows] = await Promise.all([Z.api("/v1/ops/overview"), Z.api("/v1/database/sessions")]);
      records = rows;
      $("metrics").innerHTML = [[o.counts.current,"Current sessions"],[o.counts.confirmed,"Confirmed · includes demos"],[o.counts.archive,"Imported snapshots"],[o.counts.attention,"Need attention"]].map(([n,label]) => `<div class="metric"><b>${n}</b><span>${label}</span></div>`).join("");
      $("scope").textContent = o.scope;
      $("attention").innerHTML = o.attention.slice(0,6).map(s => `<div class="alert-row"><a href="/mission-control?session=${encodeURIComponent(s.session_id)}">${E(s.title)}</a><small>${s.reasons.map(E).join(" · ")}</small></div>`).join("") || '<p class="empty">No flagged orders in the current records.</p>';
      const names = {reasoning:"Gemini reasoning",database:"Order database",artwork:"Artwork storage",workshop:"Specialist workshop",store6:"Store 6 handoff"};
      const labels = {mock:"Offline deterministic baseline",gemini:"Gemini configured",sqlite:"Persistent SQLite",firestore:"Cloud Firestore",filesystem:"Local files",gcs:"Cloud Storage",local:"Local runtime",remote:"Private remote service",sandbox:"Sandbox · no store delivery",webhook:"Live webhook configured",memory:"Temporary memory"};
      $("connections").innerHTML = Object.entries(o.connections).map(([k,v]) => `<div class="connection"><span>${names[k]}</span><span class="tag ${['mock','sandbox','memory'].includes(v)?'warn':''}">${E(labels[v] || v)}</span></div>`).join("");
      renderRecords();
    } catch(e) { notice(e.message + " — Use Admin access if this deployment is protected."); $("attention").textContent = "Admin access is needed to load operational data."; }
  }
  function renderRecords() {
    const needle = $("search").value.toLowerCase(), mode = $("filter").value;
    const visible = records.filter(r => JSON.stringify([r.title,r.state,r.spec]).toLowerCase().includes(needle) && (mode !== "risk" || r.risk.length) && (mode !== "confirmed" || r.state === "confirmed"));
    $("examples").innerHTML = visible.map(r => `<article class="example"><div><span class="tag">Historical · ${E(Z.stateLabel(r.state))}</span></div><h3>${E(r.spec.garment || "Custom apparel")}${r.spec.quantity ? ` × ${r.spec.quantity}` : " · brief incomplete"}</h3><p>${E(r.title)}</p><div class="amount">${r.total != null ? Z.money(r.total) : "Quote pending"}</div><p>${r.roster_count} roster entries · Owner: ${E(r.source_owner)}<br>Original deadline: ${E(r.spec.deadline || "Not supplied")}</p><div>${r.risk.map(x => `<span class="tag warn">${E(x)}</span>`).join(" ") || '<span class="tag">Successful example</span>'}</div><div class="actions"><button data-detail="${r.archive_id}">Inspect record</button><button class="primary" data-demo="${r.archive_id}">Run fresh demo ↗</button></div></article>`).join("") || `<p class="empty">${records.length ? "No matching records." : "Import the attached kit to explore its three examples."}</p>`;
    $("examples").querySelectorAll("[data-detail]").forEach(b => b.onclick = () => inspect(b.dataset.detail));
    $("examples").querySelectorAll("[data-demo]").forEach(b => b.onclick = () => replay(b.dataset.demo,b,false));
  }
  async function inspect(id) {
    try {
      selected = await Z.api(`/v1/database/sessions/${id}`);
      const d = selected.data;
      $("detail").hidden = false;
      $("detail").innerHTML = `<div class="section-head"><div><div class="eyebrow">PRESERVED WEBSITE SNAPSHOT</div><h2>${E(d.title || "Imported order")}</h2></div><button id="close-detail">Close</button></div><p class="caption">Source release: ${E(d.release?.release_id || "unknown")} · Source revision ${selected.source_rev} · No original artwork was included. Source owner is historical metadata, not a login account.</p><div class="actions"><button id="fresh-demo" class="primary">Run fresh demo from brief</button><button id="quote-demo">Prepare demo quote with sample roster</button><button id="export-source">Download original record</button></div><p class="caption">Fresh runs shift the deadline relative to today and generate sample artwork. Preparing a quote automatically chooses the historical proof index and submits the sample roster with short Player labels that fit the print policy. Both actions stop before confirmation.</p><div class="detail-columns"><div><h3>Order specification</h3><pre>${E(JSON.stringify(d.spec,null,2))}</pre><h3>Historical quote and delivery</h3><pre>${E(JSON.stringify({quote:d.quote,delivery:d.delivery},null,2))}</pre><h3>Roster · ${(d.roster || []).length} entries</h3><div style="max-height:240px;overflow:auto"><table><thead><tr><th>Name</th><th>Size</th><th>Number</th></tr></thead><tbody>${(d.roster || []).map(e=>`<tr><td>${E(e.name)}</td><td>${E(e.size)}</td><td>${E(e.number || '')}</td></tr>`).join('')}</tbody></table></div></div><div><h3>Historical agent activity</h3>${(d.trace || []).map(t=>`<div class="trace-row"><b>${E(t.agent)} · ${E(t.status)}</b><p>${E(t.summary)}</p></div>`).join('')}<h3>Decision evidence</h3><pre>${E(JSON.stringify(d.decisions || [],null,2))}</pre></div></div>`;
      $("close-detail").onclick=()=>{$("detail").hidden=true;};
      $("export-source").onclick=()=>download(`ZAR-original-${selected.original_id}.json`,selected.data);
      $("fresh-demo").onclick=e=>replay(id,e.target,false);
      $("quote-demo").onclick=e=>replay(id,e.target,true);
      $("detail").scrollIntoView({behavior:"smooth",block:"start"});
    } catch(e) {notice(e.message);}
  }
  async function replay(id, button, prepare) {
    button.disabled = true; const old = button.textContent; button.textContent = "Running agents…";
    // Retain the request ID on failure so a retry cannot create a second order.
    button.dataset.request ||= crypto.randomUUID();
    notice("Creating a fresh synthetic demo. The original record stays unchanged; confirmation remains yours.");
    try {
      const r = await Z.api(`/v1/database/sessions/${id}/replay`,{request_id:button.dataset.request,prepare_quote:prepare});
      location.href = `/mission-control?session=${encodeURIComponent(r.session.session_id)}&source=database`;
    } catch(e) {notice(e.message);button.disabled=false;button.textContent=old;}
  }
  $("access").onclick = () => {const token = prompt("Admin access token (blank clears access)");if(token!==null){token?sessionStorage.setItem("zar_admin",token):sessionStorage.removeItem("zar_admin");location.reload();}};
  $("import").onclick=async e=>{e.target.disabled=true;try{const r=await Z.api('/v1/database/import-kit',{});notice(`${r.added} imported · ${r.existing} already present. ${r.note}`);await refresh();}catch(err){notice(err.message);}finally{e.target.disabled=false;}};
  $("refresh").onclick=refresh; $("search").oninput=renderRecords; $("filter").onchange=renderRecords;
  Z.api('/healthz').then(h=>{$("runtime").textContent=`● ${h.mode==='mock'?'Offline baseline · no Gemini calls':'Gemini configured'} · ${h.store} · ${h.release.release_id}`;}).catch(e=>notice(e.message));
  refresh();
})(window.ZAR);
