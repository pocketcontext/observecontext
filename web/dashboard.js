import { pb, renew, view } from './api.js';
const $=id=>document.getElementById(id);
const ms=n=>Number(n).toFixed(2)+' ms';
const berlinTime=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Berlin',year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3,hourCycle:'h23',timeZoneName:'short'});
function timestamp(value){const date=value instanceof Date?value:new Date(String(value).replace(' ','T'));return Number.isNaN(date.getTime())?'Unknown time':berlinTime.format(date)}
function node(tag,text,cls=''){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;el.className=cls;return el}
function cell(row,value,cls=''){const td=node('td',value,cls);row.append(td);return td}
function failure(t){return t.status===0||t.status>=400}
function svgNode(tag,attributes={},text){
 const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
 for(const [name,value] of Object.entries(attributes))el.setAttribute(name,String(value));
 if(text!==undefined)el.textContent=text;
 return el;
}
function renderTimeline(trace,spans){
 const finite=n=>Number.isFinite(Number(n))?Math.max(0,Number(n)):0;
 const total=finite(trace.duration_ms),scale=total||1;
 const timeline=node('div',undefined,'timeline');
 const axisRow=node('div',undefined,'timeline-row timeline-axis');
 const axis=svgNode('svg',{width:'100%',height:32,'aria-hidden':'true',focusable:'false'});
 // Keep full millisecond labels legible for long requests on narrow screens.
 const ticks=total>=10000?[0,1]:total>=100?[0,.5,1]:total?[0,.25,.5,.75,1]:[0];
 for(const fraction of ticks){
  const x=(fraction*100)+'%';
  axis.append(svgNode('line',{x1:x,x2:x,y1:24,y2:32,class:'timeline-tick'}));
  axis.append(svgNode('text',{x,y:15,'text-anchor':fraction===0?'start':fraction===1?'end':'middle'},ms(total*fraction)));
 }
 axisRow.append(node('span','Phase / start offset'),axis,node('span','Duration','number'));
 timeline.append(axisRow);
 for(const span of spans){
  // Producers allow a small rounding tolerance. Keep drawings within the trace
  // while retaining reported timing values in the visible and accessible labels.
  const start=Math.min(finite(span.offset_ms),total);
  const end=Math.min(total,start+finite(span.duration_ms));
  const x=start/scale*100,width=(end-start)/scale*100;
  const description=span.name+'; starts at '+ms(span.offset_ms)+'; duration '+ms(span.duration_ms)+'; trace duration '+ms(total);
  const chart=svgNode('svg',{width:'100%',height:24,role:'img','aria-label':description,focusable:'false',class:'timeline-chart'});
  chart.append(svgNode('title',{},description));
  chart.append(svgNode('rect',{x:0,y:3,width:'100%',height:18,rx:3,class:'timeline-track'}));
  for(const fraction of ticks)chart.append(svgNode('line',{x1:(fraction*100)+'%',x2:(fraction*100)+'%',y1:0,y2:24,class:'timeline-grid'}));
  if(width>0)chart.append(svgNode('rect',{x:x+'%',y:5,width:width+'%',height:14,rx:2,class:'timeline-bar'}));
  else chart.append(svgNode('line',{x1:x+'%',x2:x+'%',y1:4,y2:20,class:'timeline-marker'}));
  const label=node('span',span.name,'timeline-label');label.append(node('span','+'+ms(span.offset_ms),'subline'));
  const row=node('div',undefined,'timeline-row');row.append(label,chart,node('span',ms(span.duration_ms),'number timeline-duration'));timeline.append(row);
 }
 return timeline;
}
function role(t){return ['client','server'].includes(t.kind)?t.kind:'unknown'}
function groupOperations(traces){
 const groups=new Map();
 for(const t of traces){const key=t.operation?'operation:'+t.operation:'trace:'+t.id;if(!groups.has(key))groups.set(key,{key,traces:[]});groups.get(key).traces.push(t)}
 return [...groups.values()].map(op=>{
  op.clients=op.traces.filter(t=>role(t)==='client');op.servers=op.traces.filter(t=>role(t)==='server');
  op.primary=op.clients[0]||op.servers[0]||op.traces[0];op.service=(op.servers[0]||op.primary).service;
  op.started=op.traces.map(t=>t.started_at).sort()[0];op.paired=op.clients.length===1&&op.servers.length===1&&op.traces.length===2;
  op.errors=op.traces.some(failure);return op;
 }).sort((a,b)=>b.started.localeCompare(a.started)||a.key.localeCompare(b.key));
}
function elapsed(traces){return traces.length===1?ms(traces[0].duration_ms):traces.length?traces.length+' traces':'—'}
function pairing(op){if(op.paired)return 'Client + server';if(op.traces.length>1)return op.traces.length+' measurements';return role(op.primary)==='unknown'?'Unclassified':role(op.primary)==='client'?'Client only':'Server only'}
function status(op){const values=[...new Set(op.traces.map(t=>t.status===0?'Transport error':String(t.status)))];return values.join(' / ')}
let sessionEpoch=0,authenticated=false,requests=new Set();
class SessionEnded extends Error {}
function clearSession(message='Sign in to view your operations.'){
 sessionEpoch++;authenticated=false;searchVersion++;loading=false;reportLoading=false;for(const controller of requests)controller.abort();requests.clear();
 operations=[];selected=null;detailVersion++;lastReport=0;
 for(const id of ['recent','report','measurements'])$(id).replaceChildren();
 for(const id of ['identity','sql','detail-title','notice','status','window-note','report-status','account-name','timing-note'])$(id).textContent='';
 for(const id of ['operation-count','paired-count','error-count','client-time','server-time','difference'])$(id).textContent='—';
 $('filter').value='';$('detail').hidden=true;$('dashboard').hidden=true;$('account-actions').hidden=true;
 $('report-panel').open=false;$('signin').hidden=false;$('login-status').textContent=message;
}
async function get(path,options={}){
 const epoch=sessionEpoch,identity=authIdentity(),token=pb.authStore.token,controller=new AbortController();requests.add(controller);
 try{
  if(!pb.authStore.isValid){pb.authStore.clear();throw new SessionEnded();}
  let data;
  try{data=await view(path,controller.signal);}catch(error){
   if(epoch!==sessionEpoch||identity!==authIdentity())throw new SessionEnded();
   if([401,403].includes(error.status)){if(token===pb.authStore.token)pb.authStore.clear();throw new SessionEnded();}
   throw error;
  }
  if(epoch!==sessionEpoch||identity!==authIdentity())throw new SessionEnded();return data;
 }finally{requests.delete(controller)}
}
let operations=[],selected=null,detailVersion=0,searchTimer,searchVersion=0;
function navigation(){const [path,query='']=location.hash.replace(/^#\/?/,'').split('?');const params=new URLSearchParams(query);return {path,q:params.get('q')||'',offset:Math.max(0,Number(params.get('offset'))||0),collection:params.get('collection')==='traces'?'traces':'operations'};}
function saveNavigation(path=navigation().path,replace=false){const params=new URLSearchParams();if($('collection').value==='traces')params.set('collection','traces');if($('filter').value)params.set('q',$('filter').value);if(navigation().offset)params.set('offset',navigation().offset);history[replace?'replaceState':'pushState'](null,'','#/'+path+(params.size?'?'+params:''));}
async function openNavigation(){
 const {path}=navigation();const match=/^(operations|traces)\/([a-z0-9]{15})$/.exec(path);
 if(!match){selected=null;detailVersion++;$('detail').hidden=true;return;}
 const version=++searchVersion;detailVersion++;$('detail').hidden=true;$('measurements').replaceChildren();$('sql').textContent='';
 try{let traces,limited=false;
 if(match[1]==='operations'){const data=await get('operation?id='+match[2]);if(!data.operation)throw Error('Record unavailable.');traces=data.recent;limited=data.recent_limited;}
 else {const data=await get('trace?id='+match[2]);if(!data.trace)throw Error('Record unavailable.');traces=[{...data.trace,kind:data.spans.some(s=>s.name==='http.client')?'client':data.spans.some(s=>s.name==='auth')?'server':'unknown'}];}
 if(version!==searchVersion||!authenticated)return;
 const op=groupOperations(traces)[0];if(op){op.limited=limited;await showDetail(op,false);}else {$('detail').hidden=true;$('notice').textContent='This operation has no measurements.';}
 }catch(error){if(version===searchVersion&&authenticated){$('detail').hidden=true;$('notice').textContent=error.message;}}
}
$('login').addEventListener('click',async()=>{
 $('login').disabled=true;
 const epoch=sessionEpoch,token=pb.authStore.token;
 try{
  // Separate store prevents a delayed popup completing after a different login.
  const {default:PocketBase,BaseAuthStore}=await import('./pocketbase.es.mjs');
  const login=new PocketBase(location.origin,new BaseAuthStore());
  const auth=await login.collection('users').authWithOAuth2({provider:'google'});
  if(epoch===sessionEpoch&&token===pb.authStore.token)pb.authStore.save(auth.token,auth.record);
 }catch{if(epoch===sessionEpoch)$('login-status').textContent='Sign-in failed. Please try again.';}
 finally{$('login').disabled=false;}
});
window.addEventListener('hashchange',()=>{if(authenticated){$('collection').value=navigation().collection;$('filter').value=navigation().q;void refresh();void openNavigation();}});

function renderOperations(){
 const filter='';$('recent').replaceChildren();let visible=0;
 for(const op of operations){
  const searchable=op.traces.map(t=>[t.service,t.method,t.route,t.status,t.correlation_id].join(' ')).join(' ').toLowerCase();if(filter&&!searchable.includes(filter))continue;
  visible++;const tr=node('tr',undefined,op.key===selected?'selected':'');cell(tr,timestamp(op.started),'number');
  const operation=cell(tr,op.primary.method+' '+op.primary.route,'route');operation.append(node('span',op.service,'subline'));
  cell(tr,elapsed(op.clients),'number');cell(tr,elapsed(op.servers),'number');cell(tr,status(op),op.errors?'error':'');
  cell(tr,'').append(node('span',pairing(op),'badge'+(op.paired?' paired':'')));
  const button=node('button','Inspect');button.type='button';button.setAttribute('aria-label','Inspect '+op.primary.method+' '+op.primary.route+' operation '+op.primary.request_id);button.onclick=()=>{saveNavigation(navigation().collection==='traces'?'traces/'+op.primary.id:'operations/'+op.primary.operation);void showDetail(op,true);};cell(tr,'').append(button);$('recent').append(tr);
 }
 $('empty').hidden=visible!==0;$('operation-count').textContent=operations.length;$('paired-count').textContent=operations.filter(op=>op.paired).length;$('error-count').textContent=operations.filter(op=>op.errors).length;
}
async function showDetail(op,focus=false){
 const version=++detailVersion;selected=op.key;renderOperations();$('detail').hidden=true;$('measurements').replaceChildren();$('sql').textContent='';
 try{
  // An operation can have many measurements. Keep the
  // detail fetch bounded and make the omission explicit, without inventing a pair.
  const details=await Promise.all(op.traces.slice(0,20).map(t=>get('trace?id='+encodeURIComponent(t.id))));
  if(version!==detailVersion||!authenticated)return;
  if(details.some(d=>!d.trace))throw Error('An operation measurement is no longer available.');
  $('detail').hidden=false;$('detail-title').textContent=op.primary.method+' '+op.primary.route;
  $('identity').textContent=op.service+' · '+timestamp(op.started)+' · operation '+(op.primary.operation||'unknown')+' · correlation '+(op.primary.correlation_id||'not supplied');
  $('client-time').textContent=elapsed(op.clients);$('server-time').textContent=elapsed(op.servers);
  const diff=op.paired?op.clients[0].duration_ms-op.servers[0].duration_ms:null;
  $('difference').textContent=diff===null?'—':ms(diff);
  $('timing-note').textContent=op.paired?(diff<0?'Server duration exceeds client duration in this pair. Measurement boundaries differ; this is not a network latency estimate.':'Server time is included in client elapsed time. The difference includes transport and client overhead; do not add the two durations.'):'A client/server comparison requires exactly one measurement of each kind. A missing measurement may arrive on the next refresh.';
  const sql=[...new Set(details.map(d=>d.trace.sql).filter(Boolean))];
  $('sql').textContent=sql.length===1?sql[0]:sql.length?'SQL differs between measurements; see each measurement below.':'SQL text was not captured.';
  $('measurements').replaceChildren();
  if(op.traces.length>20||op.limited)$('measurements').append(node('p','Showing '+Math.min(20,op.traces.length)+' of '+(op.limited?'at least ':'')+op.traces.length+' measurements. Use the Traces collection and search this operation ID to browse all measurements.'));
  details.forEach((d,i)=>{
   const t=d.trace;const kind=role(op.traces[i]);const article=node('article',undefined,'measurement');
   const link=node('a','Permanent trace link');link.href='#/traces/'+t.id;article.append(link);
   const parent=node('a','Parent operation');parent.href='#/operations/'+t.operation;article.append(node('span',' · '),parent);
   article.append(node('h3',(kind==='client'?'Client':kind==='server'?'Server':'Unclassified')+' · '+t.service+' · '+ms(t.duration_ms)));
   article.append(node('p',t.method+' '+t.route+' · '+(t.status===0?'Transport error':'HTTP '+t.status),'muted'));
   if(sql.length>1&&t.sql)article.append(node('pre',t.sql));
   if(d.spans.length){article.append(node('p',(kind==='server'?'Server timeline':'Trace timeline')+' · milliseconds from the start of this measurement. Phases may overlap; gaps are unmeasured time. Zero-duration phases appear as vertical markers.','muted'),renderTimeline(t,d.spans));
   }else article.append(node('p','No phase breakdown recorded.','muted'));
   const raw=node('details');raw.append(node('summary','Trace identity'),node('pre','Request ID: '+t.request_id+'\nStarted: '+timestamp(t.started_at)));article.append(raw);$('measurements').append(article);
  });
  $('notice').textContent='';if(focus)$('detail').focus();
 }catch(e){if(version===detailVersion&&authenticated&&!(e instanceof SessionEnded))$('notice').textContent=e.message}
}
let loading=false,reportLoading=false,lastReport=0;
async function refreshReport(force=false){
 if(!authenticated||document.hidden||reportLoading||(!force&&!$('report-panel').open))return;
 if(!force&&Date.now()-lastReport<60000)return;
 reportLoading=true;
 try{
  const d=await get('report');$('report').replaceChildren();
  for(const r of d.report){const tr=node('tr');cell(tr,r.service+' · '+r.method+' '+r.route,'route');cell(tr,r.requests,'number');cell(tr,ms(r.avg_ms),'number');cell(tr,ms(r.max_ms),'number');cell(tr,r.errors,r.errors?'error':'');$('report').append(tr)}
  lastReport=Date.now();$('report-status').textContent=d.report.length?'Updated '+timestamp(new Date()):'No measurements in the last 24 hours.';
 }catch(e){if(authenticated&&!(e instanceof SessionEnded))$('report-status').textContent='Summary unavailable. Try Refresh.'}
 finally{reportLoading=false}
}
async function refresh(manual=false){
 if(!authenticated||document.hidden||loading)return;loading=true;const requestedHash=location.hash;$('refresh').disabled=true;
 try{
  const params=new URLSearchParams();if(navigation().collection==='traces')params.set('collection','traces');if(navigation().q)params.set('q',navigation().q);if(navigation().offset)params.set('offset',navigation().offset);
  const d=await get('data'+(params.size?'?'+params:''));if(requestedHash!==location.hash)return;operations=navigation().collection==='traces'?d.recent.map(t=>({...groupOperations([t])[0],key:'trace:'+t.id})):groupOperations(d.recent);renderOperations();
  $('collection-title').textContent=navigation().collection==='traces'?'Traces':'Operations';$('collection-count-label').textContent=navigation().collection==='traces'?'Traces on this page':'Operations on this page';
  $('window-note').textContent=d.recent_limited?'Measurement limit reached; some operations may be incomplete.':'Up to 50 '+navigation().collection+' on this page. Use search or Next to find older records.';
  $('status').textContent=operations.length?'Updated '+timestamp(new Date()):'No operations yet. Capture a request to see its timings.';$('notice').textContent='';
  $('previous').disabled=navigation().offset===0;$('next').disabled=operations.length<50;
  // A permanent selection remains available outside the current search window.
  if(manual)await refreshReport(true);
 }catch(e){if(authenticated&&!(e instanceof SessionEnded)){$('notice').textContent=e.message;$('status').textContent='Refresh failed; showing the last loaded data.'}}
 finally{loading=false;$('refresh').disabled=false;if(authenticated&&requestedHash!==location.hash)void refresh();}
}
async function init(){
 if(!pb.authStore.isValid){pb.authStore.clear();clearSession();return;}
 const epoch=sessionEpoch;
 try{
  await renew();if(epoch!==sessionEpoch)return;
  authenticated=true;$('signin').hidden=true;$('dashboard').hidden=false;$('account-actions').hidden=false;
  const user=pb.authStore.record;$('account-name').textContent=user.name||user.email||'Signed in';
  $('collection').value=navigation().collection;$('filter').value=navigation().q;await refresh();await openNavigation();
 }catch{if(epoch===sessionEpoch)clearSession('Cannot verify your session. Reload to try again.');}
}
function logout(){pb.authStore.clear();clearSession('You have signed out.');}
function authIdentity(){const user=pb.authStore.record;return pb.authStore.isValid&&user?JSON.stringify([user.id,!!user.can_view_all_traces,!!user.disabled]):'';}
let identity=authIdentity();
pb.authStore.onChange(()=>{
 const next=authIdentity();
 if(next!==identity || !next){identity=next;clearSession();if(next)void init();}
});
// Polling remains authoritative; realtime transport reconnects cannot lose data.
window.addEventListener('online',()=>{if(authenticated){void refresh();void openNavigation();}else void init();});
setInterval(()=>{if(authenticated&&!document.hidden)void renew().catch(()=>{});},60000);
$('refresh').onclick=()=>refresh(true);$('logout').onclick=logout;$('filter').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{const params=new URLSearchParams();if($('collection').value==='traces')params.set('collection','traces');if($('filter').value)params.set('q',$('filter').value);history.replaceState(null,'','#/'+navigation().path+(params.size?'?'+params:''));void refresh(true);},250);};
$('collection').onchange=()=>{const params=new URLSearchParams();params.set('collection',$('collection').value);if($('filter').value)params.set('q',$('filter').value);location.hash='#/?'+params;};
$('previous').onclick=()=>{const params=new URLSearchParams();params.set('collection',$('collection').value);params.set('q',$('filter').value);params.set('offset',Math.max(0,navigation().offset-50));location.hash='#/'+navigation().path+'?'+params;};
$('next').onclick=()=>{const params=new URLSearchParams();params.set('collection',$('collection').value);params.set('q',$('filter').value);params.set('offset',navigation().offset+50);location.hash='#/'+navigation().path+'?'+params;};
$('copy-record').onclick=()=>void navigator.clipboard.writeText(new URL('#/'+navigation().path,location.href).href).catch(()=>$('notice').textContent='Unable to copy link.');
$('copy-search').onclick=()=>{const params=new URLSearchParams();if(navigation().offset)params.set('offset',navigation().offset);if($('collection').value==='traces')params.set('collection','traces');if($('filter').value)params.set('q',$('filter').value);void navigator.clipboard.writeText(new URL('#/?'+params,location.href).href).catch(()=>$('notice').textContent='Unable to copy link.');};
$('report-panel').addEventListener('toggle',()=>refreshReport());
document.addEventListener('visibilitychange',()=>{if(!document.hidden){refresh();refreshReport();}});
window.addEventListener('pagehide',()=>clearSession());
window.addEventListener('pageshow',event=>{if(event.persisted)init()});
setInterval(()=>refresh(),5000);setInterval(()=>refreshReport(),60000);init();
