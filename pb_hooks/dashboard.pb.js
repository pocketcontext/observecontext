/// <reference path="../pb_data/types.d.ts" />
// Intercept only our state namespace; the stock realtime OAuth callback remains
// unchanged for portable clients and other PocketBase SDKs.
routerUse(new Middleware((e) => require(`${__hooks}/dashboard.js`).intercept(e), -1050));
routerAdd('GET','/{$}',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'index'));
routerAdd('GET','/dashboard',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'index'));
routerAdd('GET','/dashboard/',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'index'));
routerAdd('GET','/dashboard/assets/dashboard.css',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'css'));
routerAdd('GET','/dashboard/assets/dashboard.js',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'js'));
routerAdd('GET','/api/dashboard/login',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'login'));
routerAdd('GET','/api/dashboard/session',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'session'));
routerAdd('GET','/api/dashboard/data',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'data'));
routerAdd('GET','/api/dashboard/report',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'report'));
routerAdd('GET','/api/dashboard/trace',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'trace'));
routerAdd('POST','/api/dashboard/logout',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'logout'));
