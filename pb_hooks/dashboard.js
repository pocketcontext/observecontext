/// <reference path="../pb_data/types.d.ts" />
// Browser sessions contain only opaque identifiers. Provider and PocketBase
// tokens stay in bounded process memory and disappear when the server restarts.
const PREFIX = 'observecontext.dashboard.';
function fail(status) { const error = new Error('Dashboard request failed'); error.status = status; throw error; }
function config(e) {
  const origin = String(e.app.settings().meta.appURL).replace(/\/+$/, '');
  const match = /^(https?):\/\/([a-z0-9.-]+(?::[0-9]+)?)$/.exec(origin);
  if (!match || (match[1] !== 'https' && !/^127\.0\.0\.1(?::[0-9]+)?$/.test(match[2]) && !/^localhost(?::[0-9]+)?$/.test(match[2]))) fail(503);
  const upstream = String($os.getenv('OBSERVECONTEXT_DASHBOARD_INTERNAL_URL') || 'http://127.0.0.1:80');
  if (!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(upstream) || Number(upstream.split(':')[2]) > 65535 || Number(upstream.split(':')[2]) < 1) fail(503);
  const ttl = Number($os.getenv('OBSERVECONTEXT_DASHBOARD_SESSION_TTL_SECONDS') || 3600);
  if (!Number.isInteger(ttl) || ttl < 1 || ttl > 28800) fail(503);
  return {origin,host:match[2],secure:match[1] === 'https',upstream,ttl};
}
function headers(e) {
  const h = e.response.header();
  h.set('Cache-Control', 'no-store'); h.set('Referrer-Policy', 'no-referrer');
  h.set('X-Content-Type-Options', 'nosniff'); h.set('X-Frame-Options', 'DENY');
  h.set('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
  e.set('__skipSuccessActivityLogger', true);
}
function guard(e, cfg, kind) {
  if (String(e.request.host) !== cfg.host) fail(403);
  const origin = e.request.header.get('Origin');
  if (e.request.header.values('Origin').length > 1) fail(403);
  if (origin && origin !== cfg.origin) fail(403);
  if (e.request.header.get('Sec-Fetch-Site') === 'cross-site' && !['index','css','js','login','callback'].includes(kind)) fail(403);
}
function cookieName(cfg, kind) { return (cfg.secure ? '__Host-' : '') + 'oc_dashboard_' + kind; }
function cookie(e, cfg, kind, value, age) {
  e.setCookie(new Cookie({name:cookieName(cfg,kind),value,path:'/',maxAge:age,secure:cfg.secure,httpOnly:true,sameSite:2}));
}
function cookieValue(e, cfg, kind) {
  try { return e.request.cookie(cookieName(cfg,kind)).value; } catch (_) { return ''; }
}
function key(value) { return $security.sha256(value); }
// Store.SetFunc serializes the entire transaction under its Go mutex. Strings
// cross JS runtimes safely; no mutable JS objects are shared among requests.
function transact(app, kind, fn) {
  let result;
  app.store().setFunc(PREFIX + kind, (old) => {
    const records = old ? JSON.parse(String(old)) : {};
    const now = Date.now();
    for (const id of Object.keys(records)) if (records[id].expires <= now) delete records[id];
    result = fn(records);
    return JSON.stringify(records);
  });
  return result;
}
function insert(app, kind, id, value) {
  const accepted = transact(app,kind,(records) => {
    if (Object.keys(records).length >= 1024) return false;
    records[key(id)] = value; return true;
  });
  if (!accepted) fail(503);
}
function remove(app, kind, id) { return transact(app,kind,(records) => { const value=records[key(id)]; delete records[key(id)]; return value; }); }
function upstream(cfg, path, method, body, token) {
  const h = {'Content-Type':'application/json'};
  if (token) h.Authorization = token;
  let response;
  try { response = $http.send({url:cfg.upstream+path,method:method || 'GET',headers:h,body:body ? JSON.stringify(body) : '',timeout:15}); }
  catch (_) { fail(502); }
  if (response.statusCode < 200 || response.statusCode >= 300) fail(response.statusCode === 401 || response.statusCode === 403 ? 401 : 502);
  return response.json;
}
function session(e,cfg) {
  const id = cookieValue(e,cfg,'session');
  if (!/^[a-zA-Z0-9]{64}$/.test(id)) fail(401);
  const entry = transact(e.app,'sessions',(records) => records[key(id)]);
  if (!entry) { cookie(e,cfg,'session','',-1); fail(401); }
  try {
    const user = e.app.findAuthRecordByToken(entry.token,'auth');
    if (user.collection().name !== 'users' || user.id !== entry.user || user.getBool('disabled') || user.tokenKey() !== entry.tokenKey) throw new Error('Invalid session');
    return {id,entry,user};
  } catch (_) { remove(e.app,'sessions',id); cookie(e,cfg,'session','',-1); fail(401); }
}
function query(cfg, active, sql) {
  const result = upstream(cfg,'/api/context/query','POST',{sql},active.entry.token);
  if (!Array.isArray(result.columns) || !Array.isArray(result.rows) || result.truncated) fail(502);
  return result.rows.map(row => { const value={}; result.columns.forEach((column,i)=>value[column]=row[i]); return value; });
}
function login(e,cfg) {
  const methods = upstream(cfg,'/api/collections/users/auth-methods');
  const provider = (methods.oauth2 && methods.oauth2.providers || []).find(p=>p.name === 'google');
  if (!provider || !provider.codeVerifier || !provider.authURL) fail(503);
  const id=$security.randomString(64), state='ocd_'+$security.randomString(48);
  const redirect=cfg.origin+'/api/oauth2-redirect';
  // PocketBase supplies a fresh provider URL and PKCE pair; replace only its
  // state and append the operator-registered redirect, never a browser URL.
  let authURL=String(provider.authURL);
  if (!/^https?:\/\//.test(authURL)) fail(503);
  authURL=authURL.replace(/([?&])state=[^&]*/, '$1state='+encodeURIComponent(state));
  if (!/[?&]state=/.test(authURL)) authURL+=(authURL.includes('?')?'&':'?')+'state='+encodeURIComponent(state);
  authURL=authURL.replace(/([?&])redirect_uri=[^&]*/, '$1redirect_uri='+encodeURIComponent(redirect));
  if (!/[?&]redirect_uri=/.test(authURL)) authURL+='&redirect_uri='+encodeURIComponent(redirect);
  const previous=cookieValue(e,cfg,'flow'); if(previous) remove(e.app,'flows',previous);
  insert(e.app,'flows',id,{expires:Date.now()+300000,state,verifier:provider.codeVerifier,redirect});
  cookie(e,cfg,'flow',id,300);
  return e.redirect(302,authURL);
}
function callback(e,cfg,parameters) {
  const id=cookieValue(e,cfg,'flow');
  const flow=remove(e.app,'flows',id); // Consume before exchange, including failure.
  cookie(e,cfg,'flow','',-1);
  if (!flow || parameters.malformed || !$security.equal(flow.state,parameters.state) || !parameters.code || parameters.code.length > 4096 || parameters.error) fail(403);
  const auth=upstream(cfg,'/api/collections/users/auth-with-oauth2','POST',{provider:'google',code:parameters.code,codeVerifier:flow.verifier,redirectURL:flow.redirect});
  let user;
  try { user=e.app.findAuthRecordByToken(auth.token,'auth'); } catch (_) { fail(401); }
  if (user.collection().name !== 'users' || user.getBool('disabled')) fail(401);
  const previous=cookieValue(e,cfg,'session'); if(previous) remove(e.app,'sessions',previous);
  const sid=$security.randomString(64);
  insert(e.app,'sessions',sid,{expires:Date.now()+cfg.ttl*1000,token:auth.token,user:user.id,tokenKey:user.tokenKey(),csrf:$security.randomString(48)});
  cookie(e,cfg,'session',sid,cfg.ttl);
  return e.redirect(303,'/dashboard');
}
function handle(e,kind,parameters) {
  headers(e);
  try {
    const cfg=config(e); guard(e,cfg,kind);
    if (kind === 'login') return login(e,cfg);
    if (kind === 'callback') return callback(e,cfg,parameters);
    if (kind === 'index' || kind === 'css' || kind === 'js') {
      const files={index:['index.html','text/html; charset=utf-8'],css:['dashboard.css','text/css; charset=utf-8'],js:['dashboard.js','text/javascript; charset=utf-8']};
      return e.blob(200,files[kind][1],$os.readFile('web/'+files[kind][0]));
    }
    const active=session(e,cfg);
    if(kind === 'session') return e.json(200,{user:{id:active.user.id,name:active.user.getString('name'),email:active.user.email()},csrf:active.entry.csrf});
    if(kind === 'logout') {
      if(e.request.header.get('Origin') !== cfg.origin || !$security.equal(e.request.header.get('X-CSRF-Token'),active.entry.csrf)) fail(403);
      remove(e.app,'sessions',active.id); cookie(e,cfg,'session','',-1); return e.json(200,{ok:true});
    }
    if(kind === 'data') { const recent=query(cfg,active,RECENT_SQL); return e.json(200,{recent,recent_limited:recent.length>=500}); }
    if(kind === 'report') return e.json(200,{report:query(cfg,active,REPORT_SQL)});
    if(kind === 'trace') {
      const id=e.request.url.query().get('id'); if(!/^[a-z0-9]{15}$/.test(id)) fail(400);
      const records=query(cfg,active,"SELECT * FROM traces WHERE id='"+id+"' LIMIT 1");
      const spans=query(cfg,active,"SELECT name,offset_ms,duration_ms FROM spans WHERE trace='"+id+"' ORDER BY offset_ms,ordinal LIMIT 128");
      return e.json(200,{trace:records[0] || null,spans});
    }
    fail(404);
  } catch (error) {
    // Return a generic response rather than propagating provider errors or
    // tokens into activity logs. Every response, including failures, is no-store.
    if (kind === 'callback') return e.html(error.status || 503, '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ObserveContext sign-in</title><link rel="stylesheet" href="/dashboard/assets/dashboard.css"></head><body><main><h1>Sign-in could not be completed</h1><p><a href="/dashboard">Return to sign in</a></p></main></body></html>');
    return e.json(error.status || 503,{message:error.status === 401 ? 'Sign in to ObserveContext.' : 'Dashboard request failed.'});
  }
}
function intercept(e) {
  if(e.request.url.path !== '/api/oauth2-redirect') return e.next();
  const q=e.request.url.query(), state=q.get('state');
  const counts={state:0,code:0}; let ours=String(state).startsWith('ocd_');
  for(const pair of String(e.request.url.rawQuery).split('&')) {
    const parts=pair.split('=');
    try {
      const name=decodeURIComponent(parts[0].replace(/\+/g,' '));
      if(name === 'state' || name === 'code') counts[name]++;
      if(name === 'state' && decodeURIComponent((parts[1] || '').replace(/\+/g,' ')).startsWith('ocd_')) ours=true;
    } catch (_) {}
  }
  if(!ours) return e.next();
  const parameters={state,code:q.get('code'),error:q.get('error'),malformed:counts.state !== 1 || counts.code > 1};
  // Strip authorization codes before any downstream logger can inspect them.
  e.request.url.rawQuery='';
  if(e.request.method !== 'GET') { headers(e); return e.json(405,{message:'Method not allowed.'}); }
  return handle(e,'callback',parameters);
}
module.exports={handle,intercept};

const RECENT_SQL = "WITH recent_operations AS (\n SELECT operation,max(started_at) AS latest,max(id) AS tie\n FROM traces GROUP BY operation ORDER BY latest DESC,tie DESC LIMIT 50\n), selected AS (\n SELECT id,operation,service,request_id,correlation_id,method,route,started_at,duration_ms,status\n FROM traces WHERE operation IN (SELECT operation FROM recent_operations)\n), kinds AS (\n SELECT s.trace,max(CASE WHEN s.name='http.client' THEN 1 ELSE 0 END) AS client,\n max(CASE WHEN s.name='auth' THEN 1 ELSE 0 END) AS server\n FROM spans s WHERE s.trace IN (SELECT id FROM selected) GROUP BY s.trace\n)\nSELECT t.id,t.operation,t.service,t.request_id,t.correlation_id,t.method,t.route,\n t.started_at,t.duration_ms,t.status,\n CASE WHEN k.client=1 THEN 'client' WHEN k.server=1 THEN 'server' ELSE 'unknown' END AS kind,\n t.operation AS operation_key\n FROM selected t LEFT JOIN kinds k ON k.trace=t.id\n ORDER BY t.started_at DESC,t.id DESC LIMIT 500";
const REPORT_SQL = "SELECT service,method,route,count(id) AS requests,round(avg(duration_ms),3) AS avg_ms,max(duration_ms) AS max_ms,sum(CASE WHEN status=0 OR status>=400 THEN 1 ELSE 0 END) AS errors FROM traces WHERE started_at >= strftime('%Y-%m-%d %H:%M:%fZ','now','-24 hours') GROUP BY service,method,route ORDER BY max_ms DESC LIMIT 100";
