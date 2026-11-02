"""Conservative conference metadata extraction. No missing value is guessed."""
import re, hashlib
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

UNKNOWN = None
THEORY = re.compile(r'syntax|syntactic|morphosyntax|semantics|phonetics|phonology|prosod|morphology|linguistic theor|formal (?:approach|linguistic|description|model)|синтакс|семантик|фонетик|фонолог|просоди|морфолог|типологи|формальн',re.I)
EXCLUDE = re.compile(r'neuro(?:linguist|science)|нейролингв|нейронаук|natural language processing|\bNLP\b|\b(?:EACL|EMNLP|COLING|LREC)\b|computational|language technolog|large language model|language,? brain|machine translation|переводоведен|translation|cultural studies|culture and cognition|artistic cultures|popular culture|культуры и культурного|культуролог|социокультур|sociocultural|literatur|literary|литератур|TESOL|language teaching|language education|didacti|digital humanities|social XAI',re.I)

def clean(s):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'【\d+†([^】]+)】',lambda m:m[1].split('†')[0],s))).strip()

def key(title, start=None):
    title=re.sub(r'[^\w]+','',title.casefold())
    return title + ('-'+start[:4] if start else '')

def date(s):
    if not s:return None
    s=re.sub(r'(\d)(st|nd|rd|th)\b',r'\1',s.strip(),flags=re.I)
    for fmt in ['%d-%b-%Y','%d-%B-%Y','%B %d, %Y','%b %d, %Y','%Y-%m-%d','%d.%m.%Y']:
        try:return datetime.strptime(s,fmt).date().isoformat()
        except ValueError:pass
    return None

def classify(title, fields=''):
    excluded=bool(EXCLUDE.search(title))
    # A dedicated excluded field is outside the catalogue; mixed general meetings remain.
    fieldlist=[v.strip() for v in fields.split(';') if v.strip()]
    if fieldlist and all(EXCLUDE.search(v) for v in fieldlist):excluded=True
    primary=bool(THEORY.search(title)) or bool(THEORY.search(fields))
    mixed=bool(EXCLUDE.search(fields))
    return ('excluded' if excluded else 'theory' if primary and not mixed else 'mixed' if primary else 'other')

def base(title, source, start=None):
    return dict(id=hashlib.sha256(key(title,start).encode()).hexdigest()[:16],name=title,start=start,end=None,
      location=None,deadline=None,deadline_at=None,deadline_timezone=None,notification=None,notification_text=None,
      fee=None,topics=None,subject_languages=None,working_languages=None,speaker_restrictions=None,
      submission_rules=None,format=None,website=None,sources=[source],history=[],notes=[],
      classification='other',classification_reason=None,checked_at=None,verification='partial')

def parse_issue(text,url):
    text=re.sub(r'^L\d+: ?', '', text, flags=re.M)
    lines=[clean(x) for x in text.splitlines()]
    def field(label):
        for i,l in enumerate(lines):
            m=re.match(r'^(?:'+label+r')\s*:\s*(.*)',l,re.I)
            if m:return m[1] or (lines[i+1] if i+1<len(lines) else '')
        return None
    title=field('Full Title') or field('Subject')
    if not title:return None
    # The first Date is the posting date. Event dates explicitly contain a range.
    dr=next((re.match(r'^Date:\s*(\d{1,2}-[A-Za-z]+-\d{4})\s*-\s*(\d{1,2}-[A-Za-z]+-\d{4})',l) for l in lines if re.match(r'^Date:\s*\d{1,2}-[A-Za-z]+-\d{4}\s*-',l)),None)
    c=base(title,dict(type='linguistlist',url=url,label='LINGUIST List'),date(dr[1]) if dr else None)
    c['end']=date(dr[2]) if dr else None
    c['location']=field('Location');c['deadline']=date(field('Call Deadline|Submission Deadline|Abstract Deadline'))
    c['topics']=field(r'Linguistic Field\(s\)');c['subject_languages']=field(r'Subject Language\(s\)|Language Family\(ies\)')
    c['website']=field('Web Site|Meeting URL|Website')
    if c['website']:
        m=re.search(r'https?://[^\s†】]+',c['website']);c['website']=m[0] if m else None
    c['classification']=classify(title,c['topics'] or '')
    c['classification_reason']='По названию и рубрикам объявления; смешанные конференции отмечаются отдельно.'
    # Keep short, verbatim evidence fragments only when unambiguous. Never equate subject and working languages.
    body='\n'.join(lines)
    lang=field('Working languages?|Conference languages?|Languages of (?:the )?conference|Language of (?:the )?conference')
    c['working_languages']=lang[:250] if lang else None
    c['notes']=['Ограничения на авторов, языки выступлений и сбор не проверены полностью. Отсутствие сведений не означает отсутствие ограничений.']
    source_numbers=re.findall(r'/([0-9]+)',url)
    c['source_order']=[int(x) for x in source_numbers]
    notif=field('Notification of acceptance|Acceptance notification|Notification')
    if notif:
        c['notification']=date(notif);c['notification_text']=notif[:200]
    return c

class Page(HTMLParser):
    def __init__(self):super().__init__();self.chunks=[];self.links=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ['script','style']:self.skip+=1
        if tag in ['p','div','br','li','h1','h2','h3','h4','h5','h6','tr']:self.chunks.append('\n')
        if tag=='a' and attrs.get('href'):self.links.append(attrs['href'])
    def handle_endtag(self,tag):
        if tag in ['script','style']:self.skip=max(0,self.skip-1)
        if tag in ['p','div','li','h1','h2','h3','h4','h5','h6','tr']:self.chunks.append('\n')
    def handle_data(self,d):
        if not self.skip:self.chunks.append(d)
    def text(self):return '\n'.join(clean(x) for x in ''.join(self.chunks).splitlines() if clean(x))

def merge(old,new,observed):
    """Preserve unknown old values, history and records absent from a crawl."""
    out=dict(old)
    if old.get('source_order',[])>new.get('source_order',[]):
        out['sources']=list({s['url']:s for s in old.get('sources',[])+new.get('sources',[])}.values())
        return out
    before=old.get('deadline');after=new.get('deadline')
    history=list(old.get('history',[]))
    if before and after and before!=after:
        # An exact timestamp belonging to the old deadline is no longer valid.
        out['deadline_at']=new.get('deadline_at')
        event=dict(field='deadline',old=before,new=after,observed_at=observed,type='extended' if after>before else 'changed',source=new['sources'][0]['url'])
        if not any(h.get('old')==before and h.get('new')==after for h in history):history.append(event)
    for k,v in new.items():
        if v is not None and k not in ['id','history','sources']:out[k]=v
    out['sources']=list({s['url']:s for s in old.get('sources',[])+new.get('sources',[])}.values())
    out['history']=history
    return out
