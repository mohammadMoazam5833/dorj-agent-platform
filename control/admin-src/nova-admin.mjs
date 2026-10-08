import http from "node:http";
import https from "node:https";
import crypto from "node:crypto";
import os from "node:os";
import path from "node:path";
import { readFile, writeFile, mkdir, rename } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const exec = promisify(execFile);
const HOME = "/home/moazemi-gc";
const PORT = Number(process.env.ADMIN_PORT || 8002);
const CUSTOMERS_FILE = path.join(HOME, ".nova-customers.json");
const USERS_FILE = path.join(HOME, ".nova-users.json");
const AUTH_FILE = path.join(HOME, ".local/share/opencode/auth.json");
const TENANTS_DIR = path.join(HOME, "nova-tenants");
const GATEWAY = "http://127.0.0.1:18766/health";
const VLLM = "http://127.0.0.1:8003/v1/models";
const LLM_HOST = "llm-api.isigpu.local";
const SYSTEM_UNITS = ["nova-ingress", "nova-gateway", "nova-automation", "nova-admin"];

const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const ADMIN_SECRET = process.env.ADMIN_SECRET || "";
let livePassword = ADMIN_PASSWORD;
if (!ADMIN_PASSWORD || !ADMIN_SECRET) {
  console.error("[admin] ADMIN_PASSWORD / ADMIN_SECRET missing");
  process.exit(1);
}

const loginFails = new Map();
const duCache = new Map();
const diskCache = { ts: 0, data: null };

function sign(v) {
  return crypto.createHmac("sha256", ADMIN_SECRET).update(v).digest("hex").slice(0, 32);
}
function cookieOk(req) {
  const m = /(?:^|;\s*)na_admin=(\d+)\.([a-f0-9]{32})/.exec(req.headers.cookie || "");
  if (!m) return false;
  const exp = Number(m[1]);
  return exp > Date.now() && sign(String(exp)) === m[2];
}
function json(res, code, obj, extra = {}) {
  const b = JSON.stringify(obj);
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra });
  res.end(b);
}
async function readBody(req, cap = 65536) {
  let size = 0;
  const chunks = [];
  for await (const c of req) {
    size += c.length;
    if (size > cap) throw new Error("body too large");
    chunks.push(c);
  }
  return Buffer.concat(chunks).toString("utf8") || "{}";
}
function sh(cmd, args, opts = {}) {
  return exec(cmd, args, { timeout: opts.timeout || 15000, ...opts }).then((r) => (r.stdout || "").trim());
}
function fetchHttp(url, opts = {}, ms = 4000) {
  return new Promise((resolve) => {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), ms);
    fetch(url, { ...opts, signal: ctl.signal })
      .then(async (r) => {
        const text = await r.text();
        clearTimeout(t);
        resolve({ status: r.status, text });
      })
      .catch(() => {
        clearTimeout(t);
        resolve({ status: 0, text: "" });
      });
  });
}
function fetchHttpsInsecure(hostPath, headers, ms = 5000) {
  return new Promise((resolve) => {
    const req = https.request(
      { hostname: LLM_HOST, path: hostPath, method: "GET", headers, rejectUnauthorized: false, timeout: ms },
      (r) => {
        let b = "";
        r.on("data", (c) => (b += c));
        r.on("end", () => resolve({ status: r.statusCode, text: b }));
      }
    );
    req.on("timeout", () => req.destroy());
    req.on("error", () => resolve({ status: 0, text: "" }));
    req.end();
  });
}
async function loadCustomers() {
  try {
    return JSON.parse(await readFile(CUSTOMERS_FILE, "utf8")).customers || [];
  } catch {
    return [];
  }
}
async function saveCustomers(list) {
  const bak = CUSTOMERS_FILE + ".bak-" + Date.now();
  await rename(CUSTOMERS_FILE, bak).catch(() => {});
  await writeFile(CUSTOMERS_FILE, JSON.stringify({ customers: list }, null, 2) + "\n");
  await sh("chown", ["moazemi-gc:moazemi-gc", CUSTOMERS_FILE], { timeout: 10000 }).catch(() => {});
  await sh("chmod", ["644", CUSTOMERS_FILE], { timeout: 10000 }).catch(() => {});
}
function envPath(name) {
  return name === "default" ? path.join(HOME, ".nova-env") : path.join(TENANTS_DIR, name + ".env");
}
async function parseEnv(name) {
  try {
    const raw = await readFile(envPath(name), "utf8");
    const out = {};
    for (const line of raw.split("\n")) {
      const m = /^([A-Z_][A-Z0-9_]*)=(.*)$/.exec(line.trim());
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
    return out;
  } catch {
    return {};
  }
}
function unitFor(name) {
  return name === "default" ? "nova-agent-server" : "nova-agent-server@" + name;
}
async function loadPortalUsers() {
  try {
    return JSON.parse(await readFile(USERS_FILE, "utf8")).users || [];
  } catch {
    return [];
  }
}
async function upsertPortalUser(username, customer, password) {
  const users = await loadPortalUsers();
  const salt = crypto.randomBytes(12).toString("hex");
  const hash = crypto.createHash("sha256").update(salt + ":" + password).digest("hex");
  const idx = users.findIndex((u) => u.username === username);
  const entry = { username, customer, salt, hash };
  if (idx >= 0) users[idx] = entry;
  else users.push(entry);
  const bak = USERS_FILE + ".bak-" + Date.now();
  await rename(USERS_FILE, bak).catch(() => {});
  await writeFile(USERS_FILE, JSON.stringify({ users }, null, 2) + "\n", { mode: 0o600 });
  await exec("chown", ["moazemi-gc:moazemi-gc", USERS_FILE]).catch(() => {});
  return entry;
}
async function serviceState(unit) {
  return sh("systemctl", ["is-active", unit]).catch(() => "unknown");
}
async function serviceUptime(unit) {
  try {
    const ts = await sh("systemctl", ["show", unit, "-p", "ActiveEnterTimestampMonotonic", "--value"], { timeout: 8000 });
    const us = Number(ts);
    if (!us) return null;
    const up = parseFloat((readFileSync("/proc/uptime", "utf8").split(" ")[0]));
    return Math.max(0, Math.round(up - us / 1e6));
  } catch {
    return null;
  }
}
async function gatewayHealth() {
  const r = await fetchHttp(GATEWAY, {}, 2000);
  try {
    return JSON.parse(r.text);
  } catch {
    return { customers: [] };
  }
}
function humanSize(bytes) {
  if (bytes == null) return "?";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = Number(bytes);
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return n.toFixed(n >= 100 || i === 0 ? 0 : 1) + " " + u[i];
}
function fmtUptime(s) {
  if (s == null) return "-";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return (d ? d + " روز و " : "") + h + " ساعت و " + m + " دقیقه";
}
function cleanPw(s) {
  return String(s || "")
    .replace(/[\u200B-\u200F\u202A-\u202E\u2066-\u2069\uFEFF]/g, "")
    .replace(/[\u06F0-\u06F9\u0660-\u0669]/g, (c) => String(c.charCodeAt(0) & 0x0f))
    .trim();
}

const FAVICON =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%23d97757'/%3E%3Cstop offset='1' stop-color='%238b7cf6'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='64' height='64' rx='14' fill='%230d0d0f'/%3E%3Cpath d='M19 46V18l26 28V18' stroke='url(%23g)' stroke-width='7' stroke-linecap='round' stroke-linejoin='round' fill='none'/%3E%3C/svg%3E";

const PAGE = `<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="__FAVICON__">
<title>NovaAgent | پنل مدیریت</title>
<style>
:root{--bg:#0d0d0f;--card:#161619;--card2:#1d1d21;--line:#2a2a30;--tx:#ececf1;--tx2:#9a9aa5;--acc:#d97757;--acc2:#8b7cf6;--ok:#3fb950;--bad:#f85149;--warn:#d29922}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:Vazirmatn,"Segoe UI",Tahoma,sans-serif;min-height:100vh}
.login{display:flex;align-items:center;justify-content:center;min-height:100vh}
.lcard{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:40px;width:380px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.lcard h1{font-size:22px;margin-bottom:6px}
.lcard h1 span{background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.lcard p{color:var(--tx2);font-size:13px;margin-bottom:22px}
input,select{background:var(--card2);border:1px solid var(--line);color:var(--tx);border-radius:10px;padding:10px 14px;font-size:14px;width:100%;outline:none;font-family:inherit}
input:focus,select:focus{border-color:var(--acc)}
button{background:var(--acc);border:none;color:#fff;border-radius:10px;padding:10px 18px;font-size:14px;cursor:pointer;font-family:inherit;font-weight:600}
button:hover{filter:brightness(1.1)}
button.ghost{background:var(--card2);border:1px solid var(--line);color:var(--tx);font-weight:400}
button.danger{background:#3d1d1f;color:#ff8585;border:1px solid #5c2a2e;font-weight:400}
button.mini{padding:4px 10px;font-size:11px;border-radius:7px}
.top{display:flex;align-items:center;gap:14px;padding:14px 28px;border-bottom:1px solid var(--line);background:rgba(13,13,15,.85);position:sticky;top:0;backdrop-filter:blur(8px);z-index:5}
.brand{font-weight:800;font-size:17px;display:flex;align-items:center;gap:9px}
.brand .logo{width:26px;height:26px;border-radius:7px;background:#0d0d0f;display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--line)}
.brand span{background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.tabs{display:flex;gap:6px;margin-inline-start:14px;flex:1;flex-wrap:wrap}
.tab{padding:8px 16px;border-radius:10px;color:var(--tx2);cursor:pointer;font-size:14px;user-select:none}
.tab.on{background:var(--card2);color:var(--tx);font-weight:600}
.wrap{max-width:1200px;margin:0 auto;padding:24px 28px 60px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:14px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.card h3{font-size:12px;color:var(--tx2);font-weight:400;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
.card .big{font-size:22px;font-weight:800}
.chip{display:inline-flex;align-items:center;gap:7px;background:var(--card2);border:1px solid var(--line);border-radius:999px;padding:6px 13px;font-size:13px;margin:3px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--bad);flex:none}
.dot.ok{background:var(--ok);box-shadow:0 0 8px rgba(63,185,80,.6)}
.dot.warn{background:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{color:var(--tx2);font-weight:400;text-align:start;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
table th:not(:first-child),table td:not(:first-child){text-align:center}
tr:hover td{background:rgba(255,255,255,.02)}
.mut{color:var(--tx2);font-size:12px}
.bar{height:6px;background:var(--card2);border-radius:4px;overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),var(--acc2))}
.rowflex{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.rowflex select{width:auto}
.actions{display:flex;gap:6px;flex-wrap:wrap}
pre{background:#0a0a0c;border:1px solid var(--line);border-radius:12px;padding:16px;font-size:12px;direction:ltr;text-align:left;overflow:auto;max-height:60vh;line-height:1.7;color:#c9d1d9}
.toast{position:fixed;bottom:24px;inset-inline-start:50%;transform:translateX(50%);background:var(--card2);border:1px solid var(--line);padding:12px 22px;border-radius:12px;font-size:14px;box-shadow:0 8px 30px rgba(0,0,0,.5);opacity:0;transition:.25s;pointer-events:none;z-index:9;max-width:80vw}
.toast.on{opacity:1}
.frm{display:none;gap:10px;align-items:center;background:var(--card);border:1px dashed var(--line);border-radius:14px;padding:16px;margin-bottom:16px;flex-wrap:wrap}
.frm.on{display:flex}
.frm input{width:220px}
.badge{font-size:11px;padding:3px 9px;border-radius:6px;background:var(--card2);border:1px solid var(--line);color:var(--tx2)}
h2.sec{font-size:16px;margin:26px 0 14px}
.empty{color:var(--tx2);text-align:center;padding:34px;font-size:14px}
canvas.spark{width:100%;height:38px;display:block;margin-top:6px}
.svcrow{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--line);font-size:13px;flex-wrap:wrap}
.svcrow:last-child{border-bottom:none}
.svcrow .name{direction:ltr;flex:1;min-width:150px}
label.tog{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--tx2);cursor:pointer;user-select:none}
label.tog input{width:auto}
.table-card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;letter-spacing:0}
.table-tools{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.table-search{display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--tx2)}
.table-search input{width:min(260px,100%);border-radius:10px;letter-spacing:0}
.table-meta{color:var(--tx2);font-size:12px}
.table-wrap{overflow-x:auto}
th.sortable{cursor:pointer;user-select:none}
th.sortable:focus-visible{outline:2px solid var(--acc);outline-offset:-2px}
th .dir{display:inline-block;min-width:14px;color:var(--acc)}
.table-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 14px;border-top:1px solid var(--line);color:var(--tx2);font-size:12px;flex-wrap:wrap}
.table-pager{display:flex;align-items:center;gap:8px}
.table-footer button:disabled{opacity:.45;cursor:not-allowed}
.log-cell{direction:ltr;text-align:left !important;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
</style>
</head>
<body>
<div id="app"></div>
<div class="toast" id="toast"></div>
<script>
var S={tab:"dash",tenant:"",unit:"nova-ingress",lines:100,auto:true,hist:[],histLoad:[],tables:{},tableData:{}};
var BASE=location.pathname.indexOf("/admin")===0?"/admin":"";
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function toast(m){var t=document.getElementById("toast");t.textContent=m;t.classList.add("on");setTimeout(function(){t.classList.remove("on")},3000)}
function api(p,o){var opts={headers:{"Content-Type":"application/json"}};if(o){opts.method="POST";opts.body=JSON.stringify(o)}return fetch(BASE+p,opts).then(function(r){if(r.status===401){renderLogin();throw new Error("auth")}return r.json()})}
function post(p,o){return fetch(BASE+p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(o||{})}).then(function(r){return r.json().then(function(j){return{j:j,s:r.status}})})}
function fmtB(b){if(!b&&b!==0)return"?";var u=["B","KB","MB","GB","TB"],i=0,n=Number(b);while(n>=1024&&i<4){n/=1024;i++}return n.toFixed(n>=100||i===0?0:1)+" "+u[i]}
function fmtT(ts){if(!ts)return"-";try{return new Date(ts).toLocaleString("fa-IR")}catch(e){return ts}}
function fmtUp(s){if(s==null)return"-";var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return (d?d+"روز ":"")+h+"ساعت "+m+"دقیقه"}
function dot(ok,warn){return '<span class="dot'+(ok?" ok":(warn?" warn":""))+'"></span>'}
function spark(cv,vals,color){
  if(!cv||!vals.length)return;
  var w=cv.width=cv.offsetWidth*2,h=cv.height=76;
  var x=cv.getContext("2d");x.clearRect(0,0,w,h);
  var max=Math.max.apply(null,vals.concat([1])),min=0;
  if(max<100)max=Math.max(max,1);
  x.beginPath();
  for(var i=0;i<vals.length;i++){
    var px=i/(Math.max(vals.length-1,1))*w, py=h-6-(vals[i]-min)/(max-min||1)*(h-14);
    if(i===0)x.moveTo(px,py);else x.lineTo(px,py);
  }
  x.strokeStyle=color;x.lineWidth=3;x.lineJoin="round";x.stroke();
  x.lineTo(w,h);x.lineTo(0,h);x.closePath();
  x.fillStyle=color+"22";x.fill();
}

function uiFa(){
  return String(navigator.language || "fa").toLowerCase().indexOf("fa") === 0;
}
function L(en,fa){
  return uiFa() ? fa : en;
}
function faDigits(v){
  return String(v).replace(/[0-9]/g,function(d){return "۰۱۲۳۴۵۶۷۸۹"[d]});
}
function localeDigits(v){
  return uiFa() ? faDigits(v) : String(v);
}
function applyUiLanguage(){
  var fa=uiFa();
  document.documentElement.lang=fa ? "fa" : "en";
  document.documentElement.dir=fa ? "rtl" : "ltr";
}
function tableState(id,columns){
  var st=S.tables[id];
  if(!st)st=S.tables[id]={query:"",page:1,sortKey:columns[0].key,dir:"asc"};
  if(!columns.some(function(c){return c.key===st.sortKey})){st.sortKey=columns[0].key;st.dir="asc"}
  return st;
}
function tableValue(col,row){
  return col.value ? col.value(row) : row[col.key];
}
function tableText(col,row){
  return String(col.text ? col.text(row) : tableValue(col,row) ?? "").trim();
}
function tableHtml(col,row){
  return col.cell ? col.cell(row) : esc(tableValue(col,row));
}
function tableSortValue(col,row){
  return col.sort ? col.sort(row) : tableText(col,row);
}
function tableCompare(a,b){
  var an=Number(a),bn=Number(b);
  if(a!==""&&b!==""&&isFinite(an)&&isFinite(bn))return an-bn;
  return String(a).localeCompare(String(b),uiFa()?"fa":"en",{numeric:true});
}
function tablePrepare(id){
  var cfg=S.tableData[id];
  if(!cfg)return null;
  var columns=cfg.columns,rows=cfg.rows||[];
  var st=tableState(id,columns);
  var models=rows.map(function(row){
    return {row:row,text:columns.map(function(c){return tableText(c,row)}),sort:columns.map(function(c){return tableSortValue(c,row)})};
  });
  var q=st.query.trim().toLowerCase();
  if(q)models=models.filter(function(m){return m.text.some(function(t){return t.toLowerCase().indexOf(q)>=0})});
  var ci=columns.findIndex(function(c){return c.key===st.sortKey});
  if(ci>=0)models.sort(function(a,b){return tableCompare(a.sort[ci],b.sort[ci])*(st.dir==="desc"?-1:1)});
  var pages=Math.max(1,Math.ceil(models.length/20));
  if(st.page>pages)st.page=pages;
  if(st.page<1)st.page=1;
  return {columns:columns,empty:cfg.empty,all:models.length,filtered:models.length,
          rows:models.slice((st.page-1)*20,st.page*20),page:st.page,pages:pages,state:st};
}
function tableHead(id,cfg){
  var st=tableState(id,cfg.columns);
  return cfg.columns.map(function(col){
    var arrow=st.sortKey===col.key?(st.dir==="asc"?"▲":"▼"):"";
    var sort=st.sortKey===col.key?st.dir:"none";
    return '<th class="sortable" data-table-sort="'+esc(id)+'" data-key="'+esc(col.key)+'" tabindex="0" role="button" aria-sort="'+sort+'">'+
      esc(col.label)+' <span class="dir">'+arrow+"</span></th>";
  }).join("");
}
function tableBody(id){
  var m=tablePrepare(id);
  if(!m)return "";
  if(!m.rows.length)return '<tr><td colspan="'+m.columns.length+'" class="empty">'+esc(m.empty||L("No rows","ردیفی نیست"))+"</td></tr>";
  return m.rows.map(function(item){
    return "<tr>"+m.columns.map(function(col){return "<td>"+tableHtml(col,item.row)+"</td>"}).join("")+"</tr>";
  }).join("");
}
function dataTable(id,cfg){
  S.tableData[id]=cfg;
  var st=tableState(id,cfg.columns),m=tablePrepare(id);
  return '<div class="table-card" data-table-id="'+esc(id)+'">'+
    '<div class="table-tools"><label class="table-search"><span>'+L("Search","جستجو")+"</span>"+
    '<input type="search" data-table-filter="'+esc(id)+'" value="'+esc(st.query)+'" placeholder="'+esc(L("Search…","جستجو…"))+'" aria-label="'+esc(L("Search table","جستجوی جدول"))+'"></label>'+
    '<div class="table-meta" data-table-meta="'+esc(id)+'">'+localeDigits(m.filtered)+" "+L("rows","ردیف")+"</div></div>"+
    '<div class="table-wrap"><table><thead><tr>'+tableHead(id,cfg)+"</tr></thead><tbody data-table-body=\""+esc(id)+'">'+tableBody(id)+"</tbody></table></div>"+
    '<div class="table-footer"><span>'+L("Page size: 20","اندازه صفحه: ۲۰")+"</span>"+
    '<div class="table-pager"><button class="mini ghost" data-table-page="prev" data-table-id="'+esc(id)+'"'+(m.page<=1?" disabled":"")+">"+L("Previous","قبلی")+"</button>"+
    '<span data-table-page-label="'+esc(id)+'">'+L("Page","صفحه")+" "+localeDigits(m.page)+" "+L("of","از")+" "+localeDigits(m.pages)+"</span>"+
    '<button class="mini ghost" data-table-page="next" data-table-id="'+esc(id)+'"'+(m.page>=m.pages?" disabled":"")+">"+L("Next","بعدی")+"</button></div></div></div>";
}
function updateTable(id){
  var card=document.querySelector('[data-table-id="'+id+'"]'),m=tablePrepare(id);
  if(!card||!m)return;
  card.querySelector("table thead").innerHTML="<tr>"+tableHead(id,S.tableData[id])+"</tr>";
  card.querySelector('[data-table-body="'+id+'"]').innerHTML=tableBody(id);
  card.querySelector('[data-table-meta="'+id+'"]').textContent=localeDigits(m.filtered)+" "+L("rows","ردیف");
  card.querySelector('[data-table-page-label="'+id+'"]').textContent=L("Page","صفحه")+" "+localeDigits(m.page)+" "+L("of","از")+" "+localeDigits(m.pages);
  card.querySelector('[data-table-page="prev"]').disabled=m.page<=1;
  card.querySelector('[data-table-page="next"]').disabled=m.page>=m.pages;
}
function tableSort(id,key){
  var st=tableState(id,S.tableData[id].columns);
  if(st.sortKey===key)st.dir=st.dir==="asc"?"desc":"asc";
  else{st.sortKey=key;st.dir="asc"}
  st.page=1;
  updateTable(id);
}

function renderLogin(msg){
  document.getElementById("app").innerHTML='<div class="login"><div class="lcard">'+
  '<h1><span>NovaAgent</span></h1><p>پنل مدیریت و مانیتورینگ سرور</p>'+
  (msg?'<p style="color:var(--bad)">'+esc(msg)+"</p>":"")+
  '<input id="pw" type="password" placeholder="رمز مدیریت" autofocus onkeydown="if(event.key===\'Enter\')doLogin()">'+
  '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:10px"><label class="tog"><input type="checkbox" id="showpw" onchange="document.getElementById(\'pw\').type=this.checked?\'text\':\'password\'"> نمایش رمز</label></div>'+
  '<div style="height:10px"></div><button style="width:100%" onclick="doLogin()">ورود</button>'+
  '<p style="margin-top:14px;font-size:11px;color:var(--tx2)">پنل مدیریت · v4</p></div></div>';
}
function doLogin(){
  var el=document.getElementById("pw");
  var pw=el.value.replace(/[\\u200B-\\u200F\\u202A-\\u202E\\u2066-\\u2069\\uFEFF]/g,"").replace(/[\\u06F0-\\u06F9\\u0660-\\u0669]/g,function(c){return String(c.charCodeAt(0)&15)}).trim();
  el.value=pw;
  post("/api/login",{password:pw}).then(function(r){
    if(r.j.ok){S.hist=[];boot()}else{renderLogin(r.j.error||"رمز اشتباه است")}
  }).catch(function(){renderLogin("خطا در اتصال به سرور")});
}
function logout(){fetch(BASE+"/api/logout").then(function(){renderLogin()})}
function changePw(){
  var cur=prompt("رمز فعلی:");
  if(cur==null)return;
  var nx=prompt("رمز جدید (حداقل ۸ کاراکتر):");
  if(nx==null)return;
  if(nx.length<8){toast("رمز جدید کوتاه است");return}
  post("/api/password",{current:cur,next:nx}).then(function(r){
    toast(r.j.ok?"رمز عوض شد ✓ — دفعه بعد با رمز جدید وارد شوید":("خطا: "+(r.j.error||"")));
  });
}

function probe(){
  applyUiLanguage();
  fetch(BASE+"/api/overview").then(function(r){
    if(r.ok){boot()}else{renderLogin()}
  }).catch(function(){renderLogin()});
}
function boot(){
  shell();refresh();
  if(S.timer)clearInterval(S.timer);
  S.timer=setInterval(function(){if(S.auto&&S.tab==="dash")refresh()},10000);
}
function setTab(t){S.tab=t;document.querySelectorAll(".tab").forEach(function(el){el.classList.toggle("on",el.dataset.t===t)});refresh()}
function refresh(){
  if(S.tab==="dash")loadDash();
  else if(S.tab==="tenants")loadTenants();
  else if(S.tab==="convs")loadConvs();
  else if(S.tab==="logs")loadLogs();
}
function shell(){
  document.getElementById("app").innerHTML=
  '<div class="top"><div class="brand"><span class="logo"><svg width="16" height="16" viewBox="0 0 64 64"><path d="M19 46V18l26 28V18" stroke="#d97757" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg></span><span>NovaAgent</span>&nbsp;· پنل مدیریت</div>'+
  '<div class="tabs">'+
  '<div class="tab" data-t="dash" onclick="setTab(\'dash\')">داشبورد</div>'+
  '<div class="tab" data-t="tenants" onclick="setTab(\'tenants\')">کاربران</div>'+
  '<div class="tab" data-t="convs" onclick="setTab(\'convs\')">گفتگوها</div>'+
  '<div class="tab" data-t="logs" onclick="setTab(\'logs\')">لاگ‌ها</div>'+
  "</div>"+
  '<button class="ghost mini" onclick="changePw()">تغییر رمز</button>'+
  '<button class="ghost mini" onclick="logout()">خروج</button></div><div class="wrap" id="view"></div>';
  document.querySelectorAll(".tab").forEach(function(el){el.classList.toggle("on",el.dataset.t===S.tab)});
}

function loadDash(){
  api("/api/overview").then(function(d){
    var memPct=Math.round((d.mem.total-d.mem.free)/d.mem.total*100);
    var diskPct=d.disk.total?Math.round(d.disk.used/d.disk.total*100):0;
    S.hist.push(memPct);if(S.hist.length>40)S.hist.shift();
    S.histLoad.push(d.load[0]);if(S.histLoad.length>40)S.histLoad.shift();
    var gw=d.gateway.customers.map(function(c){
      return '<span class="chip">'+dot(c.connected)+esc(c.name)+"</span>";
    }).join(" ");
    var svcTable=dataTable("services",{rows:d.services,empty:L("No services","سرویسی نیست"),columns:[
      {key:"unit",label:L("Service","سرویس"),value:function(s){return s.unit},cell:function(s){return "<b>"+esc(s.unit)+"</b>"}},
      {key:"active",label:L("Status","وضعیت"),text:function(s){return s.active},cell:function(s){return dot(s.active==="active")+esc(s.active)}},
      {key:"upSec",label:L("Uptime","آپ‌تایم"),text:function(s){return s.active==="active"?fmtUp(s.upSec):"-"},sort:function(s){return s.upSec||0}},
      {key:"actions",label:L("Action","عملیات"),text:function(s){return s.unit},sort:function(s){return s.unit},cell:function(s){
        return s.unit==="nova-admin"?'<span class="mut">-</span>':'<button class="mini ghost" onclick="sAction(\''+esc(s.unit)+'\',\'restart\')">'+L("Restart","ری‌استارت")+"</button>";
      }}
    ]});
    document.getElementById("view").innerHTML=
    '<div class="grid">'+
    '<div class="card"><h3>آپ‌تایم سرور</h3><div class="big">'+esc(d.uptimeText)+"</div></div>"+
    '<div class="card"><h3>حافظه RAM <span class="mut">'+memPct+"%</span></h3><div class=\"mut\">"+fmtB(d.mem.total-d.mem.free)+" از "+fmtB(d.mem.total)+'</div><canvas class="spark" id="spRam"></canvas></div>'+
    '<div class="card"><h3>دیسک <span class="mut">'+diskPct+"%</span></h3><div class=\"mut\">"+fmtB(d.disk.used)+" از "+fmtB(d.disk.total)+'</div><div class="bar"><i style="width:'+diskPct+'%"></i></div><div style="margin-top:10px"><button class="mini ghost" onclick="loadDisk()">جزئیات پوشه‌ها</button></div><div id="diskbox"></div></div>'+
    '<div class="card"><h3>بار CPU</h3><div class="big">'+d.load[0].toFixed(2)+"</div><canvas class=\"spark\" id=\"spLoad\"></canvas></div>"+
    "</div>"+
    '<div class="card" style="margin-bottom:22px"><h3>سرویس‌ها <span class="mut">به‌روزرسانی خودکار <label class="tog"><input type="checkbox" '+(S.auto?"checked":"")+' onchange="S.auto=this.checked"> فعال</label></span></h3>'+svcTable+"</div>"+
    '<h2 class="sec">کلاینت‌های متصل (Gateway)</h2><div style="margin-bottom:8px">'+(gw||'<span class="mut">اطلاعاتی نیست</span>')+"</div>"+
    '<div id="llmbox"><span class="mut">در حال دریافت وضعیت هوش مصنوعی…</span></div>';
    var r1=document.getElementById("spRam");if(r1)spark(r1,S.hist,"#d97757");
    var r2=document.getElementById("spLoad");if(r2)spark(r2,S.histLoad,"#8b7cf6");
    loadLlm();
  }).catch(function(e){if(e.message!=="auth")toast("خطا در دریافت وضعیت")});
}
function sAction(unit,action){
  if(action==="restart"&&!confirm("ری‌استارت "+unit+"؟"))return;
  if(unit==="nova-admin"){toast("پنل لحظه‌ای قطع و وصل می‌شود…")}
  post("/api/services/"+unit+"/action",{action:action}).then(function(r){
    toast(r.j.ok?"انجام شد ✓":("خطا: "+(r.j.error||r.s)));
    setTimeout(loadDash,1500);
  });
}
function loadDisk(){
  document.getElementById("diskbox").innerHTML='<span class="mut">محاسبه…</span>';
  api("/api/disk-usage").then(function(d){
    document.getElementById("diskbox").innerHTML=dataTable("disk",{rows:d.items,empty:L("No disk usage data","اطلاعات مصرف دیسک نیست"),columns:[
      {key:"dir",label:L("Path","مسیر"),value:function(it){return it.dir},cell:function(it){return '<span class="mut" style="direction:ltr">'+esc(it.dir)+"</span>"}},
      {key:"sizeText",label:L("Size","حجم"),text:function(it){return it.sizeText},sort:function(it){return it.bytes||0}}
    ]});
  });
}
function loadLlm(){
  api("/api/llm").then(function(d){
    var h='<h2 class="sec">هوش مصنوعی</h2><div class="grid">'+
    '<div class="card"><h3>vLLM محلی</h3>'+(d.local.ok?'<span class="chip">'+dot(true)+"فعال · "+d.local.ms+'ms</span>':'<span class="chip">'+dot(false)+'قطع</span>')+'<div class="mut" style="margin-top:8px">'+(d.local.models.map(esc).join(" · ")||"-")+"</div></div>"+
    '<div class="card"><h3>Gateway شرکتی</h3>'+(d.remote.ok?'<span class="chip">'+dot(true)+"فعال · "+d.remote.ms+'ms</span>':'<span class="chip">'+dot(false)+"قطع"+(d.remote.status?" ("+d.remote.status+")":"")+'</span>')+'<div class="mut" style="margin-top:8px">'+(d.remote.models.slice(0,6).map(esc).join(" · ")||"-")+"</div></div></div>";
    var llmRows=[];
    ["local","remote"].forEach(function(provider){
      d[provider].models.forEach(function(model){
        llmRows.push({model:model,provider:provider,ok:d[provider].ok,ms:d[provider].ms});
      });
    });
    h+=dataTable("llm",{rows:llmRows,empty:L("No models available","مدلی در دسترس نیست"),columns:[
      {key:"model",label:L("Model","مدل"),value:function(r){return r.model},cell:function(r){return "<b>"+esc(r.model)+"</b>"}},
      {key:"provider",label:L("Provider","ارائه‌دهنده"),text:function(r){return r.provider==="local"?"vLLM":"Gateway"},value:function(r){return r.provider==="local"?"vLLM":"Gateway"}},
      {key:"ok",label:L("Health","سلامت"),text:function(r){return r.ok?"ok":"down"},sort:function(r){return r.ok?1:0},cell:function(r){return dot(r.ok)+(r.ok?L("Active","فعال"):L("Down","قطع"))}},
      {key:"ms",label:L("Latency","تأخیر"),text:function(r){return r.ms+"ms"},sort:function(r){return r.ms||0}}
    ]});
    var el=document.getElementById("llmbox");if(el)el.innerHTML=h;
  }).catch(function(){});
}

function loadTenants(){
  api("/api/tenants").then(function(d){
    var tenantsTable=dataTable("tenants",{rows:d.tenants,empty:L("No users","کاربری ثبت نشده"),columns:[
      {key:"name",label:L("Name","نام"),value:function(t){return t.name},cell:function(t){
        return "<b>"+esc(t.name)+"</b>"+(t.name==="default"?' <span class="badge">اصلی</span>':"")+(t.hasPortalUser?' <span class="badge">پورتال</span>':"");
      }},
      {key:"active",label:L("Service","سرویس"),text:function(t){return t.active},sort:function(t){return t.active},cell:function(t){
        return dot(t.active==="active")+(t.active==="active"?L("Active","فعال"):L("Stopped","متوقف"));
      }},
      {key:"clientConnected",label:L("Client","کلاینت"),text:function(t){return t.clientConnected?"connected":"disconnected"},sort:function(t){return t.clientConnected?1:0},cell:function(t){
        return dot(t.clientConnected)+(t.clientConnected?L("Connected","متصل"):L("Disconnected","قطع"));
      }},
      {key:"agentOk",label:"Agent",text:function(t){return t.agentOk?"healthy":"unresponsive"},sort:function(t){return t.agentOk?1:0},cell:function(t){
        return dot(t.agentOk)+(t.agentOk?L("Healthy","سالم"):L("Unresponsive","بدون پاسخ"));
      }},
      {key:"port",label:L("Port","پورت"),value:function(t){return t.port},cell:function(t){return '<span class="mut" style="direction:ltr">'+esc(t.port)+"</span>"}},
      {key:"convCount",label:L("Conversations","گفتگوها"),text:function(t){return t.convCount==null?"-":String(t.convCount)},sort:function(t){return t.convCount==null?-1:t.convCount}},
      {key:"sizeText",label:L("Size","حجم"),text:function(t){return t.sizeText},sort:function(t){return t.size||0}},
      {key:"actions",label:L("Action","عملیات"),text:function(t){return t.name},sort:function(t){return t.name},cell:function(t){
        var act=t.active==="active";
        var btns='<div class="actions">'+
        (act?'<button class="mini ghost" onclick="tAction(\''+esc(t.name)+'\',\'restart\')">'+L("Restart","ری‌استارت")+'</button><button class="mini ghost" onclick="tAction(\''+esc(t.name)+'\',\'stop\')">'+L("Stop","توقف")+"</button>"
            :'<button class="mini ghost" onclick="tAction(\''+esc(t.name)+'\',\'start\')">'+L("Start","راه‌اندازی")+"</button>")+
        '<button class="mini ghost" onclick="tPw(\''+esc(t.name)+'\')">'+(t.hasPortalUser?'تغییر رمز':'رمز')+"</button>";
        if(t.name!=="default")btns+='<button class="mini danger" onclick="tDel(\''+esc(t.name)+'\')">'+L("Delete","حذف")+"</button>";
        return btns+"</div>";
      }}
    ]});
    document.getElementById("view").innerHTML=
    '<div class="rowflex"><h2 class="sec" style="margin:0">کاربران (Tenants)</h2><button onclick="document.getElementById(\'ntfrm\').classList.toggle(\'on\')">+ کاربر جدید</button><span class="mut">رمز، همان لاگین پورتال NovaAgent است</span></div>'+
    '<div class="frm" id="ntfrm"><input id="ntname" placeholder="نام کاربری مثلاً node3" onkeydown="if(event.key===\'Enter\')document.getElementById(\'ntpw\').focus()"><input id="ntpw" type="password" placeholder="رمز پورتال" onkeydown="if(event.key===\'Enter\')tCreate()"><button onclick="tCreate()">ایجاد</button><span class="mut">پورت و کلیدها خودکار ساخته می‌شوند</span></div>'+
    tenantsTable;
  });
}
function tPw(n){
  var p=prompt("رمز پورتال برای "+n+" (حداقل ۴ کاراکتر):");
  if(p==null)return;
  if(p.length<4){toast("رمز کوتاه است");return}
  post("/api/tenants/"+n+"/password",{password:p}).then(function(r){
    toast(r.j.ok?"رمز "+r.j.username+" ثبت شد ✓":("خطا: "+(r.j.error||r.s)));loadTenants();
  });
}
function tAction(n,a){
  if(a==="restart"&&!confirm("ری‌استارت سرویس "+n+"؟"))return;
  post("/api/tenants/"+n+"/action",{action:a}).then(function(r){
    toast(r.j.ok?"انجام شد ✓":("خطا: "+(r.j.error||r.s)));loadTenants();
  });
}
function tCreate(){
  var n=document.getElementById("ntname").value.trim();
  var pw=document.getElementById("ntpw").value;
  if(!n)return;
  if(pw&&pw.length<4){toast("رمز حداقل ۴ کاراکتر");return}
  post("/api/tenants",{name:n,password:pw}).then(function(r){
    if(r.j.ok){toast("کاربر "+r.j.name+" ساخته شد ✓ (پورت "+r.j.port+(r.j.portalUser?" · لاگین پورتال فعال":"")+")");document.getElementById("ntfrm").classList.remove("on")}
    else toast("خطا: "+(r.j.error||""));
    loadTenants();
  });
}
function tDel(n){
  if(!confirm("کاربر "+n+" حذف شود؟ سرویس متوقف و از رجیستری حذف می‌شود؛ فایل‌ها حفظ می‌شوند."))return;
  fetch(BASE+"/api/tenants/"+n,{method:"DELETE"}).then(function(r){return r.json()}).then(function(j){
    toast(j.ok?"حذف شد ✓":("خطا: "+(j.error||"")));loadTenants();
  });
}

function loadConvs(){
  api("/api/tenants").then(function(td){
    var opts=td.tenants.map(function(t){return '<option value="'+esc(t.name)+'"'+(S.tenant===t.name?" selected":"")+">"+esc(t.name)+"</option>"}).join("");
    var sel=S.tenant||((td.tenants[0]||{}).name||"");
    if(sel&&!S.tenant)S.tenant=sel;
    document.getElementById("view").innerHTML=
    '<div class="rowflex"><h2 class="sec" style="margin:0">گفتگوها</h2><select onchange="S.tenant=this.value;loadConvs()">'+opts+"</select>"+
    '<span class="mut">۵۰ گفتگوی اخیر</span></div><div id="cvbox"><span class="mut">در حال دریافت…</span></div>';
    if(!S.tenant)return;
    api("/api/conversations?tenant="+encodeURIComponent(S.tenant)).then(function(d){
      var convTable=dataTable("convs",{rows:d.conversations,empty:L("No conversations","گفتگویی نیست"),columns:[
        {key:"title",label:L("Title","عنوان"),value:function(c){return c.title},cell:function(c){return "<b>"+esc(c.title)+"</b>"}},
        {key:"id",label:L("ID","شناسه"),value:function(c){return c.id},cell:function(c){return '<span class="mut" style="direction:ltr">'+esc(c.id)+"</span>"}},
        {key:"created_at",label:L("Created","ساخت"),text:function(c){return fmtT(c.created_at)},sort:function(c){return c.created_at||0}},
        {key:"updated_at",label:L("Last activity","آخرین فعالیت"),text:function(c){return fmtT(c.updated_at)},sort:function(c){return c.updated_at||0}},
        {key:"status",label:L("Status","وضعیت"),value:function(c){return c.status||"-"}}
      ]});
      document.getElementById("cvbox").innerHTML=convTable;
    }).catch(function(){document.getElementById("cvbox").innerHTML='<div class="empty">agent-server در دسترس نیست</div>'});
  });
}
function loadLogs(){
  api("/api/overview").then(function(d){
    var units=d.services.map(function(s){return s.unit});
    var opts=units.map(function(u){return '<option value="'+u+'"'+(S.unit===u?" selected":"")+">"+u+"</option>"}).join("");
    document.getElementById("view").innerHTML=
    '<div class="rowflex"><h2 class="sec" style="margin:0">لاگ سرویس‌ها</h2><select onchange="S.unit=this.value;loadLogs()">'+opts+"</select>"+
    '<select onchange="S.lines=Number(this.value);loadLogs()"><option'+(S.lines===50?" selected":"")+'>50</option><option'+(S.lines===100?" selected":"")+'>100</option><option'+(S.lines===300?" selected":"")+'>300</option><option'+(S.lines===500?" selected":"")+'>500</option></select>'+
    '<button class="ghost" onclick="loadLogs()">به‌روزرسانی</button>'+
    '<button class="ghost" onclick="dlLogs()">دانلود کامل</button></div>'+
    '<div id="logbox" class="mut">در حال دریافت…</div>';
    api("/api/logs?unit="+encodeURIComponent(S.unit)+"&lines="+S.lines).then(function(d){
      var rows=(d.lines||"").split("\\n").map(function(line,i){return {line:i+1,text:line}});
      var logTable=dataTable("logs",{rows:rows,empty:L("No log lines","لاگی نیست"),columns:[
        {key:"text",label:L("Log","لاگ"),value:function(r){return r.text},cell:function(r){return '<span class="log-cell">'+esc(r.text)+"</span>"}},
        {key:"line",label:L("Line","خط"),value:function(r){return r.line}}
      ]});
      var el=document.getElementById("logbox");if(el)el.innerHTML=logTable;
    }).catch(function(){});
  });
}
function dlLogs(){
  var u=encodeURIComponent(S.unit);
  window.open(BASE+"/api/logs?unit="+u+"&lines=5000&download=1","_blank");
}

document.addEventListener("input",function(e){
  var el=e.target.closest ? e.target.closest("[data-table-filter]") : null;
  if(!el)return;
  var id=el.getAttribute("data-table-filter"),st=S.tables[id];
  if(!st)return;
  st.query=el.value;st.page=1;updateTable(id);
});
document.addEventListener("click",function(e){
  if(!e.target.closest)return;
  var sort=e.target.closest("[data-table-sort]");
  if(sort){tableSort(sort.getAttribute("data-table-sort"),sort.getAttribute("data-key"));return}
  var page=e.target.closest("[data-table-page]");
  if(page){
    var id=page.getAttribute("data-table-id"),st=S.tables[id];
    if(!st)return;
    st.page+=page.getAttribute("data-table-page")==="next"?1:-1;
    updateTable(id);
  }
});
document.addEventListener("keydown",function(e){
  if(!e.target.closest||(e.key!=="Enter"&&e.key!==" "))return;
  var sort=e.target.closest("[data-table-sort]");
  if(sort){e.preventDefault();tableSort(sort.getAttribute("data-table-sort"),sort.getAttribute("data-key"))}
});

probe();
</script>
</body>
</html>`;

const server = http.createServer(async (req, res) => {
  try {
    const u = new URL(req.url, "http://x");
    const p = u.pathname;

    if (p === "/" || p === "/admin") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
      res.end(PAGE.replace("__FAVICON__", FAVICON));
      return;
    }
    if (p === "/api/login" && req.method === "POST") {
      const ip = req.socket.remoteAddress || "";
      const now = Date.now();
      const body = JSON.parse(await readBody(req));
      const attempt = cleanPw(body.password);
      if (attempt === livePassword) {
        loginFails.delete(ip);
        const exp = now + 43200000;
        res.setHeader(
          "Set-Cookie",
          "na_admin=" + exp + "." + sign(String(exp)) + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"
        );
        json(res, 200, { ok: true });
        return;
      }
      const rec = loginFails.get(ip) || { n: 0, t: 0 };
      if (rec.n >= 10 && now - rec.t < 300000) {
        json(res, 429, { error: "تلاش زیاد؛ ۵ دقیقه دیگر دوباره امتحان کنید" });
        return;
      }
      console.error("[admin] failed login: len=" + attempt.length + " ip=" + ip);
      rec.n++;
      rec.t = now;
      loginFails.set(ip, rec);
      json(res, 401, { error: "bad password" });
      return;
    }
    if (p === "/api/logout") {
      res.setHeader("Set-Cookie", "na_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0");
      json(res, 200, { ok: true });
      return;
    }
    if (!cookieOk(req)) {
      json(res, 401, { error: "unauthorized" });
      return;
    }

    if (p === "/api/overview") return void apiOverview(res);
    if (p === "/api/tenants" && req.method === "GET") return void apiTenants(res);
    if (p === "/api/tenants" && req.method === "POST") {
      const body = JSON.parse(await readBody(req));
      return void apiTenantCreate(res, body);
    }
    let m;
    if ((m = /^\/api\/tenants\/([^/]+)\/action$/.exec(p)) && req.method === "POST") {
      const body = JSON.parse(await readBody(req));
      return void apiTenantAction(req, res, m[1], body);
    }
    if ((m = /^\/api\/tenants\/([^/]+)$/.exec(p)) && req.method === "DELETE") {
      return void apiTenantDelete(res, m[1]);
    }
    if ((m = /^\/api\/tenants\/([^/]+)\/password$/.exec(p)) && req.method === "POST") {
      const body = JSON.parse(await readBody(req));
      return void apiTenantPassword(res, m[1], body);
    }
    if (p === "/api/conversations") return void apiConversations(res, u.searchParams.get("tenant") || "default");
    if (p === "/api/logs") return void apiLogs(res, u, res);
    if (p === "/api/llm") return void apiLlm(res);
    if (p === "/api/disk-usage") return void apiDiskUsage(res);
    if (p === "/api/password" && req.method === "POST") {
      const body = JSON.parse(await readBody(req));
      return void apiPassword(res, body);
    }
    if ((m = /^\/api\/services\/([^/]+)\/action$/.exec(p)) && req.method === "POST") {
      const body = JSON.parse(await readBody(req));
      return void apiServiceAction(res, m[1], body);
    }
    if (p === "/api/system/reboot" && req.method === "POST") {
      json(res, 200, { ok: true, note: "سرور تا یک دقیقه دیگر ری‌استارت می‌شود" });
      setTimeout(() => sh("systemctl", ["reboot"], { timeout: 8000 }).catch(() => {}), 800);
      return;
    }

    json(res, 404, { error: "not found" });
  } catch (e) {
    json(res, 500, { error: String(e.message).slice(0, 300) });
  }
});

async function apiOverview(res) {
  const customers = await loadCustomers();
  const gw = await gatewayHealth();
  const units = SYSTEM_UNITS.slice();
  for (const c of customers) units.push(unitFor(c.name));
  const services = [];
  for (const unit of units) {
    services.push({ unit, active: await serviceState(unit), upSec: await serviceUptime(unit) });
  }
  const disk = await sh("df", ["-B1", "/"]).catch(() => "");
  let diskTotal = 0;
  let diskUsed = 0;
  const dl = disk.split("\n")[1];
  if (dl) {
    const parts = dl.split(/\s+/);
    diskTotal = Number(parts[1]) || 0;
    diskUsed = Number(parts[2]) || 0;
  }
  json(res, 200, {
    services,
    uptime: os.uptime(),
    uptimeText: fmtUptime(os.uptime()),
    mem: { total: os.totalmem(), free: os.freemem() },
    load: os.loadavg(),
    cpuCount: os.cpus().length,
    disk: { total: diskTotal, used: diskUsed },
    gateway: { customers: (gw.customers || []).map((c) => ({ name: c.name, connected: !!c.connected })) },
    hostname: os.hostname(),
    now: Date.now(),
  });
}

async function apiTenants(res) {
  const customers = await loadCustomers();
  const gw = await gatewayHealth();
  const gwMap = {};
  for (const c of gw.customers || []) gwMap[c.name] = !!c.connected;
  const out = [];
  for (const c of customers) {
    const env = await parseEnv(c.name);
    const unit = unitFor(c.name);
    const active = await serviceState(unit);
    let convCount = null;
    let agentOk = false;
    const port = Number(c.port || env.PORT || 0);
    if (port && active === "active") {
      const key = env.SESSION_API_KEY || c.token || "";
      const list = await fetchConvList(port, key, 100);
      if (list !== null) {
        agentOk = true;
        convCount = list.length;
      } else {
        const r = await fetchHttp("http://127.0.0.1:" + port + "/api/health", { headers: { "X-Session-API-Key": key } }, 2500);
        agentOk = r.status === 200;
      }
    }
    let size = null;
    const serverHome = env.HOME || (c.name === "default" ? HOME : "");
    const stateDir =
      serverHome && existsSync(serverHome + "/.openhands") ? serverHome + "/.openhands" : serverHome;
    if (stateDir && existsSync(stateDir)) {
      const cached = duCache.get(c.name);
      if (cached && Date.now() - cached.ts < 600000) size = cached.size;
      else {
        try {
          const rdu = await exec("du", ["-s", "-B1", stateDir], { timeout: 60000 });
          const mm = /(\d+)/.exec((rdu.stdout || "").trim());
          if (mm) {
            size = Number(mm[1]);
            duCache.set(c.name, { ts: Date.now(), size });
          }
        } catch (e) {
          console.error("[admin] du failed:", stateDir, e && e.message);
        }
      }
    }
    out.push({
      name: c.name,
      port,
      home: serverHome,
      roots: c.roots || [],
      unit,
      active,
      agentOk,
      clientConnected: !!gwMap[c.name],
      convCount,
      size,
      sizeText: size == null ? "-" : humanSize(size),
      token: c.token || "",
      hasPortalUser: !!(await loadPortalUsers()).some((u) => u.username === c.name),
    });
  }
  json(res, 200, { tenants: out });
}

async function apiTenantAction(req, res, name, body) {
  if (!/^[a-z0-9_-]{1,32}$/.test(name)) return json(res, 400, { error: "bad name" });
  const action = String(body.action || "");
  if (!["start", "stop", "restart"].includes(action)) return json(res, 400, { error: "bad action" });
  const unit = unitFor(name);
  try {
    await sh("systemctl", [action, unit], { timeout: 40000 });
    json(res, 200, { ok: true, unit, action, state: await serviceState(unit) });
  } catch (e) {
    json(res, 500, { error: String(e.message).slice(0, 300) });
  }
}

async function apiTenantCreate(res, body) {
  const name = String(body.name || "").trim().toLowerCase();
  const password = String(body.password || "");
  if (!/^[a-z][a-z0-9-]{0,31}$/.test(name)) return json(res, 400, { error: "نام فقط حروف کوچک، عدد و خط تیره" });
  if (name === "default") return json(res, 400, { error: "نام default مجاز نیست" });
  const customers = await loadCustomers();
  if (customers.some((c) => c.name === name)) return json(res, 400, { error: "این نام وجود دارد" });
  const portalUsers = await loadPortalUsers();
  if (portalUsers.some((u) => u.username === name))
    return json(res, 400, { error: "این نام قبلاً به‌عنوان کاربر پورتال ثبت شده" });
  const maxPort = customers.reduce((mm, c) => Math.max(mm, Number(c.port) || 0), 18000);
  const port = maxPort + 10;
  const home = path.join(TENANTS_DIR, name);
  const token = crypto.randomBytes(16).toString("hex");
  const secret = crypto.randomBytes(24).toString("hex");
  await mkdir(home, { recursive: true });
  const envContent = [
    "HOME=" + home,
    "PORT=" + port,
    "NOVAAGENT_CUSTOMER_HOME=" + home,
    "NOVAAGENT_LOCAL_TOOLS_URL=http://127.0.0.1:18766/c/" + name,
    "NOVAAGENT_LOCAL_TOOLS_TOKEN=" + token,
    "SESSION_API_KEY=" + token,
    "NOVAAGENT_TENANT=" + name,
    "OH_SECRET_KEY=" + secret,
    "",
  ].join("\n");
  await writeFile(envPath(name), envContent, { mode: 0o600 });
  await sh("chown", ["-R", "moazemi-gc:moazemi-gc", home, envPath(name)], { timeout: 30000 }).catch(() => {});
  customers.push({ name, token, roots: [home], port });
  await saveCustomers(customers);
  if (password) await upsertPortalUser(name, name, password);
  try {
    await sh("systemctl", ["daemon-reload"], { timeout: 20000 });
    await sh("systemctl", ["enable", "--now", unitFor(name)], { timeout: 40000 });
  } catch (e) {
    json(res, 500, { error: "ساخت tenant انجام شد ولی سرویس بالا نیامد: " + String(e.message).slice(0, 200) });
    return;
  }
  json(res, 200, { ok: true, name, port, token, home, portalUser: !!password });
}

async function apiTenantPassword(res, name, body) {
  if (!/^[a-z0-9_-]{1,32}$/.test(name)) return json(res, 400, { error: "bad name" });
  const password = String(body.password || "");
  if (password.length < 4) return json(res, 400, { error: "رمز حداقل ۴ کاراکتر" });
  const customers = await loadCustomers();
  if (!customers.some((c) => c.name === name)) return json(res, 404, { error: "tenant پیدا نشد" });
  await upsertPortalUser(name, name, password);
  json(res, 200, { ok: true, username: name });
}

async function apiTenantDelete(res, name) {
  if (!/^[a-z0-9_-]{1,32}$/.test(name) || name === "default")
    return json(res, 400, { error: "حذف default مجاز نیست" });
  const customers = await loadCustomers();
  const idx = customers.findIndex((c) => c.name === name);
  if (idx < 0) return json(res, 404, { error: "پیدا نشد" });
  await sh("systemctl", ["disable", "--now", unitFor(name)], { timeout: 40000 }).catch(() => {});
  customers.splice(idx, 1);
  await saveCustomers(customers);
  const ep = envPath(name);
  if (existsSync(ep)) await rename(ep, ep + ".removed-" + Date.now()).catch(() => {});
  json(res, 200, { ok: true, note: "فایل‌های فضای کاری حفظ شدند" });
}

function convRow(cv) {
  return {
    id: cv.id || cv.conversation_id || "",
    title: cv.title || "(بدون عنوان)",
    created_at: cv.created_at || null,
    updated_at: cv.last_updated_at || cv.updated_at || cv.modified_at || null,
    status: cv.execution_status || cv.status || null,
  };
}

async function fetchConvList(port, key, limit) {
  const r = await fetchHttp(
    "http://127.0.0.1:" + port + "/api/conversations/search?limit=" + limit,
    { headers: { "X-Session-API-Key": key } },
    6000
  );
  if (r.status !== 200) return null;
  try {
    const d = JSON.parse(r.text);
    if (Array.isArray(d.items)) return d.items;
    if (Array.isArray(d.results)) return d.results;
    if (Array.isArray(d)) return d;
    return [];
  } catch {
    return null;
  }
}

async function apiConversations(res, tenant) {
  const customers = await loadCustomers();
  const c = customers.find((x) => x.name === tenant);
  if (!c) return json(res, 404, { error: "tenant پیدا نشد" });
  const env = await parseEnv(tenant);
  const port = Number(c.port || env.PORT || 0);
  const key = env.SESSION_API_KEY || c.token || "";
  const list = await fetchConvList(port, key, 50);
  let rows = [];
  if (list !== null) rows = list.map(convRow);
  else {
    const r2 = await fetchHttp(
      "http://127.0.0.1:" + port + "/api/conversations",
      { headers: { "X-Session-API-Key": key } },
      6000
    );
    if (r2.status !== 200) return json(res, 502, { error: "agent-server در دسترس نیست (" + r2.status + ")" });
    try {
      const d = JSON.parse(r2.text);
      const l2 = d.conversations || d || [];
      rows = (Array.isArray(l2) ? l2 : []).map(convRow);
    } catch {}
  }
  json(res, 200, { tenant, count: rows.length, conversations: rows });
}

async function apiLogs(res, u, resOut) {
  const unit = u.searchParams.get("unit") || "";
  const download = u.searchParams.get("download") === "1";
  if (!/^[A-Za-z0-9@._-]{1,64}$/.test(unit)) return json(res, 400, { error: "bad unit" });
  const n = Math.min(Math.max(parseInt(u.searchParams.get("lines"), 10) || 100, 10), 5000);
  try {
    const out = await sh("journalctl", ["-u", unit, "-n", String(n), "--no-pager", "-o", "short-iso"], { timeout: 20000 });
    if (download) {
      resOut.writeHead(200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": 'attachment; filename="' + unit + '.log"',
      });
      resOut.end(out);
      return;
    }
    json(res, 200, { unit, lines: out.split("\n").reverse().join("\n") });
  } catch (e) {
    json(res, 500, { error: String(e.message).slice(0, 300) });
  }
}

async function apiLlm(res) {
  const t0 = Date.now();
  const local = await fetchHttp(VLLM, {}, 4000);
  const localMs = Date.now() - t0;
  let localModels = [];
  try {
    localModels = (JSON.parse(local.text).data || []).map((m) => m.id);
  } catch {}
  let key = "";
  try {
    key = (JSON.parse(await readFile(AUTH_FILE, "utf8")).isigpu || {}).key || "";
  } catch {}
  const t1 = Date.now();
  const remote = key ? await fetchHttpsInsecure("/v1/models", { Authorization: "Bearer " + key }) : { status: 0, text: "" };
  const remoteMs = Date.now() - t1;
  let remoteModels = [];
  try {
    remoteModels = (JSON.parse(remote.text).data || []).map((m) => m.id);
  } catch {}
  json(res, 200, {
    local: { ok: local.status === 200, ms: localMs, models: localModels },
    remote: { ok: remote.status === 200, status: remote.status, ms: remoteMs, models: remoteModels },
  });
}

async function apiDiskUsage(res) {
  if (diskCache.data && Date.now() - diskCache.ts < 300000) {
    return json(res, 200, diskCache.data);
  }
  const targets = [
    "/home/moazemi-gc/OpenHands",
    "/home/moazemi-gc/downloads",
    "/home/moazemi-gc/.openhands",
    "/home/moazemi-gc/nova-tenants",
    "/var/lib/docker",
    "/var/log",
  ];
  const items = [];
  for (const t of targets) {
    if (!existsSync(t)) continue;
    try {
      const r = await exec("du", ["-s", "-B1", t], { timeout: 120000 });
      const mm = /(\d+)/.exec((r.stdout || "").trim());
      if (mm) items.push({ dir: t, bytes: Number(mm[1]), sizeText: humanSize(Number(mm[1])) });
    } catch {}
  }
  items.sort((a, b) => b.bytes - a.bytes);
  diskCache.ts = Date.now();
  diskCache.data = { items };
  json(res, 200, diskCache.data);
}

async function apiPassword(res, body) {
  const current = cleanPw(body.current);
  const next = cleanPw(body.next);
  if (current !== livePassword) return json(res, 401, { error: "رمز فعلی اشتباه است" });
  if (!next || next.length < 8) return json(res, 400, { error: "رمز جدید حداقل ۸ کاراکتر" });
  if (next === current) return json(res, 400, { error: "رمز جدید باید متفاوت باشد" });
  try {
    const envRaw = await readFile("/home/moazemi-gc/.nova-admin-env", "utf8");
    const secretLine = (envRaw.split("\n").find((l) => l.startsWith("ADMIN_SECRET=")) || "").trim();
    await writeFile(
      "/home/moazemi-gc/.nova-admin-env",
      "ADMIN_PASSWORD=" + next + "\n" + secretLine + "\n",
      { mode: 0o600 }
    );
    livePassword = next;
    console.error("[admin] password changed via panel");
    json(res, 200, { ok: true });
  } catch (e) {
    json(res, 500, { error: String(e.message).slice(0, 200) });
  }
}

async function apiServiceAction(res, unit, body) {
  if (!SYSTEM_UNITS.includes(unit) && !/^nova-agent-server(@[a-z0-9_-]+)?$/.test(unit))
    return json(res, 400, { error: "unit مجاز نیست" });
  const action = String(body.action || "");
  if (!["start", "stop", "restart"].includes(action)) return json(res, 400, { error: "bad action" });
  try {
    if (unit === "nova-admin") {
      json(res, 200, { ok: true, note: "پنل ری‌استارت می‌شود" });
      setTimeout(() => sh("systemctl", ["restart", unit], { timeout: 20000 }).catch(() => {}), 600);
      return;
    }
    await sh("systemctl", [action, unit], { timeout: 40000 });
    json(res, 200, { ok: true, state: await serviceState(unit) });
  } catch (e) {
    json(res, 500, { error: String(e.message).slice(0, 300) });
  }
}

server.listen(PORT, "0.0.0.0", () => console.log("[admin] listening on 0.0.0.0:" + PORT));
