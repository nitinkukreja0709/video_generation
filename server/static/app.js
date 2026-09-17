"use strict";
let currentStory = null;
let pollTimer = null;

const $ = (id) => document.getElementById(id);

function esc(s){ const d=document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }
function statusColor(s){ return (['ok','done','skip'].includes(s)) ? 'ok' : (s==='error'?'error':(s==='warn'||s==='retry'?'warn':'running')); }
const ICON = {start:'▶', ok:'✓', skip:'–', error:'✗', retry:'↻', warn:'!', done:'✓'};

async function api(url, opts){
  const r = await fetch(url, opts);
  if(!r.ok) throw new Error(r.status + ' ' + (await r.text()));
  return r.json();
}

async function loadStories(selectName){
  const list = await api('/api/stories');
  const sel = $('story-picker');
  sel.innerHTML = '';
  if(!list.length){ sel.innerHTML = '<option value="">no stories</option>'; return; }
  list.forEach(s=>{
    const o=document.createElement('option');
    o.value=s.name; o.textContent=`${s.name} (${s.scenes} scenes · ${s.vid})`;
    sel.appendChild(o);
  });
  if(selectName && list.some(s=>s.name===selectName)) sel.value = selectName;
  await loadStory(sel.value);
}

async function loadStory(name){
  currentStory = name;
  const r = await fetch(`/api/story/${name}`);
  $('story-json').value = await r.text();
  try{
    const cfg = JSON.parse($('story-json').value);
    populateScenes(cfg);
    if(cfg.meta && cfg.meta.vid) loadMedia(cfg.meta.vid);
  }catch(e){}
  refreshBoxes();
}

function populateScenes(cfg){
  const sel = $('test-scene'); sel.innerHTML = '<option value="">— full run —</option>';
  (cfg.scenes||[]).forEach(s=>{
    const o=document.createElement('option');
    o.value=s.id; o.textContent = s.id + (s.narration?`: ${s.narration.slice(0,36)}`:'');
    sel.appendChild(o);
  });
}

function newStory(){
  const name = prompt('Story filename (must end .json)');
  if(!name || !name.endsWith('.json')) return;
  const vid = name.replace(/\.json$/,'');
  const cfg = {
    meta: {vid, fps:16, clip_frames:81, voice:'hi-IN-MadhurNeural', voice_volume:1.0, music_volume:0.32},
    boxes: {
      promax:{name:'promax', base:'http://100.81.202.86:8188'},
      spark2:{name:'spark2', base:'http://100.108.126.6:8188'}
    },
    character: {physical:'', identity:''},
    style: {global_prompt:'', negative_prompt:''},
    scenes: [{id:'s01', visual:'', cam:'static shot', narration:''}]
  };
  api(`/api/story/${name}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(cfg)})
    .then(d=>{ currentStory = d.ok ? name : currentStory; return loadStories(name); })
    .catch(e=>alert('save failed: '+e.message));
}

async function saveStory(){
  let cfg;
  try{ cfg = JSON.parse($('story-json').value); }
  catch(e){ $('story-status').textContent = 'invalid json — not saved'; return; }
  try{
    const d = await api(`/api/story/${currentStory}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(cfg)});
    $('story-status').textContent = d.ok ? 'saved ✓' : 'error: '+JSON.stringify(d.error);
    populateScenes(cfg);
    setTimeout(()=>{ $('story-status').textContent=''; }, 2500);
  }catch(e){ $('story-status').textContent = 'save failed: '+e.message; }
}

async function refreshBoxes(){
  const host = $('boxhealth');
  host.innerHTML = 'probing…';
  try{
    const q = currentStory ? ('?story=' + encodeURIComponent(currentStory)) : '';
    const d = await api('/api/boxes' + q);
    host.innerHTML = d.boxes.map(b=>{
      const c = b.status==='ok' ? 'var(--ok)' : 'var(--err)';
      return `<span style="color:${c}" title="${esc(b.base)}">● ${esc(b.name)} ${b.status==='ok'?esc(b.detail):'(down)'}</span>`;
    }).join(' ') + ' <button onclick="refreshBoxes()">↻</button>';
  }catch(e){ host.innerHTML = 'probe failed <button onclick="refreshBoxes()">↻</button>'; }
}

async function runPipeline(){
  const stages = [...document.querySelectorAll('#stage-checks input:checked')].map(c=>c.value);
  const testScene = $('test-scene').value;
  if(!stages.length){ alert('pick at least one stage'); return; }
  let cfg;
  try{ cfg = JSON.parse($('story-json').value); }catch(e){ alert('story JSON invalid'); return; }
  $('run-note').textContent = 'starting…';
  try{
    const d = await api('/api/run', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({story:currentStory, stages, test_scene: testScene || undefined})});
    if(d.error){ $('run-note').textContent = 'error: ' + (Array.isArray(d.error)?d.error.join('; '):d.error); return; }
    $('run-note').textContent = `job ${d.job_id} started`;
    $('btn-stop').disabled = false;
    startPolling(d.job_id);
  }catch(e){ $('run-note').textContent = 'error: '+e.message; }
}

async function stopJob(){
  const jobs = await api('/api/jobs');
  const running = jobs.find(j=>j.status==='running' || j.status==='queued');
  if(!running){ $('btn-stop').disabled = true; return; }
  await api(`/api/jobs/${running.job_id}/stop`, {method:'POST'});
  $('run-note').textContent = 'stop requested…';
}

function startPolling(jobId){
  if(pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(()=>pollJob(jobId), 1500);
  pollJob(jobId);
}

async function pollJob(jobId){
  let job;
  try{ job = await api(`/api/jobs/${jobId}`); }catch(e){ return; }
  renderJob(job);
  if(['done','error'].includes(job.status)){
    clearInterval(pollTimer); pollTimer = null;
    $('btn-stop').disabled = true;
    if(job.vid) loadMedia(job.vid);
  }
}

function renderJob(job){
  const pill = $('job-status');
  pill.className = 'pill ' + (job.status==='running' ? 'running' : doneLabel(job.status));
  pill.textContent = job.status + (job.test_scene ? ` [test:${job.test_scene}]` : '');

  const st = $('job-stages'); st.innerHTML = '';
  const stages = job.stages || {};
  const keys = Object.keys(stages);
  keys.forEach(name=>{
    const v = stages[name];
    const el = document.createElement('span');
    el.className = 's ' + (v.status==='start' ? 'running' : statusColor(v.status));
    el.textContent = `${name}${v.msg?` · ${v.msg}`:''}`;
    st.appendChild(el);
  });
  if(!keys.length) st.innerHTML = '<span class="muted">waiting for first stage…</span>';

  const log = $('job-log');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.innerHTML = (job.log||[]).map(fmtLog).join('\n') || '— no log yet —';
  if(job.error){ log.innerHTML += `<div class="error">job error: ${esc(job.error)}</div>`; }
  if(atBottom || !job.log?.length) log.scrollTop = log.scrollHeight;
}

function doneLabel(s){ return s==='done' ? 'done' : (s==='error' ? 'error' : 'idle'); }

function fmtLog(l){
  const time = new Date(l.at*1000).toTimeString().slice(0,8);
  const cls = statusColor(l.status);
  const sc = l.scene ? `${esc(l.scene)} ` : '';
  return `<span class="t">${time}</span> <span class="${cls}">${esc(ICON[l.status]||'·')}</span> ${sc}${esc(l.msg||'')}`;
}

async function loadMedia(vid){
  let id = vid;
  if(!id){
    try{ const jobs = await api('/api/jobs'); const j = jobs.find(x=>x.vid && x.status==='done'); id = j && j.vid; }
    catch(e){ return; }
  }
  if(!id) return;
  let m;
  try{ m = await api(`/api/medias/${encodeURIComponent(id)}`); }catch(e){ return; }

  if(m.final){
    $('final-vid').innerHTML = `<video controls src="/media/${encodeURIComponent(id)}/${m.final}"></video>`;
  } else if(m.clips && m.clips.length){
    $('final-vid').innerHTML = `<div class="placeholder">no final render yet — ${m.clips.length} clip(s) available</div>`;
  }

  const sb = $('sb-grid'); sb.innerHTML = '';
  if(m.storyboard && m.storyboard.length){
    m.storyboard.forEach(f=>{
      const el=document.createElement('div'); el.className='t';
      el.innerHTML = `<img src="/media/${encodeURIComponent(id)}/storyboard/${f}" loading="lazy"><div class="cap">${esc(f)}</div>`;
      sb.appendChild(el);
    });
  } else sb.innerHTML = '<div class="placeholder">no storyboards yet</div>';

  const cl = $('clip-grid'); cl.innerHTML = '';
  if(m.clips && m.clips.length){
    m.clips.sort().forEach(f=>{
      const el=document.createElement('div'); el.className='t';
      el.innerHTML = `<video controls preload="metadata" src="/media/${encodeURIComponent(id)}/clips/${f}"></video><div class="cap">${esc(f)}</div>`;
      cl.appendChild(el);
    });
  } else cl.innerHTML = '<div class="placeholder">no clips yet</div>';
}

(async function init(){
  loadStories().catch(()=>{ $('story-picker').innerHTML='<option>failed to reach server</option>'; });
  try{
    const jobs = await api('/api/jobs');
    const active = jobs.find(j=>j.status==='running' || j.status==='queued');
    if(active){ startPolling(active.job_id); $('btn-stop').disabled=false; }
    else if(jobs.length){ renderJob(jobs[0]); if(jobs[0].vid) loadMedia(jobs[0].vid); }
  }catch(e){}
  setInterval(refreshBoxes, 30000);
})();