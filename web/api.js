import PocketBase, { LocalAuthStore, BaseAuthStore } from './pocketbase.es.mjs';
export const pb = new PocketBase(location.origin, new LocalAuthStore('observecontext.auth'));
pb.autoCancellation(false);
// A refresh on an isolated store cannot resurrect a signed-out/switched account.
let refreshPending, refreshToken;
export function renew() {
 const token=pb.authStore.token, record=pb.authStore.record;
 if (refreshPending && refreshToken===token) return refreshPending;
 refreshToken=token;
 const isolated=new PocketBase(location.origin,new BaseAuthStore());
 isolated.authStore.save(token,record);
 const pending=isolated.collection('users').authRefresh().then(auth=>{
  if(pb.authStore.token===token)pb.authStore.save(auth.token,auth.record);
 }).catch(error=>{if(pb.authStore.token===token && [401,403].includes(error.status))pb.authStore.clear();throw error;}).finally(()=>{if(refreshPending===pending)refreshPending=undefined;});
 refreshPending=pending;return pending;
}
export async function view(path,signal){
 const [kind,search='']=path.split('?'),params=new URLSearchParams(search);
 async function query(sql){
  const result=await pb.send('/api/context/query',{method:'POST',body:{sql},signal,requestKey:null});
  if(!Array.isArray(result.columns)||!Array.isArray(result.rows)||result.truncated)throw Error('Incomplete query result');
  return result.rows.map(row=>Object.fromEntries(result.columns.map((column,i)=>[column,row[i]])));
 }
    if(kind === 'operation') {
      const id=params.get('id'); if(!/^[a-z0-9]{15}$/.test(id)) throw Error('Invalid record or page');
      const operations=await query("SELECT id,source,correlation_id,created FROM operations WHERE id='"+id+"' LIMIT 1");
      const recent=operations.length?await query(RECENT_SQL.replace('FROM traces GROUP BY operation',"FROM traces WHERE operation='"+id+"' GROUP BY operation")):[];
      return ({operation:operations[0] || null,recent,recent_limited:recent.length>=500});
    }
    if(kind === 'data') {
      const term=String(params.get('q')||'').slice(0,500).replace(/'/g,"''"), offset=Number(params.get('offset')||0);
      if(!Number.isInteger(offset)||offset<0||offset>100000)throw Error('Invalid record or page');
      const where=term?" WHERE instr(lower(id||' '||operation||' '||service||' '||route||' '||request_id||' '||correlation_id||' '||status),lower('"+term+"'))>0 ":' ';
      let sql=RECENT_SQL.replace('FROM traces GROUP BY operation','FROM traces'+where+'GROUP BY operation').replace('LIMIT 50\n','LIMIT 50 OFFSET '+offset+'\n');
      if(params.get('collection')==='traces'){sql='WITH selected AS (SELECT id,operation,service,request_id,correlation_id,method,route,started_at,duration_ms,status FROM traces'+where+'ORDER BY started_at DESC,id DESC LIMIT 50 OFFSET '+offset+'), kinds AS ('+RECENT_SQL.split('), kinds AS (')[1];}
      const recent=await query(sql); return ({recent,recent_limited:recent.length>=500}); }
    if(kind === 'report') return ({report:await query(REPORT_SQL)});
    if(kind === 'trace') {
      const id=params.get('id'); if(!/^[a-z0-9]{15}$/.test(id)) throw Error('Invalid record or page');
      const records=await query("SELECT id,operation,service,request_id,correlation_id,method,route,started_at,duration_ms,status,sql FROM traces WHERE id='"+id+"' LIMIT 1");
      const spans=await query("SELECT name,offset_ms,duration_ms FROM spans WHERE trace='"+id+"' ORDER BY offset_ms,ordinal LIMIT 128");
      return ({trace:records[0] || null,spans});
    }

 throw Error('Unknown view');
}
const RECENT_SQL = "WITH recent_operations AS (\n SELECT operation,max(started_at) AS latest,max(id) AS tie\n FROM traces GROUP BY operation ORDER BY latest DESC,tie DESC LIMIT 50\n), selected AS (\n SELECT id,operation,service,request_id,correlation_id,method,route,started_at,duration_ms,status\n FROM traces WHERE operation IN (SELECT operation FROM recent_operations)\n), kinds AS (\n SELECT s.trace,max(CASE WHEN s.name='http.client' THEN 1 ELSE 0 END) AS client,\n max(CASE WHEN s.name='auth' THEN 1 ELSE 0 END) AS server\n FROM spans s WHERE s.trace IN (SELECT id FROM selected) GROUP BY s.trace\n)\nSELECT t.id,t.operation,t.service,t.request_id,t.correlation_id,t.method,t.route,\n t.started_at,t.duration_ms,t.status,\n CASE WHEN k.client=1 THEN 'client' WHEN k.server=1 THEN 'server' ELSE 'unknown' END AS kind,\n t.operation AS operation_key\n FROM selected t LEFT JOIN kinds k ON k.trace=t.id\n ORDER BY t.started_at DESC,t.id DESC LIMIT 500";
const REPORT_SQL = "SELECT service,method,route,count(id) AS requests,round(avg(duration_ms),3) AS avg_ms,max(duration_ms) AS max_ms,sum(CASE WHEN status=0 OR status>=400 THEN 1 ELSE 0 END) AS errors FROM traces WHERE started_at >= strftime('%Y-%m-%d %H:%M:%fZ','now','-24 hours') GROUP BY service,method,route ORDER BY max_ms DESC LIMIT 100";
