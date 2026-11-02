import {test} from 'node:test';
import assert from 'node:assert/strict';
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
