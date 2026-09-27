migrate((app) => {
  const enabled="@request.auth.id != '' && @request.auth.collectionName = 'users' && @request.auth.disabled = false";
  const users=app.findCollectionByNameOrId('users');
  users.fields.add(new BoolField({name:'can_view_all_traces'}));app.save(users);
  const relation=(name,collection,required=true)=>({name,type:'relation',collectionId:app.findCollectionByNameOrId(collection).id,maxSelect:1,required,cascadeDelete:false});
  const authority=new Collection({name:'trace_authority',type:'base',listRule:null,viewRule:null,createRule:null,updateRule:null,deleteRule:null,fields:[{name:'disabled',type:'bool'},{name:'can_view_all_traces',type:'bool'}]});app.save(authority);
  const operations=new Collection({name:'operations',type:'base',listRule:enabled+" && (owner = @request.auth.id || @request.auth.can_view_all_traces = true)",viewRule:enabled+" && (owner = @request.auth.id || @request.auth.can_view_all_traces = true)",createRule:enabled,updateRule:null,deleteRule:null,fields:[relation('owner','users'),{name:'source',type:'text',required:true,max:100,pattern:'^[A-Za-z0-9_.:-]+$'},{name:'correlation_id',type:'text',max:128,pattern:'^[A-Za-z0-9_.:-]*$'},{name:'client_key',type:'text',max:32,pattern:'^[a-f0-9]{32}$'},{name:'created',type:'autodate',onCreate:true}],indexes:["CREATE UNIQUE INDEX idx_operation_client ON operations (owner,client_key) WHERE client_key != ''",'CREATE INDEX idx_operation_owner ON operations (owner)','CREATE INDEX idx_operation_correlation ON operations (owner,source,correlation_id)']});app.save(operations);
  const traces=app.findCollectionByNameOrId('traces');
  traces.fields.add(new RelationField(relation('operation','operations',false)));
  traces.indexes=traces.indexes.filter(x=>!x.includes('idx_trace_request'));
  traces.indexes.push('CREATE UNIQUE INDEX idx_trace_request ON traces (created_by,service,request_id)');
  traces.indexes.push('CREATE INDEX idx_trace_operation ON traces (operation)');
  traces.listRule=enabled+" && (created_by = @request.auth.id || @request.auth.can_view_all_traces = true)";traces.viewRule=traces.listRule;app.save(traces);
  // Group legacy measurements ONLY within the authenticated uploader identity.
  // Source user_id and correlation labels never establish authority.
  while(true){
    const records=app.findRecordsByFilter('traces',"operation = ''",'id',500,0);
    if(!records.length)break;
    for(const trace of records){
      const owner=trace.getString('created_by'),correlation=trace.getString('correlation_id');
      const previous=correlation?app.findRecordsByFilter('operations',"owner = {:owner} && source = 'legacy' && correlation_id = {:correlation}",'',1,0,{owner,correlation}):[];
      let op=previous.length?previous[0]:new Record(operations);
      if(!previous.length){op.set('owner',owner);op.set('source','legacy');op.set('correlation_id',correlation);app.save(op);}
      trace.set('operation',op.id);app.save(trace);
    }
  }
  traces.fields.getByName('operation').required=true;app.save(traces);
  const spans=app.findCollectionByNameOrId('spans');spans.listRule=enabled+" && (trace.created_by = @request.auth.id || @request.auth.can_view_all_traces = true)";spans.viewRule=spans.listRule;app.save(spans);
  const directory=app.findCollectionByNameOrId('user_directory');directory.listRule=enabled+" && (id = @request.auth.id || @request.auth.can_view_all_traces = true)";directory.viewRule=directory.listRule;app.save(directory);
  let offset=0;
  while(true){const records=app.findRecordsByFilter('users','', 'id',500,offset);if(!records.length)break;
    for(const user of records){const row=new Record(authority);row.set('id',user.id);row.set('disabled',user.getBool('disabled'));row.set('can_view_all_traces',user.getBool('can_view_all_traces'));app.save(row);}offset+=records.length;}
},()=>{throw new Error('Private ownership migration requires restoring a verified backup to roll back.');});
