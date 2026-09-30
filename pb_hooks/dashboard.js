/// <reference path="../pb_data/types.d.ts" />
// Public static shell. All private data uses the ordinary authenticated SQL API.
function handle(e,kind){
 const files={index:['index.html','text/html; charset=utf-8'],css:['dashboard.css','text/css; charset=utf-8'],js:['dashboard.js','text/javascript; charset=utf-8'],api:['api.js','text/javascript; charset=utf-8'],sdk:['pocketbase.es.mjs','text/javascript; charset=utf-8']};
 const h=e.response.header();h.set('Cache-Control','no-store');h.set('Referrer-Policy','no-referrer');h.set('X-Content-Type-Options','nosniff');h.set('X-Frame-Options','DENY');
 h.set('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
 return e.blob(200,files[kind][1],$os.readFile('web/'+files[kind][0]));
}
module.exports={handle};
