"""Erzeugt lst/app/index.html aus dem abgestimmten Prototyp (prototyp/prototyp.html).

Der Prototyp bleibt die Vorlage für Bedienung und Aussehen. Dieses Skript ersetzt die
Teile, die im Echtbetrieb anders laufen: Anmeldung über den Server, Daten vom Server,
Abgleich (Einsätze, Fotos, Listen, Abschluss) mit Zwischenspeicher fürs Offline-Arbeiten.

Aufruf:  python3 werkzeuge/app_bauen.py
Jede Ersetzung muss genau einmal passen, sonst bricht das Skript ab.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUELLE = ROOT / "prototyp" / "prototyp.html"
ZIEL = ROOT / "lst" / "app" / "index.html"

KOPF = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="LST Baustelle">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="theme-color" content="#ffffff">
<link rel="manifest" href="/app/manifest.webmanifest">
<link rel="apple-touch-icon" href="/app/icon-180.png">
"""

SYNC = r"""
/* ---------- Abgleich mit dem Server ----------
   Alles wird zuerst auf dem iPad gespeichert (localStorage, Fotos in IndexedDB) und dann
   im Hintergrund zum Server geschickt. Ohne Netz wartet es und geht später raus. */
const SY={busy:false,offline:false,err:'',offen:0,last:0,again:false};
const sk=()=>(S.sync=S.sync||{});
async function api(path,opt={}){let r;try{r=await fetch(path,{credentials:'same-origin',cache:'no-store',...opt})}catch(err){const e2=new Error('Keine Verbindung zum Büro');e2.netz=true;throw e2}
  let d=null;try{d=await r.json()}catch(err){}
  if(r.status===401){const e2=new Error('Bitte neu anmelden');e2.auth=true;throw e2}
  if(!r.ok)throw new Error((d&&d.fehler)||('Fehler '+r.status));return d}
const jpost=(p,b)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
function fuerServer(e){const c=JSON.parse(JSON.stringify(e));delete c.lsHtml;delete c.sync_abschluss;delete c.serverDocs;delete c.docs;
  c.fotos=(c.fotos||[]).map(f=>({id:f.id,anlage:f.anlage,phase:f.phase,tag:f.tag||0,ts:f.ts}));return c}
async function stempel(o){return sha(JSON.stringify(o))}
function listen(){return {maengel:S.maengel,tanken:S.tanken,reisetage:S.reisetage,fahrzeuge:S.fahrzeuge,kompressoren:S.kompressoren,intern:S.intern,notizen:S.notizen||[],geloescht:S.weg||{}}}
function zaehlen(){let n=0;for(const e of S.einsaetze){n+=(e.fotos||[]).filter(f=>!f.up).length;if(e.sync_abschluss)n++}SY.offen=n}
let syncT=null;function syncBald(ms=1500){clearTimeout(syncT);syncT=setTimeout(syncen,ms)}
async function syncen(){if(!S.user)return;if(SY.busy){SY.again=true;return}SY.busy=true;SY.again=false;chip();
  try{
    for(const e of S.einsaetze){
      const body=fuerServer(e);const h=await stempel(body);
      if(sk()['e:'+e.id]!==h){await jpost('/api/einsatz',body);sk()['e:'+e.id]=h;save0()}
      for(const f of e.fotos||[]){if(f.up)continue;const b=await getBlob(f.id);if(!b){f.up=-1;save0();continue}
        const fd=new FormData();fd.append('foto',b,f.id+'.jpg');fd.append('id',f.id);fd.append('anlage',f.anlage||'');fd.append('phase',f.phase||'vorher');fd.append('tag',f.tag||0);
        await api(`/api/einsatz/${encodeURIComponent(e.id)}/foto`,{method:'POST',body:fd});f.up=1;save0();zaehlen();chip()}
      if(e.sync_abschluss&&(e.fotos||[]).every(f=>f.up)){
        const r=await jpost(`/api/einsatz/${encodeURIComponent(e.id)}/abschluss`,e.sync_abschluss);
        delete e.sync_abschluss;e.serverDocs={lieferschein:r.lieferschein,fotodoku:r.fotodoku};e.docs=r.docs||[];e.ohneLexware=r.lexware===false;save0();
        toast('Lieferschein und Fotodoku sind fertig');if(V.id===e.id)render()}
    }
    const L=listen();const hl=await stempel(L);if(sk().listen!==hl){await jpost('/api/listen',L);sk().listen=hl;S.weg={};save0()}
    SY.offline=false;SY.err='';SY.last=Date.now();
  }catch(err){SY.offline=!!err.netz;SY.err=err.message;if(err.auth){S.user=null;save0();V={name:'login'};render();toast('Bitte neu anmelden. Deine Eingaben sind gespeichert.')}}
  finally{SY.busy=false;zaehlen();chip();if(SY.again)syncBald(300)}}
/* Server-Stand übernehmen. Was auf dem iPad geändert und noch nicht übertragen ist, bleibt. */
async function holen(){if(!S.user)return;let d;try{d=await api('/api/stand')}catch(err){SY.offline=!!err.netz;SY.err=err.message;chip();if(err.auth){S.user=null;save0();V={name:'login'};render()}return}
  ['mitarbeiter','strahlgeraete','typen','strahlmittel','checkliste'].forEach(k=>{if(d[k]!=null)S[k]=d[k]});
  const lokalL=await stempel(listen());const listenUnveraendert=sk().listen===lokalL;
  for(const k of ['maengel','tanken','reisetage','fahrzeuge','kompressoren','intern','notizen']){const srv=d[k]||[];
    if(listenUnveraendert)S[k]=srv;else{const m=new Map(srv.map(x=>[x.id,x]));(S[k]||[]).forEach(x=>m.set(x.id,x));S[k]=[...m.values()]}}
  if(listenUnveraendert)sk().listen=await stempel(listen());
  const neu=[];
  for(const se of d.einsaetze){const le=E(se.id);
    if(!le){se.trash=[];neu.push(se);sk()['e:'+se.id]=await stempel(fuerServer(se));continue}
    const unveraendert=sk()['e:'+le.id]===await stempel(fuerServer(le));
    if(unveraendert&&!le.sync_abschluss){const lokaleFotos=new Map((le.fotos||[]).map(f=>[f.id,f]));
      se.fotos=se.fotos.map(f=>({...(lokaleFotos.get(f.id)||{}),...f}));(le.fotos||[]).filter(f=>!f.up).forEach(f=>se.fotos.push(f));
      se.trash=le.trash||[];if(le.lsHtml)se.lsHtml=le.lsHtml;if(le.serverDocs)se.serverDocs=le.serverDocs;neu.push(se);sk()['e:'+se.id]=await stempel(fuerServer(se))}
    else{if(se.docs)le.docs=se.docs;if(se.ls&&!le.ls)le.ls=se.ls;if(se.status==='abgeschlossen'&&!le.sync_abschluss){le.status='abgeschlossen';le.abschluss=se.abschluss}neu.push(le)}}
  for(const e of S.einsaetze){if(d.einsaetze.some(x=>x.id===e.id))continue;
    if(e.sync_abschluss||sk()['e:'+e.id]!==await stempel(fuerServer(e)))neu.push(e);else delete sk()['e:'+e.id]}
  S.einsaetze=neu;if(d.ich)S.ich=d.ich;save0();zaehlen();render();syncBald(200)}
function chip(){const c=document.getElementById('sync');if(!c)return;let t,cls;
  if(SY.busy){t=SY.offen?`Überträgt … (${SY.offen})`:'Überträgt …';cls=''}
  else if(SY.offline){t=SY.offen?`Offline · ${SY.offen} wartet`:'Offline · gespeichert';cls='warn'}
  else if(SY.err){t='Fehler: '+SY.err;cls='err'}
  else{t=SY.offen?`${SY.offen} wartet`:'Alles im Büro';cls='ok'}
  c.className='chip '+cls;c.querySelector('.lbl').textContent=t;c.title=SY.last?'Zuletzt abgeglichen '+new Date(SY.last).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}):''}
window.addEventListener('online',()=>{SY.offline=false;holen()});
window.addEventListener('offline',()=>{SY.offline=true;chip()});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')holen()});
setInterval(()=>{if(document.visibilityState==='visible')holen()},120000);
async function leuteLaden(){try{const l=await api('/api/leute');S.leute=l;save0();if(!S.user)render()}catch(err){SY.offline=!!err.netz}}
async function pinPruefsumme(id,pin){return sha('lst|'+id+'|'+pin)}
/* ---------- Lieferschein und Fotodoku vom Server laden und teilen ----------
   Erst laden, dann teilen: Safari erlaubt das Teilen nur direkt nach einem Fingertipp. */
/* Interne Aufgaben: überfällig/heute zuerst, dann mit Termin, dann ohne Termin nach Status-Rang
   (Zu tun/Läuft, Wartet, Wenn Zeit dann tun) */
function internKey(i){const d=i.datum||'';if(d&&d<=today())return [0,d];if(d)return [1,d];return [1+(i.rang||1),i.titel||'']}
function internSort(a,b){const x=internKey(a),y=internKey(b);return x[0]-y[0]||String(x[1]).localeCompare(String(y[1]))}
function internMeta(i){const d=i.datum,t=[];t.push(d?fD(d)+(d<today()?' · überfällig':d===today()?' · heute':''):'ohne Termin');
  if(i.nstatus)t.push(i.nstatus);if(i.prio)t.push('Priorität '+i.prio);if(i.projekt)t.push(i.projekt);return t.join(' · ')}
/* ---------- Interner Auftrag mit Arbeitsbericht ----------
   Pflicht zum Abhaken ist nur „Was wurde gemacht?“. Zeit, Material und Fotos sind freiwillig.
   Fotos gehen direkt zum Büro (brauchen also Verbindung). */
function dauerText(z){if(!z||!z.von||!z.bis)return '–';const m0=(+z.bis.slice(0,2)*60+ +z.bis.slice(3,5))-(+z.von.slice(0,2)*60+ +z.von.slice(3,5));const m=m0<0?m0+1440:m0;return `${Math.floor(m/60)}:${pad(m%60)} h`}
function vIntern() {const i=S.intern.find(x=>x.id===V.id);if(!i)return vStart();const offen=i.status==='offen';const z=i.zeit||{};const ro=offen?'':' disabled';
  const fotos=ph=>(i.fotos||[]).filter(f=>f.phase===ph).map(f=>`<img src="/intern-foto/${encodeURIComponent(i.id)}/${encodeURIComponent(f.id)}?v=1" alt="Foto ${ph}" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px">`).join('');
  return `<div class="page"><div class="icon">🔧</div><h1>${esc(i.titel)}</h1>
  <div class="props"><div class="k">Erledigen am</div><div>${i.datum?fDY(i.datum):'ohne Termin'}</div>
  ${i.nstatus?`<div class="k">In Notion</div><div>${esc(i.nstatus)}${i.prio?' · Priorität '+esc(i.prio):''}</div>`:''}
  ${i.projekt?`<div class="k">Projekt</div><div>${esc(i.projekt)}</div>`:''}
  <div class="k">Zuständig</div><div>${i.zust?`<span class="tag purple">${esc(ma(i.zust)?.name)}</span>`:'<span class="tag">offen für alle</span>'}</div>
  <div class="k">Status</div><div>${offen?'<span class="tag yellow">Offen</span>':`<span class="tag green">Erledigt${i.erledigt_am?' am '+fDY(i.erledigt_am):''}</span>`}</div></div>
  ${i.beschreibung?`<div class="hr"></div><p style="white-space:pre-line">${esc(i.beschreibung)}</p>`:''}
  <div class="hr"></div><h2>Arbeitsbericht</h2>
  <div class="field"><label for="ib-t">Was wurde gemacht? <span class="small muted">(nötig zum Abhaken)</span></label><textarea class="inp" id="ib-t" data-ib="notiz" placeholder="z. B. Kupplung getauscht, neue bestellt"${ro}>${esc(i.notiz||'')}</textarea></div>
  <div class="row" style="flex-wrap:wrap;gap:12px;align-items:flex-end">
    <div class="field"><label for="ib-d">Datum</label><input class="inp" type="date" id="ib-d" data-ib="z.datum" value="${z.datum||today()}"${ro}></div>
    <div class="field"><label for="ib-v">von</label><input class="inp" type="time" id="ib-v" data-ib="z.von" value="${z.von||''}"${ro}></div>
    <div class="field"><label for="ib-b">bis</label><input class="inp" type="time" id="ib-b" data-ib="z.bis" value="${z.bis||''}"${ro}></div>
    <div class="field"><label>Dauer</label><div id="ib-dauer" style="padding:10px 2px">${dauerText(z)}</div></div></div>
  <div class="field"><label for="ib-m">Material <span class="small muted">(freiwillig)</span></label><textarea class="inp" id="ib-m" data-ib="material" style="min-height:60px" placeholder="z. B. 2 m Druckluftschlauch, 1 Kupplung"${ro}>${esc(i.material||'')}</textarea></div>
  <div class="field"><label>Fotos <span class="small muted">(freiwillig)</span></label>
  ${['vorher','nachher'].map(ph=>`<div class="small muted" style="margin:6px 0">${ph==='vorher'?'Vorher':'Nachher'}</div><div class="thumbs">${fotos(ph)}${offen?`<label class="btn small" style="cursor:pointer;justify-content:center">📷 Foto<input type="file" accept="image/*" multiple hidden data-ifoto="${ph}"></label>`:''}</div>`).join('')}
  <div class="small muted" id="ib-st" role="status" style="min-height:18px"></div></div>
  <div class="fab">${!i.zust&&offen?'<button class="btn" data-a="mache">Mache ich</button>':''}${offen?'<button class="btn pri" data-a="erledigt">Erledigt</button>':''}</div></div>`}
function internErledigt(){const i=S.intern.find(x=>x.id===V.id);if(!i)return;
  if(!(i.notiz||'').trim()){toast('Bitte kurz eintragen, was gemacht wurde.');const t=document.getElementById('ib-t');if(t)t.focus();return}
  i.status='erledigt';i.erledigt_am=today();i.erledigt_von=S.user;i.zeit=i.zeit||{datum:today()};if(!i.zust)i.zust=S.user;
  save();V={name:'start'};render();toast('Auftrag erledigt')}
function internFeld(ev){const t=ev.target;if(!t||!t.dataset||!t.dataset.ib)return;const i=S.intern.find(x=>x.id===V.id);if(!i||i.status!=='offen')return;const k=t.dataset.ib;
  if(k.startsWith('z.')){i.zeit=i.zeit||{datum:today()};i.zeit[k.slice(2)]=t.value;const d=document.getElementById('ib-dauer');if(d)d.textContent=dauerText(i.zeit)}else i[k]=t.value;save()}
document.addEventListener('input',internFeld);document.addEventListener('change',internFeld);
document.addEventListener('change',async ev=>{const t=ev.target;if(!t||!t.dataset||!t.dataset.ifoto)return;const i=S.intern.find(x=>x.id===V.id);if(!i)return;
  const st=document.getElementById('ib-st');const files=[...t.files];const ph=t.dataset.ifoto;t.value='';
  for(const [n,f] of files.entries()){if(st)st.textContent=`Foto ${n+1} von ${files.length} wird hochgeladen …`;
    try{const blob=await shrink(f);const fd=new FormData();fd.append('foto',blob,'foto.jpg');fd.append('phase',ph);
      const r=await api(`/api/intern/${encodeURIComponent(i.id)}/foto`,{method:'POST',body:fd});i.fotos=i.fotos||[];i.fotos.push({id:r.id,phase:ph});save()}
    catch(err){if(st)st.textContent=err.netz?'Fotos gehen nur mit Verbindung zum Büro. Bitte später noch einmal.':err.message;render();return}}
  render()});
const DOKNAME={lieferschein:'Lieferschein',fotodoku:'Fotodoku'};
function dokLeiste(e){const docs=e.docs||[];
  if(e.sync_abschluss)return `<div class="callout small" style="margin-bottom:16px"><span>⏳</span><div>Lieferschein und Fotodoku entstehen im Büro, sobald alles übertragen ist. Danach kannst du sie hier ansehen und verschicken.</div></div>`;
  if(!docs.length)return `<div class="row" style="margin-bottom:16px"><span class="small muted">Noch keine Dokumente vom Büro geladen.</span><button class="btn small" data-a="sync">Neu laden</button></div>`;
  return `<div class="stack" style="margin-bottom:16px"><div class="row" style="flex-wrap:wrap">${docs.map(d=>`<button class="btn" data-a="dokzeigen" data-id="${d.id}">${d.art==='lieferschein'?'📄':'🖼️'} ${DOKNAME[d.art]||d.art} ansehen</button>`).join('')}</div>
  <div class="small muted">${e.ohneLexware&&!e.ls?'Hinweis: Beim Abschluss lag kein Lexware-Lieferschein im Büro vor, der Lieferschein enthält nur die Seite Arbeitsnachweis.':'Ansehen, dann verschicken oder teilen.'}</div></div>`}
/* Dokument ansehen: Seiten als Bilder vom Server, mit Zurück, Verschicken und Teilen */
function dokAnsicht(e,id){const d=(e.docs||[]).find(x=>x.id===id);if(!d)return;const n=d.seiten||1;
  const o=document.createElement('div');o.id='dokansicht';
  o.style.cssText='position:fixed;inset:0;z-index:15;background:var(--bg);overflow:auto;padding:calc(env(safe-area-inset-top,0px) + 8px) 12px calc(env(safe-area-inset-bottom,0px) + 16px)';
  o.innerHTML=`<div class="row" style="position:sticky;top:0;background:var(--bg);padding:8px 0;gap:8px;flex-wrap:wrap;z-index:1;border-bottom:1px solid var(--line);margin-bottom:12px">
    <button class="btn" id="da-zu">← Zurück</button><b style="flex:1;min-width:160px">${esc(DOKNAME[d.art]||d.art)}</b>
    <button class="btn pri" id="da-mail">✉️ Verschicken</button><button class="btn" id="da-teilen" data-ids="${d.id}">📤 Teilen</button></div>
    <div class="small muted" id="dok-st" role="status" style="min-height:18px">${esc(d.name)}</div>
    ${Array.from({length:n},(_,k)=>`<img src="/api/dokument/${encodeURIComponent(id)}/seite/${k+1}" alt="Seite ${k+1} von ${n}" loading="lazy" style="display:block;width:100%;max-width:900px;margin:0 auto 14px;box-shadow:0 1px 6px rgba(0,0,0,.18);background:#fff;min-height:200px">`).join('')}
    <div class="row" style="justify-content:center"><button class="btn" id="da-zu2">← Zurück</button></div>`;
  document.body.appendChild(o);document.body.style.overflow='hidden';
  const zu=()=>{o.remove();document.body.style.overflow=''};
  o.querySelector('#da-zu').onclick=zu;o.querySelector('#da-zu2').onclick=zu;
  o.querySelector('#da-teilen').onclick=ev=>dokTeilen(ev.currentTarget,e);
  o.querySelector('#da-mail').onclick=()=>verschickenSheet(e,id)}
function verschickenSheet(e,id){const docs=e.docs||[];const ak=e.apKunde||{},ao=e.apOrt||{};
  const emp=[[ak.mail,ak.name||'Auftraggeber',true],[ao.mail,ao.name||'Einsatzort',false]].filter(x=>x[0]);
  sheet(`<h2>Verschicken</h2>
  <div class="field"><label>Anhänge</label>${docs.map(d=>`<label class="row" style="gap:8px;margin:4px 0"><input type="checkbox" data-vd="${d.id}"${d.id===id?' checked':''}> ${esc(DOKNAME[d.art]||d.art)}</label>`).join('')}</div>
  <div class="field"><label>An</label>${emp.map(([m,nm,an])=>`<label class="row" style="gap:8px;margin:4px 0"><input type="checkbox" data-ve="${esc(m)}"${an?' checked':''}> ${esc(nm)} · <span class="muted">${esc(m)}</span></label>`).join('')||'<div class="small muted">Keine Adresse hinterlegt – unten eintragen.</div>'}</div>
  <div class="field"><label for="vs-w">Weitere Empfänger <span class="small muted">(mit Komma trennen)</span></label><input class="inp" id="vs-w" type="email" inputmode="email" autocapitalize="off" placeholder="name@firma.de"></div>
  <div class="field"><label for="vs-b">Betreff</label><input class="inp" id="vs-b" value="${esc('Unterlagen '+e.einsatzort+(e.ls?' – '+e.ls:''))}"></div>
  <div class="field"><label for="vs-t">Text</label><textarea class="inp" id="vs-t">${esc('Guten Tag,\n\nanbei die Unterlagen zum Einsatz '+e.einsatzort+'.\n\nMit freundlichen Grüßen\nLST Lüke-Strahl-Technik GmbH')}</textarea></div>
  <div class="small" id="vs-st" role="status" style="min-height:18px"></div>
  <div class="row" style="justify-content:flex-end"><button class="btn" id="vs-x">Abbrechen</button><button class="btn pri" id="vs-ok">Senden</button></div>`,el=>{
    el.querySelector('#vs-x').onclick=closeSheet;const st=el.querySelector('#vs-st');
    el.querySelector('#vs-ok').onclick=async ev=>{const b=ev.currentTarget;
      const body={docs:[...el.querySelectorAll('[data-vd]:checked')].map(x=>x.dataset.vd),an:[...el.querySelectorAll('[data-ve]:checked')].map(x=>x.dataset.ve),
        weitere:el.querySelector('#vs-w').value,betreff:el.querySelector('#vs-b').value,text:el.querySelector('#vs-t').value};
      if(!body.docs.length){st.textContent='Bitte mindestens ein Dokument wählen.';return}
      if(!body.an.length&&!body.weitere.trim()){st.textContent='Bitte mindestens einen Empfänger wählen.';return}
      b.disabled=true;st.textContent='Wird gesendet …';
      try{const r=await jpost(`/api/einsatz/${encodeURIComponent(e.id)}/senden`,body);closeSheet();
        toast(r.status==='versendet'?'Versendet an '+r.an.join(', '):'Im Postausgang im Büro: '+(r.fehler||'nicht versendet'))}
      catch(err){st.textContent=err.netz?'Keine Verbindung zum Büro. Bitte später noch einmal.':err.message;b.disabled=false}}})}
async function dokTeilen(b,e){const st=document.getElementById('dok-st');const say=t=>{if(st)st.textContent=t};
  if(b.dataset.ready&&b._files){const fs=b._files;
    try{if(navigator.canShare&&navigator.canShare({files:fs})){await navigator.share({files:fs,title:e.einsatzort});return}}catch(err){if(err&&err.name==='AbortError')return}
    fs.forEach(f=>{const a=document.createElement('a');a.href=URL.createObjectURL(f);a.download=f.name;document.body.appendChild(a);a.click();a.remove()});return}
  const ids=b.dataset.ids.split(',');const docs=(e.docs||[]).filter(d=>ids.includes(d.id));
  b.disabled=true;say('Wird geladen …');
  try{const fs=[];for(const d of docs){let r;try{r=await fetch('/api/dokument/'+encodeURIComponent(d.id),{credentials:'same-origin',cache:'no-store'})}catch(err){throw new Error('Keine Verbindung zum Büro')}
      if(r.status===401)throw new Error('Bitte neu anmelden');if(!r.ok)throw new Error(DOKNAME[d.art]+' nicht gefunden');
      fs.push(new File([await r.blob()],d.name,{type:'application/pdf'}))}
    const mb=fs.reduce((n,f)=>n+f.size,0)/1048576;
    b._files=fs;b.dataset.ready=1;b.classList.add('pri');b.textContent='📤 Jetzt teilen';say(`Bereit (${mb.toFixed(1).replace('.',',')} MB). Noch einmal antippen zum Teilen.`)}
  catch(err){say(err.message)}finally{b.disabled=false}}
"""

ERSETZUNGEN = [
    # Speicherschlüssel und save(): nach jedem Speichern Abgleich anstoßen
    ("const KEY='lst-baustelle-prototyp-v3';try{localStorage.removeItem('lst-baustelle-prototyp-v1');localStorage.removeItem('lst-baustelle-prototyp-v2')}catch(e){}", "const KEY='lst-baustelle-app-v1';"),
    ("function save(){try{localStorage.setItem(KEY,JSON.stringify(S))}catch(e){}}",
     "function save0(){try{localStorage.setItem(KEY,JSON.stringify(S))}catch(e){}}\n"
     "function save(){save0();zaehlen();chip();syncBald()}"),
    # Startzustand: leer, Daten kommen vom Server
    ("let S=load()||seed();S.reisetage=S.reisetage||[];",
     "function leer(){return {v:1,user:null,mitarbeiter:[],leute:[],fahrzeuge:[],kompressoren:[],strahlgeraete:[],typen:{},"
     "strahlmittel:{ultrafein:'CaCO3 ultrafein',fein:'CaCO3 fein',sonst:'sonstiges'},checkliste:[],einsaetze:[],intern:[],"
     "maengel:[],tanken:[],reisetage:[],sync:{},weg:{},pins:{}}}\n"
     "let S=load()||leer();['reisetage','maengel','tanken','intern','einsaetze','fahrzeuge','kompressoren'].forEach(k=>{S[k]=S[k]||[]});S.pins=S.pins||{};S.weg=S.weg||{};"
     + SYNC),
    # Interne Aufträge: neue Detailansicht mit Arbeitsbericht (vIntern steht im SYNC-Teil)
    ("function vIntern(){const i=S.intern.find(x=>x.id===V.id)", "function vIntern_prototyp(){const i=S.intern.find(x=>x.id===V.id)"),
    ("case 'erledigt':{const i=S.intern.find(x=>x.id===V.id);i.status='erledigt';save();V={name:'start'};render();toast('Auftrag erledigt');break}",
     "case 'erledigt':internErledigt();break;"),
    # Interne Aufgaben (auch aus Notion): Reihenfolge, Termin optional, Status/Priorität/Projekt
    ("sort((a,b)=>a.datum.localeCompare(b.datum)).map(i=>`<button class=\"card\" data-a=\"intern\"",
     "sort(internSort).map(i=>`<button class=\"card\" data-a=\"intern\""),
    ("<div class=\"meta\">${fD(i.datum)}${i.datum<today()?' · überfällig':''}</div>",
     "<div class=\"meta\"${i.datum&&i.datum<today()?' style=\"color:var(--danger)\"':''}>${esc(internMeta(i))}</div>"),
    ("<div class=\"k\">Erledigen am</div><div>${fDY(i.datum)}</div>",
     "<div class=\"k\">Erledigen am</div><div>${i.datum?fDY(i.datum):'ohne Termin'}</div>"
     "${i.nstatus?`<div class=\"k\">In Notion</div><div>${esc(i.nstatus)}${i.prio?' · Priorität '+esc(i.prio):''}</div>`:''}"
     "${i.projekt?`<div class=\"k\">Projekt</div><div>${esc(i.projekt)}</div>`:''}"),
    # Abgeschlossener Einsatz: Lieferschein und Fotodoku zum Teilen
    ("${e.lsHtml?`<div style=\"margin-bottom:24px;overflow-x:auto\">${e.lsHtml}</div>`:''}",
     "${dokLeiste(e)}${e.lsHtml?`<div style=\"margin-bottom:24px;overflow-x:auto\">${e.lsHtml}</div>`:''}"),
    ("case 'fteilen':teilenSheet(e);break;", "case 'fteilen':teilenSheet(e);break;\n  case 'dok':dokTeilen(b,e);break;\n  case 'dokzeigen':dokAnsicht(e,b.dataset.id);break;"),
    ("const me=()=>S.mitarbeiter.find(m=>m.id===S.user);",
     "const me=()=>S.mitarbeiter.find(m=>m.id===S.user)||S.ich||{id:S.user,name:'',rolle:'mitarbeiter'};"),
    ("hydrateImages();\n  if(V.name==='abschluss'&&ui.wiz===4)initSign();", "hydrateImages();chip();\n  if(V.name==='abschluss'&&ui.wiz===4)initSign();"),
    # Testfotos nie erzeugen
    ("async function ensureSeedPhotos(){", "async function ensureSeedPhotos(){return;"),
    # Fotos ohne lokale Datei (z. B. vom anderen iPad) vom Server laden
    ("function hydrateImages(){document.querySelectorAll('img[data-img]').forEach(async im=>{const u=await imgUrl(im.dataset.img);if(u)im.src=u})}",
     "function fotoEinsatz(id){const e=S.einsaetze.find(x=>(x.fotos||[]).some(f=>f.id===id)||(x.trash||[]).some(f=>f.id===id));return e?e.id:null}\n"
     "function hydrateImages(){document.querySelectorAll('img[data-img]').forEach(async im=>{const id=im.dataset.img;const u=await imgUrl(id);"
     "if(u){im.src=u;return}const eid=fotoEinsatz(id);if(eid)im.src=`/foto/${encodeURIComponent(eid)}/${encodeURIComponent(id)}?v=1`})}"),
    # Kopfzeile: Abgleichsstatus statt „Offline bereit“
    ('<span class="chip" title="Prototyp speichert nur auf diesem Gerät"><span class="dot"></span><span class="lbl">Offline bereit</span></span>',
     '<button class="chip" id="sync" data-a="sync" title=""><span class="dot"></span><span class="lbl">…</span></button>'),
    # Anmeldung
    ('<p class="muted">Prototyp, Stufe 1. Alle Daten sind Testdaten und bleiben auf diesem Gerät.</p>',
     '<p class="muted">Lüke-Strahl-Technik · Einsätze, Zeiten, Fotos und Lieferschein.</p>'),
    ("${S.mitarbeiter.map(m=>`<button class=\"pick${V.pickMa===m.id?' on':''}\" data-a=\"lgpick\" data-id=\"${m.id}\">${esc(m.name)}</button>`).join('')}",
     "${(S.leute&&S.leute.length?S.leute:S.mitarbeiter).map(m=>`<button class=\"pick${V.pickMa===m.id?' on':''}\" data-a=\"lgpick\" data-id=\"${m.id}\">${esc(m.name)}</button>`).join('')||'<span class=\"small muted\">Keine Verbindung zum Büro. Bitte WLAN oder Mobilfunk und Tailscale prüfen.</span>'}"),
    ('placeholder="Test-PIN 1234" autocomplete="off"', 'placeholder="PIN" autocomplete="off"'),
    ('<p class="small muted">Rollen im Test: Andy und Tina sind Admins, Matthias ist technisch verantwortlich, Faro und Arthur sind Mitarbeiter.</p>',
     '<p class="small muted">Einmal angemeldet, bleibt das iPad angemeldet. Ohne Netz geht die Anmeldung, wenn du dich auf diesem iPad schon einmal angemeldet hast.</p>'),
    ("case 'login':{const pin=$('#lg-pin').value;if(!V.pickMa){$('#lg-err').textContent='Bitte zuerst deinen Namen wählen.';return}if(pin!=='1234'){$('#lg-err').textContent='PIN stimmt nicht. Im Test ist sie 1234.';return}S.user=V.pickMa;save();V={name:'start'};render();break}",
     "case 'login':{const pin=$('#lg-pin').value;const err=t=>{$('#lg-err').textContent=t};if(!V.pickMa)return err('Bitte zuerst deinen Namen wählen.');if(!pin)return err('Bitte PIN eingeben.');"
     "b.disabled=true;try{const r=await jpost('/api/login',{wer:V.pickMa,pin});S.user=r.ich.id;S.ich=r.ich;S.pins[r.ich.id]=await pinPruefsumme(r.ich.id,pin);save0();V={name:'start'};render();holen()}"
     "catch(ex){if(ex.netz&&S.pins[V.pickMa]&&S.pins[V.pickMa]===await pinPruefsumme(V.pickMa,pin)){S.user=V.pickMa;save0();V={name:'start'};render();toast('Offline angemeldet. Alles wird später übertragen.')}"
     "else err(ex.netz?'Keine Verbindung zum Büro. Ohne Netz geht die Anmeldung nur, wenn du dich hier schon einmal angemeldet hast.':ex.message)}finally{b.disabled=false}break}"),
    ("case 'logout':S.user=null;save();V={name:'login'};render();break;",
     "case 'logout':{zaehlen();if(SY.offen&&!b.dataset.sure){b.dataset.sure=1;toast(`Noch ${SY.offen} nicht übertragen. Nochmal tippen zum Abmelden – die Daten bleiben auf dem iPad.`);break}"
     "S.user=null;save0();V={name:'login'};render();jpost('/api/logout',{}).catch(()=>{});leuteLaden();break}\n"
     "  case 'sync':holen();break;"),
    # Startseite: kein „Testdaten zurücksetzen“
    ('<button class="btn ghost" data-a="reset">Testdaten zurücksetzen</button>', ''),
    # Reisetag löschen: dem Server mitteilen
    ("S.reisetage=S.reisetage.filter(x=>x.id!==r.id);save();",
     "S.reisetage=S.reisetage.filter(x=>x.id!==r.id);(S.weg.reisetage=S.weg.reisetage||[]).push(r.id);save();"),
    # Abschluss: an den Server übergeben
    ("  save();V={name:'einsatz',id:e.id};ui.tab='heute';render();\n  sheet(`<h2>${sig.ohne?'Abgeschlossen ohne Unterschrift':'Lieferschein unterschrieben'}</h2><div class=\"stack small\">\n   <div class=\"callout ok\">Gespeichert. Im Echtbetrieb entsteht jetzt:</div>",
     "  e.sync_abschluss={datum:today(),name:sig.name||'',bild:sig.img||null,eingesehen:!!sig.eingesehen,zeiten_auf_ls:!!sig.zeiten,versand:!!sig.versand,"
     "spaeter:!!sig.spaeter,mail:sig.mail||'',ohne_kunde:!!sig.ohne};\n"
     "  save();V={name:'einsatz',id:e.id};ui.tab='heute';render();syncBald(100);\n"
     "  sheet(`<h2>${sig.ohne?'Abgeschlossen ohne Unterschrift':'Lieferschein unterschrieben'}</h2><div class=\"stack small\">\n"
     "   <div class=\"callout ok\">Gespeichert. Sobald Netz da ist, erstellt das Büro daraus:</div>"),
    # Start
    ("if(S.user)V={name:'start'};\nrender();\nopenDB().then(()=>{render();ensureSeedPhotos()});",
     "if(S.user)V={name:'start'};\nzaehlen();render();chip();\nleuteLaden();\nopenDB().then(()=>{render();holen()});\n"
     "if('serviceWorker' in navigator&&(location.protocol==='https:'||location.hostname==='localhost'))navigator.serviceWorker.register('/app/sw.js').catch(()=>{});"),
]

# CSS für den Abgleichs-Status
CSS = """.chip.ok .dot{background:var(--ok)}.chip.warn .dot{background:var(--warn)}.chip.err .dot{background:var(--danger)}
button.chip{cursor:pointer;font:inherit}
</style>"""


def bauen():
    s = QUELLE.read_text(encoding="utf-8")
    s = s[s.index("<title>"):]   # Kopf des Prototyps (charset, viewport) ersetzt KOPF
    for alt, neu in ERSETZUNGEN:
        n = s.count(alt)
        if n != 1:
            raise SystemExit(f"Ersetzung passt {n}-mal statt einmal:\n{alt[:160]}")
        s = s.replace(alt, neu)
    s = s.replace("</style>", CSS, 1)
    kopf_ende = s.index("</style>") + len("</style>")
    s = KOPF + s[:kopf_ende] + "\n</head>\n<body>\n" + s[kopf_ende:] + "\n</body>\n</html>\n"
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    ZIEL.write_text(s, encoding="utf-8")
    print("geschrieben:", ZIEL.relative_to(ROOT), len(s), "Zeichen")


if __name__ == "__main__":
    bauen()
