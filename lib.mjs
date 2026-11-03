export const normalize = s => (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu,'');
export function dayInMoscow(now = new Date()) {return new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);}
export function daysUntil(iso, now = new Date()) {return iso ? Math.round((Date.parse(iso+'T00:00:00Z')-Date.parse(dayInMoscow(now)+'T00:00:00Z'))/86400000) : null;}
export function deadlineStatus(c, now = new Date()) {if(c.submissions_open && c.submissions_open>dayInMoscow(now))return 'upcoming';if(c.deadline_at && Number.isFinite(Date.parse(c.deadline_at)))return Date.parse(c.deadline_at)<now.getTime()?'closed':'open';if(!c.deadline)return 'unknown';return daysUntil(c.deadline,now)<0?'closed':'open';}
export function eventStatus(c, now = new Date()) {const today=dayInMoscow(now);if(c.date_conflict||!c.start)return 'unknown';if((c.end||c.start)<today)return 'past';if(c.start<=today)return 'live';return 'future';}
export const isExtended = c => (c.history||[]).some(h=>h.field==='deadline' && (h.type==='extended' || (h.old && h.new>h.old)));
export function mergeCatalogues(lists, overrides = {}) {
  const all=new Map();
  for(const list of lists) for(const row of list) {
    const k=row.merge_key||normalize(row.name)+'|'+(row.start||'');
    if(!all.has(k)){all.set(k,{...row,sources:[...(row.sources||[])],history:[...(row.history||[])],notes:[...(row.notes||[])]});continue;}
    const prev=all.get(k);
    for(const [field,value] of Object.entries(row))if(value!==null && value!==undefined && !['id','sources','history','notes'].includes(field))prev[field]=value;
    prev.sources=[...new Map([...prev.sources,...(row.sources||[])].map(s=>[s.url,s])).values()];
    prev.history=[...prev.history,...(row.history||[])].filter((h,i,a)=>a.findIndex(x=>JSON.stringify(x)===JSON.stringify(h))===i);
    prev.notes=[...new Set([...prev.notes,...(row.notes||[])])];
  }
  return [...all.values()].map(c=>{
    const o={...(overrides[c.id]||{})};
    // A later successful source check must not be hidden by a frozen manual deadline.
    if(o._reviewed_at && c.checked_at>o._reviewed_at && c.deadline && o.deadline && c.deadline!==o.deadline){
      for(const k of ['deadline','deadline_at','deadline_timezone','history'])delete o[k];
    }
    return {...c,...o};
  }).filter(c=>c.classification!=='excluded');
}
export function filterCatalog(rows,s,saved,now=new Date()) {
 const q=(s.q||'').toLocaleLowerCase().trim();
 return rows.filter(c=>{
  const status=deadlineStatus(c,now),event=eventStatus(c,now),d=daysUntil(c.deadline,now);
  if(q && ![c.name,c.location,c.location_original,c.topics,c.topics_original,c.subject_languages,c.subject_languages_original,c.working_languages,c.conference_group?.label].filter(Boolean).join(' ').toLocaleLowerCase().includes(q))return false;
  if(s.source!=='all' && !(c.sources||[]).some(x=>x.type===s.source))return false;
  if(s.deadline!=='all' && status!==s.deadline)return false;
  if(s.chip==='theory' && c.classification!=='theory')return false;
  if(s.chip==='open' && (status!=='open'||event==='past'))return false;
  if(s.chip==='soon' && (status!=='open'||d===null||d>30||event==='past'))return false;
  if(s.chip==='extended' && !isExtended(c))return false;
  if(s.chip==='past' && event!=='past')return false;
  if(s.chip==='saved' && !saved.has(c.id))return false;
  return true;
 }).sort((a,b)=>{
  if(s.sort==='name')return a.name.localeCompare(b.name,'ru');
  if(s.sort==='start')return (a.start||'9999').localeCompare(b.start||'9999');
  if(s.sort==='checked')return (b.checked_at||'').localeCompare(a.checked_at||'');
  const rank=c=>eventStatus(c,now)==='past'?4:({open:0,upcoming:1,unknown:2,closed:3}[deadlineStatus(c,now)]);
  return rank(a)-rank(b) || (a.deadline||'9999').localeCompare(b.deadline||'9999') || a.name.localeCompare(b.name,'ru');
 });
}
export function csvCell(value) {let s=String(value??'Неизвестно');if(/^[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}
export function icsText(c,now=new Date()) {
 const escape=s=>String(s||'').replace(/\\/g,'\\\\').replace(/\n/g,'\\n').replace(/,/g,'\\,').replace(/;/g,'\\;');
 const stamp=now.toISOString().replace(/[-:]/g,'').split('.')[0]+'Z';
 const events=[];
 const event=(uid,summary,start,end,description)=>{events.push('BEGIN:VEVENT',`UID:${uid}@interprole.github.io`,`DTSTAMP:${stamp}`,`DTSTART;VALUE=DATE:${start.replaceAll('-','')}`,`DTEND;VALUE=DATE:${end.replaceAll('-','')}`,`SUMMARY:${escape(summary)}`,`DESCRIPTION:${escape(description)}`,`LOCATION:${escape(c.location||'Неизвестно')}`,'END:VEVENT');};
 const next=d=>new Date(Date.parse(d+'T00:00:00Z')+86400000).toISOString().slice(0,10);
 const url=c.website||c.sources?.[0]?.url||'';
 if(c.deadline)event(c.id+'-deadline','Подача: '+c.name,c.deadline,next(c.deadline),'Дедлайн: '+c.deadline+'. Часовой пояс: '+(c.deadline_timezone||'неизвестно')+'. Сверьте время в источнике. '+url);
 if(c.start&&!c.date_conflict)event(c.id+'-event',c.name,c.start,next(c.end||c.start),url);
 // Fold UTF-8 lines at 73 bytes without splitting a Unicode code point (RFC 5545).
 return ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Conferences//Linguistics//RU','CALSCALE:GREGORIAN',...events,'END:VCALENDAR'].map(line=>{let parts=[],part='',bytes=0;for(const ch of line){let n=new TextEncoder().encode(ch).length;if(bytes+n>73){parts.push(part);part=' ';bytes=1;}part+=ch;bytes+=n;}parts.push(part);return parts.join('\r\n');}).join('\r\n')+'\r\n';
}
