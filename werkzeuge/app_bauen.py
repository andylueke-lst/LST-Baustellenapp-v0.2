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
function fuerServer(e){const c=JSON.parse(JSON.stringify(e));delete c.lsHtml;delete c.sync_abschluss;delete c.serverDocs;
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
        delete e.sync_abschluss;e.serverDocs={lieferschein:r.lieferschein,fotodoku:r.fotodoku};save0();
        toast('Lieferschein und Fotodoku sind im Büro angekommen');if(V.id===e.id)render()}
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
    else{if(se.ls&&!le.ls)le.ls=se.ls;if(se.status==='abgeschlossen'&&!le.sync_abschluss){le.status='abgeschlossen';le.abschluss=se.abschluss}neu.push(le)}}
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
"""

ERSETZUNGEN = [
    # Speicherschlüssel und save(): nach jedem Speichern Abgleich anstoßen
    ("const KEY='lst-baustelle-prototyp-v2';try{localStorage.removeItem('lst-baustelle-prototyp-v1')}catch(e){}", "const KEY='lst-baustelle-app-v1';"),
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
