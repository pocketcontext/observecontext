migrate((app) => {
  const access = "@request.auth.id != '' && @request.auth.collectionName = 'users' && @request.auth.disabled = false";
  const text = (name, required=false, max=200) => ({name,type:'text',required,max});
  const number = (name,max=86400000,onlyInt=false) => ({name,type:'number',min:0,max,onlyInt});
  const relation = (name,collection) => ({name,type:'relation',collectionId:app.findCollectionByNameOrId(collection).id,maxSelect:1,required:true,cascadeDelete:false});
  const create=(name,fields,indexes=[],writable=false)=>app.save(new Collection({name,type:'base',listRule:access,viewRule:access,createRule:writable?access:null,updateRule:null,deleteRule:null,fields,indexes}));
  create('user_directory',[text('name',true,200)]);
  create('traces',[
    {name:'version',type:'number',required:true,min:1,max:1,onlyInt:true},
    {...text('request_id',true,32),pattern:'^[a-f0-9]{32}$'},
    {...text('correlation_id',false,128),pattern:'^[A-Za-z0-9_.:-]*$'},
    {...text('service',true,100),pattern:'^[A-Za-z0-9_.:-]+$'},
    {...text('method',true,16),pattern:'^[A-Z]+$'},text('route',true,500),
    {name:'started_at',type:'date',required:true},number('duration_ms'),
    {name:'status',type:'number',min:0,max:599,onlyInt:true},
    text('user_id',false,200),text('sql',false,16384),number('rows',9007199254740991,true),
    {name:'truncated',type:'bool'},{name:'spans',type:'json',maxSize:65536},
    relation('created_by','users'),{name:'created',type:'autodate',onCreate:true}
  ],['CREATE UNIQUE INDEX idx_trace_request ON traces (service,request_id)','CREATE INDEX idx_trace_started ON traces (started_at)','CREATE INDEX idx_trace_correlation ON traces (correlation_id)','CREATE INDEX idx_trace_service_time ON traces (service,started_at)'],true);
  create('spans',[relation('trace','traces'),number('ordinal',127,true),text('name',true,100),number('offset_ms'),number('duration_ms')],['CREATE UNIQUE INDEX idx_span_ordinal ON spans (trace,ordinal)','CREATE INDEX idx_span_name ON spans (name)']);
  const settings=app.settings();settings.batch.enabled=true;settings.batch.maxRequests=20;settings.batch.timeout=5;app.save(settings);
},()=>{throw new Error('Restore a verified backup to roll back the initial schema.');});
