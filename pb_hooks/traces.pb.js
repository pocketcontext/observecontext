onRecordCreateRequest((e)=>require(`${__hooks}/traces.js`).ingest(e),'traces');
onRecordUpdateRequest(()=>{throw new ForbiddenError('Traces and spans are immutable.');},'traces','spans','operations');
onRecordDeleteRequest(()=>{throw new ForbiddenError('Traces and spans are retained.');},'traces','spans','operations');
onRecordCreateRequest(()=>{throw new ForbiddenError('Spans are created atomically through traces.');},'spans');

onRecordCreateRequest((e)=>require(`${__hooks}/traces.js`).operation(e),'operations');
