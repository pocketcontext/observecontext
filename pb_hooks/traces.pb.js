onRecordCreateRequest((e)=>require(`${__hooks}/traces.js`).ingest(e),'traces');
onRecordUpdateRequest(()=>{throw new ForbiddenError('Traces and spans are immutable.');},'traces','spans');
onRecordDeleteRequest(()=>{throw new ForbiddenError('Traces and spans are retained.');},'traces','spans');
onRecordCreateRequest(()=>{throw new ForbiddenError('Spans are created atomically through traces.');},'spans');
