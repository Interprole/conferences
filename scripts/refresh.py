"""Refresh public LINGUIST List announcements; preserve the archive on errors."""
import argparse,json,time,re,sys
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urljoin,urlsplit
from datetime import datetime,timezone
from catalog import Page,parse_issue,merge,key

ROOT=Path(__file__).resolve().parents[1]
def load(path,default):
    try:return json.loads((ROOT/path).read_text())
    except FileNotFoundError:return default
def save(path,data):
    dest=ROOT/path;tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(dest)
def fetch(url):
    # Requests are sequential and rate limited; failures are recorded, never bypassed.
    time.sleep(.25)
    with urlopen(Request(url,headers={'User-Agent':'ConferenceDirectory/1.0 (+https://github.com/Interprole/conferences)'}),timeout=35) as r:
        if r.status!=200:raise RuntimeError('HTTP '+str(r.status))
        p=Page();p.feed(r.read().decode('utf-8'));return p

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--max-pages',type=int,default=100);ap.add_argument('--max-issues',type=int,default=1500);args=ap.parse_args()
    now=datetime.now(timezone.utc).isoformat();today=now[:10]
    old=load('data/linguistlist.json',[]);by={key(c['name'],c.get('start')):c for c in old};issues=set();errors=[];pages=0
    frontier=['https://linguistlist.org/conf/search/','https://linguistlist.org/issues/?topic=Calls&topic=Conferences'];seen=set()
    # Walk the entire conference catalogue and recent announcement pages (including extensions).
    while frontier and pages<args.max_pages:
        u=frontier.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            p=fetch(u);pages+=1
            for href in p.links:
                v=urljoin(u,href)
                if re.match(r'https://linguistlist.org/issues/\d+/\d+/?$',v):issues.add(v)
                if '/conf/search/' in v and v not in seen and v not in frontier:frontier.append(v)
                if '/issues/?' in v and 'page=' in v:
                    match=re.search(r'[?&]page=(\d+)',v)
                    if match and int(match[1])<=5 and v not in seen and v not in frontier:frontier.append(v)
        except Exception as e:errors.append({'url':u,'error':str(e)[:180]})
    if frontier:errors.append({'url':'catalogue','error':'Pagination limit reached; scan incomplete'})
    # Revisit every existing future event's posts even if an index no longer contains it.
    for c in old:
        if not c.get('end') or c['end']>=today:
            issues.update(s['url'] for s in c.get('sources',[]) if re.match(r'https://linguistlist.org/issues/\d+/\d+/?$',s['url']))
    success=0;consecutive_errors=0;pending={}
    if len(issues)>args.max_issues:errors.append({'url':'issues','error':'Issue limit reached; scan incomplete'})
    for u in sorted(issues,key=lambda x:[int(n) for n in re.findall(r'/([0-9]+)',x)],reverse=False)[:args.max_issues]:
        try:
            p=fetch(u);c=parse_issue(p.text(),u)
            if not c:raise ValueError('Conference fields could not be parsed')
            c['checked_at']=today;success+=1;consecutive_errors=0;k=key(c['name'],c.get('start'))
            # Existing conferences stay in the archive, even when their classification changes.
            if c['classification']=='excluded' and k not in by:continue
            pending[k]=merge(pending[k],c,today) if k in pending else c
        except Exception as e:
            errors.append({'url':u,'error':str(e)[:180]});consecutive_errors+=1
            if consecutive_errors>=6:
                errors.append({'url':'issues','error':'Stopped after six consecutive failures; remaining sources not checked'});break
    for k,c in pending.items():
        if k in by:
            result=merge(by[k],c,today)
            result['history']=list({json.dumps(h,sort_keys=True):h for h in by[k].get('history',[])+result.get('history',[])+c.get('history',[])}.values())
            by[k]=result
        else:by[k]=c
    if success:save('data/linguistlist.json',sorted(by.values(),key=lambda c:c['name'].casefold()))
    previous=load('data/linguistlist-status.json',{})
    state={'attempted_at':now,'last_success_at':now if success else previous.get('last_success_at'),'pages_read':pages,'issues_read':success,'complete':not errors,'errors':errors[:100],'error_count':len(errors)}
    save('data/linguistlist-status.json',state)
    print(json.dumps({'pages':pages,'issues':success,'retained':len(by),'errors':len(errors)}))
    # Preserve status.json and old records even after a failed collection.
    return 0 if success else 1
if __name__=='__main__':sys.exit(main())
