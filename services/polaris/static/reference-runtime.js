/* Load the reference interface after establishing truthful runtime labels. */
window.ZARHeaders = () => sessionStorage.getItem('zar_admin') ? {'X-ZAR-Admin':sessionStorage.getItem('zar_admin')} : {};
window.ZARAdmin = () => {
  const token = prompt('Admin access token (blank clears access)');
  if (token !== null) {
    token ? sessionStorage.setItem('zar_admin',token) : sessionStorage.removeItem('zar_admin');
    location.reload();
  }
};
(async()=>{
  try {
    const response = await fetch('/healthz');
    if(!response.ok) throw Error('Backend health check failed');
    const h=await response.json(), mode=h.mode==='mock'?'Offline baseline':'Gemini configured';
    window.ZAR_RUNTIME={badge:h.mode==='mock'?'OFFLINE MODE':'GEMINI MODE',workspace:`${mode} · orders saved to ${h.store}`,release:h.release,
      extraction:h.mode==='mock'?'Deterministic baseline extraction. Gemini is not connected.':'Gemini extraction configured, with deterministic grounding checks.',
      database:`Connected ${h.store} database. ${h.store==='memory'?'Temporary development storage.':'Orders persist across refreshes and restarts.'}`,
      databaseStatus:h.store==='memory'?'Temporary':'Connected',
      artwork:`Original uploads and generated proofs are saved using ${h.assets} storage.`,
      gemini:h.mode==='mock'?'The current extractor uses the offline rules baseline. Configure Vertex AI credentials to enable Gemini.':'Vertex AI and Gemini are configured. Capability failures are reported in the order trace.',
      geminiStatus:h.mode==='mock'?'Not configured':'Configured',
      cloud:h.cloud_runtime?'This application is running on Google Cloud Run.':'This application is running locally. Google Cloud deployment instructions are available under Database & deployment.',
      cloudStatus:h.cloud_runtime?'Deployed':'Local'};
    document.getElementById('reference-status').textContent=`${mode.toUpperCase()} · Connected backend · Orders saved to ${h.store} · Review synthetic demos before confirmation`;
    const script=document.createElement('script');script.src='/static/reference-app.js?v=r5-overview2';
    script.onerror=()=>{document.getElementById('reference-status').textContent='Unable to load the interface. Refresh to retry.';};
    document.body.appendChild(script);
  } catch(e) {
    document.getElementById('reference-status').textContent=`Cannot connect to ZAR: ${e.message}. Start ZAR, then refresh this page.`;
  }
})();
