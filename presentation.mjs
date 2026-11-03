// Explicit editorial metadata only: no title matching or inferred relationships.
export function presentCatalogue(rows, editorial = {}) {
 const memberships = new Map();
 for (const [id, group] of Object.entries(editorial.groups || {})) {
  for (const member of group.members) memberships.set(member, {id, label:group.label, parent_id:group.parent_id});
 }
 return rows.map(c => {
  const review = editorial.conferences?.[c.id] || {};
  const topics = review.topics_summary || (c.topics ? c.topics.split(';').map(t => editorial.topics?.[t.trim()] || t.trim()).join('; ') : null);
  return {...c, location_original:c.location, topics_original:c.topics, subject_languages_original:c.subject_languages,
   location:editorial.locations?.[c.location] || c.location,
   topics, subject_languages:review.subject_languages_summary || editorial.subject_languages?.[c.subject_languages] || c.subject_languages,
   conference_group:memberships.get(c.id) || null};
 });
}

// Pagination operates on whole groups, so a workshop is never orphaned on another page.
export function groupCatalogue(rows) {
 const units=[], byGroup=new Map();
 for (const row of rows) {
  const group=row.conference_group;
  if (!group) {units.push({id:row.id,label:null,rows:[row]});continue;}
  let unit=byGroup.get(group.id);
  if (!unit) {unit={id:group.id,label:group.label,parent_id:group.parent_id,rows:[]};byGroup.set(group.id,unit);units.push(unit);}
  unit.rows.push(row);
 }
 for (const unit of units) if (unit.label && unit.parent_id) {
  const index=unit.rows.findIndex(r=>r.id===unit.parent_id);
  if(index>0)unit.rows.unshift(unit.rows.splice(index,1)[0]);
 }
 return units;
}

export function resolveTheme(preference, systemDark) {
 return preference==='dark' || (preference!=='light' && systemDark) ? 'dark' : 'light';
}

export function initTheme() {
 const media=window.matchMedia('(prefers-color-scheme: dark)'), control=document.querySelector('#theme');
 let preference='system';
 try {const stored=localStorage.getItem('ling-conferences-theme');if(['system','light','dark'].includes(stored))preference=stored;}catch{}
 const apply=()=>{document.documentElement.dataset.theme=resolveTheme(preference,media.matches);control.value=preference;};
 apply();media.addEventListener('change',apply);
 control.addEventListener('change',()=>{preference=control.value;try{localStorage.setItem('ling-conferences-theme',preference);}catch{}apply();});
}
