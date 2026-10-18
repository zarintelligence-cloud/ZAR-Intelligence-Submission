/* Shared helpers: API calls, agent catalogue, formatting, JSON highlighting. */
window.ZAR = window.ZAR || {};
(function (Z) {
  Z.AGENTS = {
    polaris: { key: "polaris", name: "Polaris", role: "Front desk · Master", color: "#f0c75e", glyph: "✳",
      kind: "Orchestrator - Core workflow engine", model: "none", policy: "workflow.yaml",
      desc: "The only public component. Receives the brief and events, runs the Retail Pack workflow, calls specialists by capability name and turns their results into customer messages. It never computes a business answer itself." },
    requirements_analysis: { key: "requirements_analysis", name: "Rosetta", role: "Brief", color: "#5fd4c4", glyph: "✎",
      kind: "Gemini 3.5 Flash + deterministic verification", model: "gemini-3.5-flash (baseline offline)", policy: "question_policy.yaml",
      desc: "Turns the conversation into a structured order spec. Numbers the customer never wrote are dropped. Asks at most three questions, chosen by expected loss (value of information)." },
    artwork_analysis: { key: "artwork_analysis", name: "Spectra", role: "Artwork", color: "#b28cff", glyph: "◈",
      kind: "Pixel measurement + Gemini vision + policy table", model: "gemini-3.5-flash (measurement offline)", policy: "print_policy.yaml",
      desc: "Measures size, colours, transparency and resolution in code; Gemini reports gradients and fine text; a versioned first-match rule table chooses the print method." },
    proof_generation: { key: "proof_generation", name: "Hubble", role: "Proof", color: "#e08cf0", glyph: "▣",
      kind: "Deterministic compositing", model: "none", policy: "catalog.yaml (colours)",
      desc: "Places the customer's original logo on front/back garment proofs in their colours. Fails if the logo aspect ratio drifts more than 1%. Flags low-contrast colourways." },
    roster_management: { key: "roster_management", name: "Pleiades", role: "Team", color: "#7bd88f", glyph: "⁂",
      kind: "Plain software", model: "none", policy: "catalog.yaml (sizes, name limits)",
      desc: "Creates the shareable roster, validates names, numbers and sizes, blocks duplicate numbers and lets a player correct their entry." },
    pricing: { key: "pricing", name: "Kepler", role: "Quote", color: "#f2b35b", glyph: "₹",
      kind: "Rules + constraint optimiser", model: "rules-only", policy: "pricing.yaml",
      desc: "Prices every allowed print method from versioned rules. If the recommended one breaks the budget, picks the cheapest allowed method within it." },
    fulfillment_confidence: { key: "fulfillment_confidence", name: "Horizon", role: "Delivery", color: "#6fb6ff", glyph: "◷",
      kind: "Monte Carlo (5,000 runs, session-seeded)", model: "monte-carlo", policy: "fulfillment.yaml",
      desc: "Estimates the probability of meeting the deadline from production, roster, approval and shipping time distributions." },
    compose_reply: { key: "compose_reply", name: "Voice", role: "Language", color: "#9aa8c8", glyph: "✉",
      kind: "Gemini 3.5 Flash-Lite + number guard", model: "gemini-3.5-flash-lite", policy: "-",
      desc: "Rewrites replies into the customer's language when it isn't English. Any rewrite that changes a number is rejected." },
    order_handoff: { key: "order_handoff", name: "Store 6", role: "Handoff", color: "#ff8a7a", glyph: "⇄",
      kind: "Connector - signed webhook or sandbox", model: "none", policy: "tenants/store6.yaml",
      desc: "Sends the confirmed production spec to Store 6's order system with an idempotency key and HMAC signature." },
  };
  Z.ORDER = ["requirements_analysis", "artwork_analysis", "proof_generation", "roster_management",
    "pricing", "fulfillment_confidence", "compose_reply", "order_handoff"];

  const customerKeys = new Map();
  function customerKey(sid) { try { return localStorage.getItem('zar_customer_'+sid) || customerKeys.get(sid); } catch { return customerKeys.get(sid); } }
  function rememberCustomer(sid, token) { customerKeys.set(sid,token); try { localStorage.setItem('zar_customer_'+sid,token); } catch {} }
  Z.api = async function (path, body, method) {
    const opt = body !== undefined ? { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
    const admin = sessionStorage.getItem("zar_admin");
    if (admin) opt.headers = { ...(opt.headers || {}), "X-ZAR-Admin": admin };
    const sid = path.match(/^\/v1\/sessions\/([a-f0-9]{32})/)?.[1];
    const access = sid && customerKey(sid);
    if (access) opt.headers = { ...(opt.headers || {}), 'X-ZAR-Customer': access };
    const r = await fetch(path, opt);
    if (!r.ok) { const e = await r.json().catch(() => ({ detail: r.statusText })); throw new Error(typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)); }
    const result = await r.json();
    if (result.customer_token && result.session_id) rememberCustomer(result.session_id, result.customer_token);
    return result;
  };
  Z.assetUrl = function(path) { if(path.includes('signature=')) return path; const sid=path.match(/^\/v1\/sessions\/([a-f0-9]{32})/)?.[1]; const token=sid && customerKey(sid); return token ? path+(path.includes('?')?'&':'?')+'access='+encodeURIComponent(token) : path; };
  Z.esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  Z.money = (n, c = "INR") => n == null ? "" : `${Number(n).toLocaleString("en-IN")} ${c}`;
  Z.pct = p => p == null ? "" : p >= .995 ? ">99%" : p < .005 ? "<1%" : `${Math.round(p * 100)}%`;
  Z.ago = iso => { const s = (Date.now() - new Date(iso)) / 1000; return s < 60 ? `${Math.max(1, s | 0)}s ago` : s < 3600 ? `${(s / 60) | 0}m ago` : `${(s / 3600) | 0}h ago`; };
  Z.blobToB64 = b => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result.split(",")[1]); r.onerror = no; r.readAsDataURL(b); });
  Z.sleep = ms => new Promise(r => setTimeout(r, ms));
  Z.json = function (obj) {
    const s = Z.esc(JSON.stringify(obj, null, 2));
    return s.replace(/(&quot;(?:\\.|[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false)\b|\bnull\b|-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b/g,
      (m, str, colon, bool) => str ? (colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`)
        : bool ? `<span class="b">${m}</span>` : m === "null" ? `<span class="z">${m}</span>` : `<span class="n">${m}</span>`);
  };
  Z.agentOf = cap => Z.AGENTS[cap] || { name: cap, role: "", color: "#aaa", glyph: "•" };
  Z.STATES = ["new", "asking", "artwork", "awaiting_approval", "collecting", "awaiting_confirmation", "confirmed"];
  Z.stateLabel = s => ({ new: "New", asking: "Asking", artwork: "Artwork", awaiting_approval: "Proofs ready",
    collecting: "Collecting roster", awaiting_confirmation: "Quote sent", confirmed: "Confirmed", cancelled: "Cancelled" }[s] || s);
})(window.ZAR);
