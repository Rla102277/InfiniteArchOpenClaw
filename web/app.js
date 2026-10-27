let token = '';
let snapshot = {};
const $ = (id) => document.getElementById(id);
const node = (tag, text, cls) => {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
const clear=(el)=>el.replaceChildren();
const empty=(text)=>node('div',text,'empty');
function message(text){$('notice').textContent=text;setTimeout(()=>{if($('notice').textContent===text)$('notice').textContent='';},7000);}
async function getData(){const r=await fetch('/api/state');if(!r.ok)throw Error('Unable to read local HQ');snapshot=await r.json();render();}
async function post(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-HQ-Token':token},body:JSON.stringify(data)});const payload=await r.json();if(!r.ok)throw Error(payload.error||'Request failed');await getData();return payload.result;}
async function act(fn){try{await fn()}catch(e){message(e.message||String(e));}}
function render(){
 const {projects,pending,skills,tools,events}=snapshot;
 $('stat-projects').textContent=projects.length;$('stat-approvals').textContent=pending.length;
 $('stat-skills').textContent=skills.length;$('stat-agents').textContent=[tools.hermes,tools.codex].filter(Boolean).length+'/2';
 const pro=$('project-list');clear(pro);if(!projects.length)pro.append(empty('No projects yet. Start with an idea, not a technical specification.'));
 projects.forEach(p=>{const e=node('div',undefined,'entry'),c=node('div');c.append(node('h3',p.name),node('p',p.summary),node('small',`${p.stage} · ${p.feature_count} feature request(s)`));const a=node('div',undefined,'actions');const add=node('button','+ Feature','secondary');add.addEventListener('click',()=>{const d=$('feature-dialog');d.querySelector('[name="project"]').value=p.id;d.showModal();});const copy=node('button','Copy chat command','ghost');copy.addEventListener('click',()=>act(async()=>{await navigator.clipboard.writeText(`./hq chat --project ${p.id}`);message('Command copied. Paste it in Terminal from this repository.');}));a.append(add,copy);e.append(c,a);pro.append(e);});
 const rev=$('approval-list');clear(rev);if(!pending.length)rev.append(empty('No approval requests. Requests appear here only when an agent or operator records one.'));
 pending.forEach(r=>{const e=node('div',undefined,'entry'),c=node('div');c.append(node('h3',r.title),node('p',`${r.project_name} · ${r.ref}`),node('small',`Evidence: ${r.evidence}`));const a=node('div',undefined,'actions');for(const [label,decision] of [['Accept demo','accepted'],['Request changes','changes_requested']]){const b=node('button',label,decision==='accepted'?'primary':'secondary');b.addEventListener('click',()=>act(async()=>{if(!confirm(`${label} for ${r.title}? This records an owner's decision, but DOES NOT merge or deploy code.`))return;await post('/api/decide',{project:r.project,review_id:r.id,decision});message(`Recorded: ${label}. No code was merged or deployed.`);}));a.append(b);}e.append(c,a);rev.append(e);});
 const log=$('activity-list');clear(log);if(!events.length)log.append(empty('Activity will appear as projects and feature requests are created.'));
 events.slice(0,12).forEach(e=>{const el=node('div',undefined,'activity');el.append(node('small',`${e.project} · ${new Date(e.time).toLocaleString()}`),node('p',e.message));log.append(el);});
 const toolList=$('tool-list');clear(toolList);Object.entries(tools).forEach(([name,installed])=>{const e=node('div',undefined,'entry health');e.append(node('strong',name),node('span',installed?'Detected':'Not installed',installed?'pill':'pill missing'));toolList.append(e);});
 const skillList=$('skill-list');clear(skillList);skills.forEach(s=>skillList.append(node('span',s,'chip')));
}
function show(dialog){$(dialog).showModal();}
document.querySelectorAll('.cancel').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
$('new-project').addEventListener('click',()=>show('project-dialog'));
$('hero-create').addEventListener('click',()=>show('project-dialog'));
$('add-project').addEventListener('click',()=>show('project-dialog'));
$('refresh').addEventListener('click',()=>act(getData));
$('project-form').addEventListener('submit',(e)=>{e.preventDefault();act(async()=>{const fd=new FormData(e.target);const p=await post('/api/projects',{name:fd.get('name'),idea:fd.get('idea')});e.target.reset();$('project-dialog').close();message(`Project created. In Terminal run: ./hq chat --project ${p.id}`);});});
$('feature-form').addEventListener('submit',(e)=>{e.preventDefault();act(async()=>{const fd=new FormData(e.target);await post('/api/features',{project:fd.get('project'),title:fd.get('title'),description:fd.get('description')});e.target.reset();$('feature-dialog').close();message('Feature request saved to the project. Ask Hermes to plan the next slice.');});});
(async()=>{try{const r=await fetch('/api/csrf');token=(await r.json()).token;await getData();}catch(e){message('Could not connect to the local HQ service.');}})();
