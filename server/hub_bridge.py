"""Authenticated login hub adapter; returns current QR images, never hub credentials."""
import base64
from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import json
import urllib.parse
import urllib.request

PNG = b'\x89PNG\r\n\x1a\n'

class HubSession:
    def __init__(self, base, credentials):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        user, password = credentials.split(':', 1)
        data = urllib.parse.urlencode({'u':user,'pw':password,'next':'/'}).encode()
        with self.opener.open(urllib.request.Request(base+'login',data=data), timeout=8) as r:
            if urllib.parse.urlparse(r.geturl()).path == '/login':
                raise ValueError('hub_login_failed')
    def get(self, path, binary=False):
        with self.opener.open(self.base+path.lstrip('/'), timeout=7) as r:
            data = r.read(2*1024*1024+1)
            if len(data)>2*1024*1024:raise ValueError('hub_response_too_large')
        if binary:return data
        return json.loads(data)


def collect_qrs(session):
    def image(path):
        data=session.get(path,binary=True)
        if not data.startswith(PNG):raise ValueError('invalid_qr_image')
        return base64.b64encode(data).decode('ascii')
    def douyin():
        d=session.get('api/douyin/qr-data')
        if d.get('content') and not d.get('logged_in'):
            try:d['image_base64']=image('api/douyin/qr')
            except Exception:pass
        return d
    def dianping():
        d=session.get('api/dianping/status')
        d['logged_in']=d.get('state')=='login_redirect_confirmed'
        if d.get('qr_ready') and not d['logged_in']:
            d['image_base64']=image('api/dianping/qr')
        return d
    def wechat():
        d=session.get('api/list');items=[]
        rows=[row for row in (d.get('items') or [])[:8] if isinstance(row,dict) and row.get('id') and row.get('left',0)>0]
        def enrich(row):
            qid=urllib.parse.quote(str(row['id']),safe='')
            item={k:row[k] for k in ('id','title','note','left') if k in row}
            try:
                raw=session.get('api/qr/'+qid+'.txt',binary=True)
                item['content']=raw.decode('utf-8')
                item['image_base64']=image('api/qr/'+qid+'.png')
            except Exception as e:item['error']=type(e).__name__
            return item
        with ThreadPoolExecutor(max_workers=4) as qr_pool:
            items=list(qr_pool.map(enrich,rows))
        return {'items':items}
    out={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks={k:pool.submit(fn) for k,fn in [('douyin',douyin),('dianping',dianping),('wechat',wechat)]}
        for k,task in tasks.items():
            try:out[k]=task.result()
            except Exception as e:out[k]={'error':type(e).__name__,'state':'unavailable'}
    return out
