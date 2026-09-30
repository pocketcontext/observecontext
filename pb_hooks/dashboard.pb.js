routerAdd('GET','/{$}',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'index'));
routerAdd('GET','/dashboard',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'index'));
routerAdd('GET','/dashboard/',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'index'));
routerAdd('GET','/dashboard/assets/dashboard.css',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'css'));
routerAdd('GET','/dashboard/assets/dashboard.js',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'js'));
routerAdd('GET','/dashboard/assets/api.js',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'api'));
routerAdd('GET','/dashboard/assets/pocketbase.es.mjs',(e)=>require(`${__hooks}/dashboard.js`).handle(e,'sdk'));
