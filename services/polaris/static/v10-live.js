/* V10 UI on the existing Supabase/FastAPI contracts. No Firebase or demo login. */
async function v10(path, body) {
  const r=await fetch('/api/v10'+path,{credentials:'same-origin',cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-ZAR-Console':'1'},body:JSON.stringify(body)})});
  const d=await r.json();if(!r.ok){const e=new Error(typeof d.detail==='string'?d.detail:'Unable to save');e.status=r.status;throw e;}return d;
}
loadV7Preferences=async()=>{const user=Live.user?.user?.id;if(!user||Live.prefUser===user)return;const prefs=await api('/preferences');Live.preferences=prefs;Live.prefUser=user;Local.set('zar.store',prefs.storefront);Sess.set('zar.ops.tab',prefs.overview_tab);};
ZAR_CONFIG.demo={enabled:false};
ZAR_CONFIG.cloud.enabled=false;
ZAR_CONFIG.feed.simulateOnTop=false;
ZAR_CONFIG.channels=['Web chat'];
ZAR_CONFIG.hq.sendEmail=false;
ZAR_CONFIG.hq.incidents=[];
ZAR_CONFIG.hq.uptime=null;
ZAR_CONFIG.hq.lastPenTest='Not recorded';
ZAR_CONFIG.hq.secretsRotation='Managed in server configuration';
ZAR_CONFIG.architecture={channels:'planned',polaris:'live',a2a:'repo',workshop:'live',gemini:'repo',armor:'planned',pillow:'live',tasks:'planned',firestore:'planned',console:'live',functions:'planned',bigquery:'planned'};
HQ_DB.loaded=true;HQ_DB.companies=[];HQ_DB.outreach=[];
const oldAuthGet=Auth.get.bind(Auth);
Auth.get=()=>{const s=oldAuthGet();if(!s)return null;if(Live.hq&&parseHash().path.startsWith('/hq'))return {...s,hq:true,role:HQ_ROLES.zar_admin};return {...s,hq:false};};
Auth.signIn=async(email,password)=>{await Auth.in(email,password);return true;};
const oldOut=Auth.out.bind(Auth);
Auth.out=async()=>{await oldOut();Live.v10User=null;Live.hq=false;HQ_DB.companies=[];HQ_DB.outreach=[];Tenant._c=null;['zar.hq.over','zar.hq.log','zar.hq.auto',Tenant.key(),'zar.zuzu.log'].forEach(k=>{Local.del(k);Sess.del(k);});};
const oldRefresh=Engine.refresh.bind(Engine);
Engine.refresh=async()=>{await oldRefresh();if(Live.user&&Live.v10User!==Live.user.user.id)await loadV10();};
async function loadV10(){
 const p=await v10('/profile');Live.hq=p.hq;
 const t=await v10('/document?path='+encodeURIComponent('tenants/'+ZAR_CONFIG.tenant.id));
 Tenant._c=t?.subscription||{plan:'pro',cycle:'monthly',start:null,end:null,autoRenew:false,addons:[],flags:{},history:[]};
 localStorage.setItem(Tenant.key(),JSON.stringify(Tenant._c));
 Cloud.on=true;
 if(Live.hq)await loadHQ();
 Live.v10User=Live.user.user.id;
}
const V10Cloud={
 profile:{email:''},
 async get(path){return v10('/document?path='+encodeURIComponent(path));},
 async list(path,o={}){let rows=await v10('/documents?path='+encodeURIComponent(path));if(o.orderBy)rows.sort((a,b)=>String(a[o.orderBy]??'').localeCompare(String(b[o.orderBy]??''))*(o.desc?-1:1));return o.limit?rows.slice(0,o.limit):rows;},
 async put(path,data){return v10('/document',{path,data});},
 async add(path,data){const id=crypto.randomUUID();await this.put(path+'/'+id,data);return id;}
};
async function loadHQ(){
 const cos=await V10Cloud.list('companies');
 const rows=await Promise.all(cos.map(async c=>{const [inv,tickets,outreach]=await Promise.all(['invoices','tickets','outreach'].map(k=>V10Cloud.list('companies/'+c.id+'/'+k)));return{company:hqFromDoc(c,inv,tickets),outreach:outreach.map(x=>({...x,co:c.id}))};}));
 HQ_DB.companies=rows.map(x=>x.company);HQ_DB.outreach=rows.flatMap(x=>x.outreach);HQ_DB.loaded=true;
}
HQDB.load=loadHQ;
HQDB.patch=async(id,patch)=>{try{const {paid=[],...rest}=patch;for(const no of paid)await V10Cloud.put('companies/'+id+'/invoices/'+no,{status:'paid',paidAt:Date.now()});if(Object.keys(rest).length)await V10Cloud.put('companies/'+id,rest);await loadHQ();render();}catch(e){toast('Change was not saved: '+e.message);}};
HQDB.addLog=async e=>{try{await V10Cloud.add('companies/'+e.co+'/outreach',{...e,at:Date.now(),delivery:'draft-only'});await loadHQ();}catch(err){toast('Draft was not saved: '+err.message);}};
Cloud.push=(k,v)=>{if(!Live.user)return;const paths={[Tenant.key()]:'tenants/'+ZAR_CONFIG.tenant.id,'zar.hq.auto':'hq/settings'};if(!paths[k])return;const data=k===Tenant.key()?{subscription:v}:{reminderSchedule:v};v10('/document',{path:paths[k],data}).catch(e=>{Live.v10User=null;toast('Change was not saved: '+e.message);loadV10().then(()=>render()).catch(()=>{});});};
// Existing operational capabilities remain available regardless of draft plan choices.
const originalTenantOn=Tenant.on.bind(Tenant);
Tenant.on=(id,who='staff',t)=>['overview','orders','traces','audit','insights','brain','proofs','roster','webchat','multistore'].includes(id)?true:originalTenantOn(id,who,t);
Api.call=async(path,body,options={})=>{try{return await v10(path,options.method==='GET'?undefined:body);}catch(e){if(path==='/health')Api.state=false;return{error:e.status===429?'rate_limited':e.message};}};
Api.label=()=>Live.config.llm==='gemini'?'Gemini configured · server verified sign-in':'Local deterministic reasoning';
Api.state=false;
function tryOrderHTML(){return `<section class="panel"><div class="panel-h"><h2>Start a real order</h2><span>Existing approval workflow</span></div><div class="panel-b"><p>Submit your brief and artwork, approve a proof, complete the roster and confirm the quote. Orders are saved by the existing backend.</p><iframe src="/embed?tenant=${encodeURIComponent(ZAR_CONFIG.tenant.id)}" title="ZAR order assistant" style="width:100%;height:680px;border:0"></iframe></div></section>`;}
function mountTryOrder(){}
const oldAppAdmin=appAdmin;
appAdmin=(ctx,sub)=>sub==='enablement'?appEnablement(ctx):sub==='subscription'?appSubscription(ctx):oldAppAdmin(ctx,sub);
appEnablement=ctx=>({html:`<div class="page-title"><div><h1>Enablement</h1><p>Existing order capabilities stay active. Optional connections require provider setup.</p></div></div><section class="panel"><div class="panel-h"><h2>Working capabilities</h2></div><div class="panel-b">${['Orders and approvals','Agent traces and audit','Second brain','Logo proofs','Team roster','Web chat'].map(n=>`<p><span class="badge b-verd">Available</span> ${n}</p>`).join('')}</div></section><section class="panel"><div class="panel-h"><h2>Assistant preference</h2></div><form id="v10-feature" class="panel-b"><label><input id="v10-zuzu" type="checkbox" ${Tenant.get().flags?.zuzu?.staff===false?'':'checked'}> Show ZuZu in this workspace</label><p><button class="btn btn-brass">Save preference</button></p><p id="v10-feature-result" role="status"></p></form></section><section class="panel"><div class="panel-h"><h2>Connections requiring setup</h2></div><div class="panel-b"><p>Email delivery, WhatsApp, Instagram, SSO and billing are not activated by a feature switch.</p><a class="btn btn-quiet" href="#/app/retail/admin/connections">View connections</a></div></section>`,mount(){const f=$('#v10-feature');f.onsubmit=async e=>{e.preventDefault();const t=structuredClone(Tenant.get());t.flags={...t.flags,zuzu:{staff:$('#v10-zuzu').checked}};try{await v10('/document',{path:'tenants/'+ZAR_CONFIG.tenant.id,data:{subscription:t}});Tenant._c=t;localStorage.setItem(Tenant.key(),JSON.stringify(t));$('#v10-feature-result').textContent='Preference saved.';ZuZu.sync();}catch(err){$('#v10-feature-result').textContent=err.message;}};}});
appSubscription=ctx=>({html:`<div class="page-title"><div><h1>Subscription</h1><p>Save a proposed plan for review. No payment is taken and existing order access is unchanged.</p></div></div><section class="panel"><form id="v10-plan" class="panel-b"><label for="v10-plan-id">Proposed plan</label><select class="input" id="v10-plan-id">${PLANS.map(p=>`<option value="${esc(p.id)}" ${Tenant.get().plan===p.id?'selected':''}>${esc(p.name)}${p.m?.INR?' · '+inr(p.m.INR)+'/month':' · Contact us'}</option>`).join('')}</select><p><button class="btn btn-brass">Save proposed plan</button></p><p id="v10-plan-result" role="status"></p><p>Prices are from the supplied Version 10 catalogue. There is no active billing agreement or automatic renewal in this deployment.</p></form></section>`,mount(){const f=$('#v10-plan');f.onsubmit=async e=>{e.preventDefault();const t={...Tenant.get(),plan:$('#v10-plan-id').value,autoRenew:false};try{await v10('/document',{path:'tenants/'+ZAR_CONFIG.tenant.id,data:{subscription:t}});Tenant._c=t;localStorage.setItem(Tenant.key(),JSON.stringify(t));$('#v10-plan-result').textContent='Proposed plan saved; no charge made.';}catch(err){$('#v10-plan-result').textContent=err.message;}};}});
const originalHQCompaniesView=hqCompaniesView;
hqCompaniesView=(s,id)=>{
 const view=originalHQCompaniesView(s,id),mount=view.mount;
 view.html=`<div class="notice">Saved company records. Revenue projections are estimates; payment collection and email delivery are not enabled.</div><details class="panel"><summary class="btn btn-quiet">Add company</summary><form id="v10-company" class="panel-b"><label for="v10-company-name">Company name</label><input class="input" id="v10-company-name" required maxlength="160"><label for="v10-company-domain">Website domain</label><input class="input" id="v10-company-domain" maxlength="255" placeholder="example.com"><button class="btn btn-brass">Save company</button><p id="v10-company-result" role="status"></p></form></details>`+view.html;
 view.mount=()=>{mount?.();$('#v10-company').onsubmit=async e=>{
  e.preventDefault();const name=$('#v10-company-name').value.trim(),domain=$('#v10-company-domain').value.trim();if(!name)return;
  const button=e.currentTarget.querySelector('button');button.disabled=true;
  try{await V10Cloud.put('companies/'+crypto.randomUUID(),{name,domain,city:'',plan:'basic',cycle:'monthly',startAt:Date.now(),endAt:Date.now()+30*D,sinceAt:Date.now(),trial:true,autoRenew:false,usage:{use:0,orders:0},seats:0,storefronts:0,contact:{name:'',title:'',email:''}});await loadHQ();render();toast('Company saved.');}catch(err){$('#v10-company-result').textContent=err.message;button.disabled=false;}
 };};return view;
};
const originalRenderHQ=renderHQ;
renderHQ=path=>{originalRenderHQ(path);const main=$('#main');if(main&&Live.hq&&!$('#v10-hq-return')){const a=document.createElement('a');a.id='v10-hq-return';a.href='#/app/retail';a.className='btn btn-quiet';a.textContent='Merchant workspace';main.prepend(a);}};
const originalUpdateConnection=updateConnection;
updateConnection=()=>{const b=$('#runtime-banner');if(b)b.remove();const el=$('#live-pill');if(el)el.innerHTML='<i></i><span>'+ (Live.online?'Saved data':'Not connected')+'</span>';if(Live.hq&&parseHash().path.startsWith('/app')&&!$('#hq-link')){const a=document.createElement('a');a.id='hq-link';a.className='btn btn-quiet';a.href='#/hq';a.textContent='ZAR HQ';$('.page-title')?.appendChild(a);}};
const originalBootLive=bootLive;
bootLive=async()=>{['zar.session','zar.hq.over','zar.hq.log','zar.zuzu.log'].forEach(k=>{Local.del(k);Sess.del(k);});try{const c=await v10('/catalog');applyCatalog(c.plans,c.features);}catch(e){console.warn('Pricing catalogue unavailable');}await originalBootLive();Api.state=Live.config.llm==='gemini';};
