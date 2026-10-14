"""Identity and operation scoped durable request receipts.

Successful and deterministic 4xx responses are replayed. Capacity, storage and
model-unavailable responses remain retryable. Tasks are linked in their own commit
so a crash between task creation and response capture cannot create a duplicate.
"""
import hashlib
import json
import threading
import time
import sqlite3
from contextlib import closing
from flask import g, has_request_context

RETRYABLE = {429, 500, 502, 503, 504, 507}

def normalized(payload):
    def clean(value):
        if isinstance(value,dict):
            return {key:clean(item) for key,item in sorted(value.items()) if key!='request_id'}
        if isinstance(value,list): return [clean(item) for item in value]
        return value
    return hashlib.sha256(json.dumps(clean(payload),sort_keys=True,separators=(',',':'),
                                    ensure_ascii=False,allow_nan=False).encode()).hexdigest()

class IdempotencyStore:
    def __init__(self,index,max_receipts=100000):
        self.index=index; self.lock=threading.RLock(); self.max_receipts=max_receipts; self.domain_paths=[]
        with index.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS request_receipts (
                actor TEXT NOT NULL, operation TEXT NOT NULL, request_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, body TEXT NOT NULL, http_status INTEGER NOT NULL,
                task_id TEXT, created REAL NOT NULL, PRIMARY KEY(actor,operation,request_id))''')

    def attach_domain(self,path):
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('''CREATE TABLE IF NOT EXISTS request_receipts (
                actor TEXT NOT NULL, operation TEXT NOT NULL, request_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, body TEXT NOT NULL, http_status INTEGER NOT NULL,
                task_id TEXT, created REAL NOT NULL, PRIMARY KEY(actor,operation,request_id))''')
        self.domain_paths.append(path)

    def get(self,actor,operation,request_id):
        with self.index.connect() as db:
            row=db.execute('SELECT fingerprint,body,http_status,task_id FROM request_receipts WHERE actor=? AND operation=? AND request_id=?',
                           (actor,operation,request_id)).fetchone()
        if row is None:
            for path in self.domain_paths:
                with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)) as db:
                    row=db.execute('SELECT fingerprint,body,http_status,task_id FROM request_receipts WHERE actor=? AND operation=? AND request_id=?',
                                   (actor,operation,request_id)).fetchone()
                if row is not None: break
        return dict(fingerprint=row[0],body=json.loads(row[1]),status=row[2],task_id=row[3]) if row else None

    def save(self,actor,operation,request_id,fingerprint,body,status,task_id=None,db=None):
        args=(actor,operation,request_id,fingerprint,json.dumps(body,ensure_ascii=False,default=str),status,task_id,time.time())
        sql='''INSERT INTO request_receipts VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(actor,operation,request_id)
            DO UPDATE SET body=excluded.body,http_status=excluded.http_status,task_id=excluded.task_id'''
        if db is not None: db.execute(sql,args)
        else:
            with self.index.connect() as connection: connection.execute(sql,args)

    def capacity(self):
        with self.index.connect() as db:
            return db.execute('SELECT COUNT(*) FROM request_receipts').fetchone()[0] < self.max_receipts

def link_task_receipt(db,task):
    if has_request_context() and getattr(g,'idempotency_context',None):
        actor,operation,request_id,fingerprint,receipts=g.idempotency_context
        receipts.save(actor,operation,request_id,fingerprint,
            {'code':0,'message':'success','data':{'task_id':task['task_id'],
                'request_id':task['request_id'],'product_id':task['product_id'],'status':task['status']}},
            202,task['task_id'],db=db)

def link_domain_receipt(db,data,status):
    """Commit an object response in the object's transaction using the shared receipt schema."""
    if has_request_context() and getattr(g,'idempotency_context',None):
        actor,operation,request_id,fingerprint,receipts=g.idempotency_context
        receipts.save(actor,operation,request_id,fingerprint,
                      {'code':0,'message':'success','data':data},status,db=db)
