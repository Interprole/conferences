import {test} from 'node:test';
import assert from 'node:assert/strict';
import {presentCatalogue,groupCatalogue,resolveTheme} from '../presentation.mjs';
import fs from 'node:fs';
import {dayInMoscow,daysUntil,deadlineStatus,eventStatus,isExtended,mergeCatalogues,filterCatalog,csvCell,icsText} from '../lib.mjs';

test('Moscow date changes at 21:00 UTC; civil deadlines close on the following date',()=>{
 const now=new Date('2026-11-01T21:30:00Z');assert.equal(dayInMoscow(now),'2026-11-02');
 assert.equal(daysUntil('2026-11-02',now),0);assert.equal(deadlineStatus({deadline:'2026-11-01'},now),'closed');
 assert.equal(deadlineStatus({deadline:'2026-11-02'},now),'open');
});
test('Exact deadlines respect the organizer timezone',()=>{
 const c={deadline:'2026-11-08',deadline_at:'2026-11-08T23:59:59-08:00'};
 assert.equal(deadlineStatus(c,new Date('2026-11-09T07:00:00Z')),'open');
 assert.equal(deadlineStatus(c,new Date('2026-11-09T08:00:00Z')),'closed');
});
test('Past deadlines and finished conferences stay in the catalogue',()=>{
 const rows=[{id:'a',name:'A',deadline:'2025-01-01',start:'2025-02-01',sources:[]}];
 assert.equal(filterCatalog(rows,{source:'all',deadline:'all',chip:'all',sort:'deadline'},new Set(),new Date('2026-11-02')).length,1);
 assert.equal(eventStatus(rows[0],new Date('2026-11-02')),'past');assert.equal(eventStatus({start:'2025-01-01',date_conflict:'conflict'},new Date('2026-11-02')),'unknown');
});
test('An extension and author requirements survive a cross-source merge',()=>{
 const a={id:'a',name:'A',start:'2027-01-01',merge_key:'same',sources:[{url:'https://a',type:'linguistlist'}],history:[]};
 const b={id:'b',name:'А',merge_key:'same',sources:[{url:'https://b',type:'mosling'}],history:[{field:'deadline',old:'2026-11-01',new:'2026-11-08',type:'extended'}],speaker_restrictions:'До 35 лет'};
 const [c]=mergeCatalogues([[a],[b]]);assert.equal(c.id,'a');assert.equal(c.sources.length,2);assert.equal(c.speaker_restrictions,'До 35 лет');assert.ok(isExtended(c));
});
test('Missing values are unknown and CSV formulas are inert',()=>{assert.equal(deadlineStatus({}),'unknown');assert.equal(csvCell(null),'"Неизвестно"');assert.equal(csvCell('=1+1'),'"\'=1+1"');});
test('A newer source deadline supersedes an earlier manual correction',()=>{
 const [c]=mergeCatalogues([[{id:'a',name:'A',deadline:'2026-11-12',checked_at:'2026-11-04'}]],{a:{deadline:'2026-11-08',_reviewed_at:'2026-11-02',deadline_at:'2026-11-08T23:59:00Z'}});
 assert.equal(c.deadline,'2026-11-12');assert.equal(c.deadline_at,undefined);
});
test('Calendar exports include exclusive end dates, stable IDs and UTF-8 folded lines',()=>{
 const c={id:'test',name:'Конференция '.repeat(15),start:'2026-11-02',end:'2026-11-04',deadline:'2026-10-30'};
 const ics=icsText(c,new Date('2026-11-02T00:00:00Z'));
 assert.ok(ics.includes('DTEND;VALUE=DATE:20261105'));assert.ok(ics.includes('UID:test-deadline@'));
 for(const line of ics.split('\r\n'))assert.ok(new TextEncoder().encode(line).length<=75);
 assert.equal((ics.match(/BEGIN:VEVENT/g)||[]).length,2);
});

test('Explicit workshop groups keep distinct deadlines and survive filtering without their parent',()=>{
 const raw=[{id:'a',name:'Conference',deadline:'2026-12-01'},{id:'b',name:'Workshop',deadline:'2026-11-01'},{id:'c',name:'Unrelated workshop',deadline:'2026-11-01'}];
 const editorial={groups:{event:{label:'Conference group',members:['a','b'],parent_id:'a'}}};
 const presented=presentCatalogue(raw,editorial),units=groupCatalogue([presented[1],presented[2],presented[0]]);
 assert.deepEqual(units.map(u=>u.rows.map(r=>r.id)),[['a','b'],['c']]);
 assert.deepEqual(units[0].rows.map(r=>r.deadline),['2026-12-01','2026-11-01']);
 assert.deepEqual(groupCatalogue([presented[1]])[0].rows.map(r=>r.id),['b']);
 assert.equal(raw[1].conference_group,undefined);
});
test('Russian presentation preserves unknown subject languages and source-language search',()=>{
 const raw=[{id:'a',name:'A',location:'Paris, France',topics:'Syntax',subject_languages:null,working_languages:'English'}];
 const [c]=presentCatalogue(raw,{locations:{'Paris, France':'Париж, Франция'},topics:{Syntax:'Синтаксис'}});
 assert.equal(c.location,'Париж, Франция');assert.equal(c.subject_languages,null);assert.equal(c.working_languages,'English');
 const state={q:'Paris',source:'all',deadline:'all',chip:'all',sort:'name'};
 assert.equal(filterCatalog([c],state,new Set()).length,1);
 assert.equal(filterCatalog([c],{...state,q:'Синтаксис'},new Set()).length,1);
});
test('System theme is the default, with explicit light and dark overrides',()=>{
 assert.equal(resolveTheme('system',true),'dark');assert.equal(resolveTheme('system',false),'light');
 assert.equal(resolveTheme(null,true),'dark');assert.equal(resolveTheme('light',true),'light');assert.equal(resolveTheme('dark',false),'dark');
});
test('Editorial groups reference existing stable IDs once and cover every current place',()=>{
 const data=name=>JSON.parse(fs.readFileSync(new URL('../data/'+name,import.meta.url)));
 const rows=mergeCatalogues([data('linguistlist.json'),data('mosling.json')],data('overrides.json'));
 const editorial=data('editorial.json'),ids=new Set(rows.map(c=>c.id)),seen=new Set();
 for(const group of Object.values(editorial.groups))for(const id of group.members){assert.ok(ids.has(id),id);assert.ok(!seen.has(id),id);seen.add(id);}
 for(const row of rows)if(row.location){assert.ok(editorial.locations[row.location],row.location);assert.ok(!/[A-Za-z]/.test(editorial.locations[row.location]),row.location);}
 for(const [id,review] of Object.entries(editorial.conferences)){assert.ok(ids.has(id));assert.ok(review.review_basis);}
});
