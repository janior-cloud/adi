"""A single self-contained page, served by the runtime itself.

Why the UI ships inside the server instead of a separate frontend build: the app
shell has to open a URL and be usable. One file, no build step, no node_modules,
and the same page works in Safari, in a WKWebView, and on a phone on the LAN.

It shows the two things that matter about this runtime — which provider answered,
and whether the answer was degraded — instead of hiding them.
"""

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>ADI</title>
<style>
  :root{
    --bg:#070b12; --card:#0d1622; --line:#152436; --fg:#e8f1ff; --dim:#8fa6c4;
    --dimmer:#5e7593; --ok:#5fd09a; --warn:#f2c55c; --bad:#ff7b72; --accent:#3f8ecb;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{
    background:var(--bg); color:var(--fg); display:flex; flex-direction:column;
    font:15px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  header{
    display:flex; align-items:center; gap:10px; padding:12px 18px;
    border-bottom:1px solid var(--line); background:#0a1018; flex:none;
  }
  .mark{width:24px;height:24px;border-radius:7px;background:linear-gradient(160deg,#0e1622,#060a11);
        display:grid;place-items:center;font:700 12px ui-monospace,Menlo,monospace;color:#cfe2f5;
        border:1px solid #1d3348}
  header h1{font-size:14px;margin:0;letter-spacing:.12em;font-weight:600}
  header .who{color:var(--dimmer);font-size:12px;margin-left:auto;display:flex;gap:8px;align-items:center}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--dimmer)}
  .dot.on{background:var(--ok)}
  #log{flex:1;overflow-y:auto;padding:22px 18px 8px;scroll-behavior:smooth}
  .msg{max-width:min(680px,88%);margin:0 0 16px;padding:11px 14px;border-radius:12px;white-space:pre-wrap;
       word-break:break-word}
  .me{margin-left:auto;background:#14263a;border:1px solid #1d3348}
  .ai{background:var(--card);border:1px solid var(--line)}
  .meta{display:block;margin-top:9px;font:11px ui-monospace,Menlo,monospace;color:var(--dimmer);letter-spacing:.04em}
  .meta .ok{color:var(--ok)} .meta .warn{color:var(--warn)} .meta .bad{color:var(--bad)}
  .sys{color:var(--bad);font-size:13px;text-align:center;margin:14px 0}
  form{display:flex;gap:10px;padding:14px 18px 18px;border-top:1px solid var(--line);background:#0a1018;flex:none}
  input[type=text]{
    flex:1;background:#0e1724;border:1px solid #1d3348;border-radius:10px;color:var(--fg);
    padding:11px 14px;font:inherit;outline:none
  }
  input[type=text]:focus{border-color:var(--accent)}
  button{
    background:#1b4a6b;color:#dbeaf8;border:1px solid #2b6d97;border-radius:10px;
    padding:0 18px;font:inherit;font-weight:600;cursor:pointer
  }
  button:hover{background:#20577c}
  button:disabled{opacity:.5;cursor:default}
  .hint{color:var(--dimmer);font-size:12px;padding:0 18px 10px;background:#0a1018}
</style></head>
<body>
<header>
  <div class="mark">A</div>
  <h1>ADI</h1>
  <div class="who"><span id="whoami">connecting</span><span class="dot" id="dot"></span></div>
</header>
<div id="log"></div>
<form id="f">
  <input id="q" type="text" autocomplete="off" placeholder="Ask me anything. Works with the network down.">
  <button id="send" type="submit">Send</button>
</form>
<div class="hint" id="hint">&nbsp;</div>
<script>
const log = document.getElementById('log');
const form = document.getElementById('f');
const input = document.getElementById('q');
const send = document.getElementById('send');
const whoami = document.getElementById('whoami');
const dot = document.getElementById('dot');
const hint = document.getElementById('hint');

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function add(cls, text){
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

async function health(){
  try{
    const r = await fetch('/api/health', {cache:'no-store'});
    const h = await r.json();
    dot.classList.add('on');
    whoami.textContent = 'v' + h.version;
    hint.textContent = 'runtime ' + location.host + '   \u00b7   chain: '
                     + (h.providers || []).join(' \u2192 ');
  }catch(e){
    dot.classList.remove('on');
    whoami.textContent = 'offline';
    hint.textContent = 'runtime unreachable';
  }
}
health(); setInterval(health, 8000);

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const text = input.value.trim();
  if(!text) return;
  input.value = '';
  send.disabled = true;
  add('me', text);
  const bubble = add('ai', '');
  const meta = document.createElement('span');
  meta.className = 'meta';
  bubble.appendChild(meta);
  let body = '', provider = '', degraded = false, errored = false;

  try{
    const resp = await fetch('/api/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: text})
    });
    if(!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while(true){
      const {value, done} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream:true});
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for(const block of parts){
        let kind = '', data = '';
        for(const line of block.split('\n')){
          if(line.startsWith('event:')) kind = line.slice(6).trim();
          else if(line.startsWith('data:')) data = line.slice(5).trim();
        }
        if(!data) continue;
        let ev2;
        try{ ev2 = JSON.parse(data); }catch(e){ continue; }
        if(kind === 'meta'){ provider = ev2.provider; degraded = ev2.degraded; }
        else if(kind === 'delta'){
          body += ev2.text;
          bubble.textContent = body;
          bubble.appendChild(meta);
          log.scrollTop = log.scrollHeight;
        }
        else if(kind === 'final'){
          if(!body){ body = ev2.reply; }
          provider = ev2.provider; degraded = ev2.degraded;
        }
        else if(kind === 'error'){
          errored = true;
          body = 'All providers are down: ' + (ev2.error || 'unknown');
        }
      }
    }
  }catch(err){
    errored = true;
    body = 'Request failed: ' + err.message;
  }

  bubble.textContent = body;
  if(errored){
    meta.innerHTML = '<span class="bad">no provider answered</span>';
  }else{
    const cls = degraded ? 'warn' : 'ok';
    meta.innerHTML = '<span class="' + cls + '">' + esc(provider || '?') + '</span>'
                   + (degraded ? '  \u00b7  degraded \u2014 answered on this machine' : '');
  }
  bubble.appendChild(meta);
  log.scrollTop = log.scrollHeight;
  send.disabled = false;
  input.focus();
});
input.focus();
</script>
</body></html>
"""
