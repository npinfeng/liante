"""Immutable, authenticated review decisions, independent of model availability."""
import json
import sqlite3
import uuid
from datetime import datetime,timezone
from schemas.qualification import ReviewRequest
from tasks.qualification_store import QualificationError

class ReviewStore:
    def __init__(self,qualifications,tasks):
        self.qualifications=qualifications;self.tasks=tasks;self.path=qualifications.path
        with self.qualifications.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS reviews (review_id TEXT PRIMARY KEY,product_id TEXT NOT NULL,
                qualification_id TEXT,stage3_task_id TEXT,created_at TEXT NOT NULL,entry_json TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS reviews_product ON reviews(product_id,created_at)')

    def create(self,payload,actor):
        value=ReviewRequest.model_validate(payload).model_dump(exclude={'request_id'})
        qualification=self.qualifications.get(value['qualification_id']) if value['qualification_id'] else None
        if value['qualification_id'] and (not qualification or qualification['product_id']!=value['product_id']):
            raise QualificationError('复核证据与产品不匹配','REVIEW_LINK_INVALID',409)
        task=self.tasks.get(value['stage3_task_id']) if value['stage3_task_id'] else None
        if value['stage3_task_id'] and (not task or task['stage']!='stage3' or task['product_id']!=value['product_id']):
            raise QualificationError('复核任务与产品不匹配','REVIEW_LINK_INVALID',409)
        result=(task or {}).get('result') or {}
        suggested=result.get('suggested_grade') or result.get('grade')
        if value['final_grade'] is not None and (suggested is None or not task or task['status'] not in {'COMPLETED','REVIEW_REQUIRED'}):
            raise QualificationError('没有真实分级结果时等级必须为空','MODEL_NOT_CONFIGURED',409)
        if value['decision']=='REJECT' and value['final_grade'] is not None:
            raise QualificationError('拒绝复核时不能填写最终等级','INVALID_ARGUMENT',400)
        if value['final_grade'] is not None:
            if not qualification:
                raise QualificationError('最终分级复核需要有效合格证据','QUALIFICATION_REQUIRED',409)
            self.qualifications.active(qualification['qualification_id'],value['product_id'])
        value.update(review_id=uuid.uuid4().hex,reviewer=actor,suggested_grade=suggested,
                     created_at=datetime.now(timezone.utc).isoformat())
        with self.qualifications.connect() as db:
            db.execute('INSERT INTO reviews VALUES (?,?,?,?,?,?)',(value['review_id'],value['product_id'],
                value['qualification_id'],value['stage3_task_id'],value['created_at'],json.dumps(value,ensure_ascii=False)))
            if qualification:
                self.qualifications.event(db,qualification['qualification_id'],actor,'review',value['comment'],
                                          review_id=value['review_id'],decision=value['decision'])
            from services.idempotency import link_domain_receipt
            link_domain_receipt(db,value,201)
        return value

    def get(self,review_id):
        with self.qualifications.connect() as db:
            row=db.execute('SELECT entry_json FROM reviews WHERE review_id=?',(review_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self,product_id=None,limit=100,offset=0):
        where=' WHERE product_id=?' if product_id else '';args=(product_id,) if product_id else ()
        with self.qualifications.connect() as db:
            total=db.execute('SELECT COUNT(*) FROM reviews'+where,args).fetchone()[0]
            rows=db.execute('SELECT entry_json FROM reviews'+where+' ORDER BY created_at DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return [json.loads(row[0]) for row in rows],total
