function invalid(message) {throw new BadRequestError(message);}
function finite(value, field, max=86400000) {
  if(typeof value!=='number'||!Number.isFinite(value)||value<0||value>max) invalid(field+' must be a bounded nonnegative number');
}
function ingest(e) {
  if(!e.auth||e.auth.collection().name!=='users'||e.auth.getBool('disabled')) throw new ForbiddenError('Ingest with an enabled ordinary user.');
  const body=JSON.parse(JSON.stringify(e.requestInfo().body));
  const allowed=['version','request_id','correlation_id','service','method','route','started_at','duration_ms','status','user_id','sql','rows','truncated','spans'];
  for(const key of Object.keys(body)) if(!allowed.includes(key)) invalid('Unknown or server-managed trace field: '+key);
  if(body.version!==1) invalid('version must be 1');
  finite(body.duration_ms,'duration_ms');
  if(typeof body.status!=='number'||!Number.isInteger(body.status)||(body.status!==0&&body.status<100)||body.status>599) invalid('Invalid HTTP status');
  if(body.rows!==undefined){finite(body.rows,'rows',9007199254740991);if(!Number.isInteger(body.rows))invalid('rows must be an integer');}
  if(body.truncated!==undefined&&typeof body.truncated!=='boolean')invalid('truncated must be boolean');
  if(typeof body.route!=='string'||!body.route.startsWith('/')||body.route.includes('?')||body.route.includes('#'))invalid('route must be a path pattern without query or fragment');
  for(const field of ['request_id','service','method']) if(typeof body[field]!=='string')invalid(field+' must be text');
  for(const field of ['correlation_id','user_id','sql']) if(body[field]!==undefined&&typeof body[field]!=='string')invalid(field+' must be text');
  const spans=body.spans===undefined?[]:JSON.parse(JSON.stringify(e.record)).spans;
  if(!Array.isArray(spans)||spans.length>128) invalid('spans must contain at most 128 entries');
  for(const span of spans) {
    if(!span||typeof span!=='object'||Array.isArray(span))invalid('Invalid span');
    for(const key of Object.keys(span))if(!['name','offset_ms','duration_ms'].includes(key))invalid('Unknown span field');
    if(typeof span.name!=='string'||!/^[A-Za-z0-9_.:-]{1,100}$/.test(span.name))invalid('Invalid span name');
    finite(span.offset_ms,'offset_ms');finite(span.duration_ms,'span duration_ms');
    if(span.offset_ms+span.duration_ms>body.duration_ms+1)invalid('Span extends beyond request duration');
  }
  const original=e.app;
  original.runInTransaction(app=>{
    e.app=app;
    try {
      e.record.set('created_by',e.auth.id);
      e.record.set('spans',spans);
      e.next();
      for(let i=0;i<spans.length;i++) {
        const row=new Record(app.findCollectionByNameOrId('spans'));
        row.set('trace',e.record.id);row.set('ordinal',i);
        for(const key of ['name','offset_ms','duration_ms'])row.set(key,spans[i][key]);
        app.save(row);
      }
    } finally {e.app=original;}
  });
}
module.exports={ingest};
