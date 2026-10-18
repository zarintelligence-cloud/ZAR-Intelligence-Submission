/* ZAR Intelligence embed for client storefronts.
   <script src="https://<zar-host>/widget.js" data-tenant="store6" defer></script>
   Adds a launcher button and opens the ZAR assistant in an isolated iframe (no CORS, no shared cookies). */
(function () {
  var me = document.currentScript, origin = new URL(me.src).origin, tenant = me.getAttribute("data-tenant") || "store6";
  var label = me.getAttribute("data-label") || "Design your team kit";
  var css = ".zarw-btn{position:fixed;right:20px;bottom:20px;z-index:2147483000;background:#101828;color:#fff;border:0;border-radius:999px;padding:13px 18px;font:600 15px system-ui,sans-serif;box-shadow:0 8px 24px rgba(16,24,40,.28);cursor:pointer;display:flex;gap:8px;align-items:center}" +
    ".zarw-btn:focus-visible{outline:3px solid #f5b82e;outline-offset:2px}.zarw-btn i{width:9px;height:9px;border-radius:50%;background:#f5b82e;display:inline-block}" +
    ".zarw-panel{position:fixed;right:20px;bottom:84px;width:min(420px,calc(100vw - 24px));height:min(680px,calc(100vh - 110px));z-index:2147483000;border-radius:16px;overflow:hidden;box-shadow:0 18px 50px rgba(16,24,40,.35);background:#fff;display:none}" +
    ".zarw-panel.open{display:block}.zarw-panel iframe{width:100%;height:100%;border:0}" +
    ".zarw-x{position:absolute;top:10px;right:10px;background:rgba(255,255,255,.14);color:#fff;border:0;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:18px}";
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  var btn = document.createElement("button"); btn.className = "zarw-btn"; btn.innerHTML = "<i></i>" + label; btn.setAttribute("aria-expanded", "false");
  var panel = document.createElement("div"); panel.className = "zarw-panel"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Team kit assistant");
  var frame = document.createElement("iframe"); frame.title = "Team kit assistant"; frame.src = origin + "/embed?tenant=" + encodeURIComponent(tenant);
  var x = document.createElement("button"); x.className = "zarw-x"; x.setAttribute("aria-label", "Close assistant"); x.textContent = "×";
  panel.appendChild(frame); panel.appendChild(x); document.body.appendChild(panel); document.body.appendChild(btn);
  function open(on) { panel.classList.toggle("open", on); btn.setAttribute("aria-expanded", String(on)); }
  btn.onclick = function () { open(!panel.classList.contains("open")); }; x.onclick = function () { open(false); btn.focus(); };
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") open(false); });
  var loaded = false, pending = null;
  frame.addEventListener("load", function () { loaded = true; if (pending) { frame.contentWindow.postMessage(pending, origin); pending = null; } });
  window.ZARWidget = {
    open: function (opts) {
      open(true);
      if (opts && opts.brief) { var m = { type: "zar:prefill", brief: opts.brief }; loaded ? frame.contentWindow.postMessage(m, origin) : (pending = m); }
    },
    close: function () { open(false); }
  };
})();
