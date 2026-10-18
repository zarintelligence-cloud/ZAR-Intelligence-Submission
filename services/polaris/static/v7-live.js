/* V7 additions: server preferences, policy retrieval and durable team notes. */
async function loadV7Preferences(){
  const user=Live.user?.user?.id;
  if(!user || Live.prefUser===user)return;
  const [prefs,cat]=await Promise.all([api('/preferences'),api('/overview-catalog')]);
  Live.preferences=prefs;Live.prefUser=user;
  Local.set('zar.store',prefs.storefront);Sess.set('zar.ops.tab',prefs.overview_tab);
  DEPARTMENTS.splice(0,DEPARTMENTS.length,...cat.departments.map(d=>({id:d.id,name:d.name,icon:d.icon,
    ...(d.planned?{planned:d.agents.map(([id,role])=>[id[0].toUpperCase()+id.slice(1),role])}:{agents:d.agents})})));
  cat.departments.forEach(d=>{DEPT_COLOR[d.id]=d.color;DEPT_SUB[d.id]=d.subtitle;});
  Object.keys(AGENT_JOBS).forEach(k=>delete AGENT_JOBS[k]);
  cat.jobs.forEach(j=>(AGENT_JOBS[j.agent_id]??=[]).push([j.id.split('.')[1],j.label]));
  Object.assign(STATE_JOB,cat.state_jobs);
}
let preferenceQueue=Promise.resolve();
function saveV7Preference(patch){
  if(!Live.user)return;
  Live.preferences={overview_tab:'map',storefront:'all',...Live.preferences,...patch};
  const body={...Live.preferences};
  preferenceQueue=preferenceQueue.catch(()=>{}).then(()=>api('/preferences',body)).catch(e=>toast('Preference was not saved: '+e.message));
}
const originalMiniDashHTML=miniDashHTML;
miniDashHTML=function(item){return !Live.user&&item.kind!=='cta'?`<div class="mdash mdash-cta"><h4>${esc(item.title)}</h4><p>Sign in to view your saved orders and agent activity.</p><a class="btn btn-brass" href="#/signin">Sign in</a></div>`:originalMiniDashHTML(item);};

function appBrain(ctx,sourceId){
  const admin=ctx.s.user.role==='administrator';
  return {html:`<div class="page-title"><div><h1>Second brain</h1><p>Search saved orders, versioned policies and team reference notes. Answers show their sources.</p></div></div>
    <section class="panel"><div class="panel-b"><label for="brain-store">Storefront</label><select class="input" id="brain-store">${STORES().map(s=>`<option value="${esc(s.id)}" ${curStore()===s.id?'selected':''}>${esc(s.name)}</option>`).join('')}</select>
    <form id="brain-search" class="ask-row"><label class="sr" for="brain-q">Search the second brain</label><input class="input" id="brain-q" required minlength="3" maxlength="1000" placeholder="Which orders are waiting? Or enter an order ID or policy term"><button class="btn btn-brass">Search</button></form><div id="brain-answer" aria-live="polite"></div></div></section>
    <section class="panel"><div class="panel-h"><h2>Policy price estimate</h2></div><form id="brain-estimate" class="panel-b form-grid"><label>Garment<select class="input" name="garment"><option>cricket jersey</option><option>polo</option><option>round neck tee</option></select></label><label>Print method<select class="input" name="method"><option>dtf</option><option>screen</option><option>sublimation</option></select></label><label>Quantity<input class="input" name="quantity" type="number" min="1" max="10000" value="40" required></label><label><input name="personalisation" type="checkbox"> Names or numbers</label><button class="btn btn-quiet">Calculate estimate</button><p id="estimate-result" role="status"></p></form></section>
    <div class="src-grid" id="knowledge-sources">Loading saved sources…</div>
    ${admin?`<section class="panel"><div class="panel-h"><h2>Add a team reference note</h2></div><form id="brain-note" class="panel-b"><p>Saved for this storefront. Notes do not override prices, approvals or agent policy.</p><label for="note-title">Title</label><input class="input" id="note-title" required maxlength="80"><label for="note-text">Note</label><textarea class="textarea" id="note-text" required maxlength="4000"></textarea><button class="btn btn-brass">Save note</button><p id="note-status" role="status"></p></form></section>`:''}`,
    mount(){let alive=true;onLeave(()=>alive=false);const tenant=()=>$('#brain-store').value;
      const draw=async()=>{try{const d=await api('/knowledge?tenant='+encodeURIComponent(tenant()));if(!alive)return;$('#knowledge-sources').innerHTML=d.sources.map(s=>`<article class="src open"><header><span class="tag">${esc(s.kind)}</span><b>${esc(s.name)}</b></header><details ${sourceId===s.id?'open':''}><summary>Read source</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(s.text)}</pre><small>${esc(s.reference)}</small></details>${admin&&s.kind==='Team reference note'?`<button class="btn btn-quiet" data-note-archive="${esc(s.id)}">Archive note</button>`:''}</article>`).join('');}catch(e){if(alive)$('#knowledge-sources').textContent=e.message;}};
      $('#brain-store').onchange=()=>{$('#brain-answer').textContent='';draw();};draw();
      $('#brain-search').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const a=await api('/knowledge/ask',{tenant_id:tenant(),question:$('#brain-q').value});if(!alive)return;$('#brain-answer').innerHTML=`<div class="ans"><p>${esc(a.answer)}</p><small>${esc(a.method)}</small>${a.sources.map(s=>`<article><b>${s.href?`<a href="${esc(s.href)}">${esc(s.label)}</a>`:esc(s.label)}</b><pre style="white-space:pre-wrap">${esc(s.text)}</pre><small>${esc(s.reference||'Saved order')}</small></article>`).join('')}</div>`;}catch(err){if(alive)$('#brain-answer').textContent=err.message;}finally{b.disabled=false;}};
      $('#brain-estimate').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{const d=await api('/knowledge/estimate',{tenant_id:tenant(),garment:f.get('garment'),method:f.get('method'),quantity:Number(f.get('quantity')),personalisation:f.has('personalisation')});if(alive)$('#estimate-result').textContent=`INR ${d.quote.total.toLocaleString('en-IN')} total; INR ${d.quote.unit_price} each. ${d.notice}`;}catch(err){if(alive)$('#estimate-result').textContent=err.message;}};
      $('#knowledge-sources').onclick=async e=>{const b=e.target.closest('[data-note-archive]');if(!b)return;b.disabled=true;try{await api('/knowledge/notes/'+b.dataset.noteArchive+'/archive',{});await draw();}catch(err){toast(err.message);b.disabled=false;}};
      const f=$('#brain-note');if(f)f.onsubmit=async e=>{e.preventDefault();const b=f.querySelector('button');b.disabled=true;try{await api('/knowledge/notes',{tenant_id:tenant(),title:$('#note-title').value,text:$('#note-text').value});if(alive){f.reset();$('#note-status').textContent='Note saved to the database.';await draw();}}catch(err){if(alive)$('#note-status').textContent=err.message;}finally{b.disabled=false;}};
    }};
}
