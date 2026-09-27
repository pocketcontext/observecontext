/// <reference path="../pb_data/types.d.ts" />
// Model hooks cover REST, dashboard, and internal account changes alike.
onRecordCreateExecute((e) => require(`${__hooks}/user_directory.js`).sync(e, false), "users");
onRecordUpdateExecute((e) => require(`${__hooks}/user_directory.js`).sync(e, false), "users");
onRecordDeleteExecute((e) => require(`${__hooks}/user_directory.js`).sync(e, true), "users");
// Authority is a transactional mirror, never an independent grant API.
onRecordCreateRequest(()=>{throw new ForbiddenError('Authority is managed through users.');},'trace_authority');
onRecordUpdateRequest(()=>{throw new ForbiddenError('Authority is managed through users.');},'trace_authority');
onRecordDeleteRequest(()=>{throw new ForbiddenError('Authority is managed through users.');},'trace_authority');
