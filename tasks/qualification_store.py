"""Server-side qualification evidence for Stage3 admission."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager,closing
from threading import RLock
from pydantic import ValidationError
from schemas.qualification import QualificationRequest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.frame_journal import FrameJournal
from tasks.inference_store import InferenceTaskStore
from config import QUALIFICATION_MAX_AGE_HOURS


class QualificationError(ValueError):
    def __init__(self,message,code='INVALID_QUALIFICATION',status=422):
        super().__init__(message);self.code=code;self.status=status


class QualificationStore:
    def __init__(self, output_dir: Path, tasks: InferenceTaskStore, frames: FrameJournal) -> None:
        self.path = Path(output_dir) / "qualifications.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.tasks = tasks
        self.frames = frames
        self.lock=RLock()
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS qualifications (
                qualification_id TEXT PRIMARY KEY, product_id TEXT NOT NULL,
                aoi_task_id TEXT NOT NULL, stage2_task_id TEXT NOT NULL,
                evidence_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS qualifications_product ON qualifications(product_id, created_at)")
            columns={row[1] for row in db.execute('PRAGMA table_info(qualifications)')}
            for name,definition in {'status':"TEXT NOT NULL DEFAULT 'ACTIVE'",'updated_at':'TEXT',
                                    'expires_at':'TEXT','reason':'TEXT','superseded_by':'TEXT',
                                    'owner_device_id':'TEXT'}.items():
                if name not in columns: db.execute(f'ALTER TABLE qualifications ADD COLUMN {name} {definition}')
            db.execute('CREATE TABLE IF NOT EXISTS qualification_events (id INTEGER PRIMARY KEY AUTOINCREMENT,qualification_id TEXT NOT NULL,event_json TEXT NOT NULL)')
            db.execute('UPDATE qualifications SET updated_at=created_at WHERE updated_at IS NULL')
            for qid,created in db.execute('SELECT qualification_id,created_at FROM qualifications WHERE expires_at IS NULL').fetchall():
                expiry=(datetime.fromisoformat(created)+timedelta(hours=QUALIFICATION_MAX_AGE_HOURS)).isoformat()
                db.execute('UPDATE qualifications SET expires_at=? WHERE qualification_id=?',(expiry,qid))
            db.execute('PRAGMA user_version=2')

    @contextmanager
    def connect(self):
        with self.lock,closing(sqlite3.connect(self.path,timeout=15)) as db:
            db.execute('PRAGMA synchronous=FULL')
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def expired(value):
        try:
            expiry=datetime.fromisoformat(value)
            return expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)
        except (TypeError,ValueError):
            return True

    def create(self, payload: dict, actor='local', replace_id=None, replacement_reason=None) -> dict:
        try: payload=QualificationRequest.model_validate(payload).model_dump(mode='json',exclude_none=True)
        except ValidationError as exc: raise QualificationError(exc.errors(include_url=False)[0]['msg']) from exc
        product_id = payload.get("product_id")
        if not isinstance(product_id, str) or not product_id.strip():
            raise QualificationError("product_id 必须为非空字符串")
        aoi_id, stage2_id = payload.get("aoi_task_id"), payload.get("stage2_task_id")
        aoi, stage2 = self.tasks.get(aoi_id), self.tasks.get(stage2_id)
        aoi_product=self.frames.product(aoi_id,product_id) if aoi else None
        is_stream=aoi and isinstance(aoi.get('result'),dict) and 'stream_id' in aoi['result']
        if not aoi or aoi['stage']!='stage1' or (is_stream and (not aoi_product or aoi_product['status']!='COMPLETED')) or (not is_stream and aoi['status']!='COMPLETED'):
            raise QualificationError("需要已完成的 Stage1 AOI 任务")
        if not stage2 or stage2["stage"] != "stage2" or stage2["status"] != "COMPLETED":
            raise QualificationError("需要已完成的 Stage2 任务")
        if stage2["product_id"] != product_id:
            raise QualificationError("Stage2 产品编号不匹配")
        now = datetime.now(timezone.utc)
        for name, task in (("AOI", aoi), ("Stage2", stage2)):
            try:
                completed = datetime.fromisoformat(aoi_product['updated_at'] if name=='AOI' and is_stream else task['finish_time'])
                if completed.tzinfo is None: raise ValueError('timezone missing')
            except (TypeError, ValueError):
                raise QualificationError(f"{name} 缺少可验证的完成时间") from None
            if completed > now + timedelta(minutes=5) or now - completed > timedelta(hours=QUALIFICATION_MAX_AGE_HOURS):
                raise QualificationError(f"{name} 结果已过期或完成时间异常")
        stage2_files = (stage2.get("result") or {}).get("files")
        if not isinstance(stage2_files, list) or not stage2_files or any(
            not isinstance(item, dict) or (item.get("source") or {}).get("row_count", 0) < 1
            for item in stage2_files):
            raise QualificationError("Stage2 缺少实际参数预测结果")
        recipe_versions = {item.get("recipe_version") for item in stage2_files}
        if len(recipe_versions) != 1:
            raise QualificationError("Stage2 模型配方版本不一致")
        recipe_version = recipe_versions.pop()
        if not recipe_version or payload["recipe_version"] != recipe_version:
            raise QualificationError("请求配方版本与 Stage2 结果不一致")
        for item in stage2_files:
            _,count=self.tasks.index.predictions(stage2_id,item.get('file_index',1),item.get('channel',0),1,0)
            if count<1 or count!=item.get('prediction_count') or count!=item['source']['row_count']:
                raise QualificationError('Stage2 实际预测行与结果摘要不一致')
        if aoi["product_id"] == product_id and isinstance(aoi.get("result"), dict) and "stream_id" not in aoi["result"]:
            outputs = [aoi["result"]]
            if any(output.get("qualified") is not True for output in outputs):
                raise QualificationError("该产品缺少 AOI 合格结果")
            frame_count = len(outputs)
            aoi_model_versions = {aoi["result"].get("model_version")} - {None}
            aoi_recipe_versions = {aoi["result"].get("recipe_version")} - {None}
        else:
            frame_count, failures, not_qualified = self.frames.qualification_counts(aoi_id, product_id)
            if failures or not_qualified:
                raise QualificationError("该产品存在失败的 AOI 帧")
            aoi_model_versions, aoi_recipe_versions = self.frames.qualification_versions(aoi_id, product_id)
        if not frame_count:
            raise QualificationError("该产品缺少 AOI 合格结果")
        if len(aoi_model_versions) > 1 or len(aoi_recipe_versions) > 1:
            raise QualificationError("该产品的 AOI 模型或配方版本不一致")
        if recipe_version and aoi_recipe_versions and recipe_version not in aoi_recipe_versions:
            raise QualificationError("AOI 与 Stage2 配方版本不一致")
        aoi_source_id = (aoi.get("result") or {}).get("source_id")
        if payload.get("source_id") and aoi_source_id and payload["source_id"] != aoi_source_id:
            raise QualificationError("AOI 设备来源与合格证据不匹配")
        optical = payload.get("optical_features")
        electrical = payload.get("electrical_features")
        tuning = payload.get("tuning_result")
        if not isinstance(optical, dict) or optical.get("qualified") is not True:
            raise QualificationError("光学合格证据无效")
        if not isinstance(electrical, dict) or electrical.get("qualified") is not True:
            raise QualificationError("电学合格证据无效")
        for name, features in (("光学", optical), ("电学", electrical)):
            measurements = [value for key, value in features.items()
                            if key != "qualified" and isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value)]
            if not measurements and not features.get('measurements'):
                raise QualificationError(f"{name}证据至少需要一项有限数值测量")
        if not isinstance(tuning, dict) or tuning.get("status") != "complete":
            raise QualificationError("调测完成证据无效")
        manual = payload.get("manual_review")
        if not isinstance(manual, dict) or manual.get("decision") != "qualified":
            raise QualificationError("需要人工合格确认记录")
        reviewer = actor
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise QualificationError("需要填写实际测量和复核人")
        try:
            measured_at = datetime.fromisoformat(manual["measured_at"])
            if measured_at.tzinfo is None:
                raise ValueError("timezone required")
        except (KeyError, TypeError, ValueError):
            raise QualificationError("人工测量时间必须为带时区的 ISO 8601 时间") from None
        if measured_at > now + timedelta(minutes=5) or now - measured_at > timedelta(hours=QUALIFICATION_MAX_AGE_HOURS):
            raise QualificationError("人工测量记录已过期或时间异常")
        notes = manual.get("notes")
        if not isinstance(notes, str) or not notes.strip():
            raise QualificationError("需要填写人工复核说明")
        evidence = dict(product_id=product_id, aoi_task_id=aoi_id, stage2_task_id=stage2_id,
                        aoi_result={"qualified": True, "frame_count": frame_count},
                        optical_features=optical, electrical_features=electrical,
                        tuning_result=tuning, tuning_parameters=payload.get('tuning_parameters'),
                        recipe_id=payload['recipe_id'],recipe_version=recipe_version,
                        stage1_finished_at=aoi_product['updated_at'] if is_stream else aoi['finish_time'], stage2_finished_at=stage2["finish_time"],
                        aoi_model_version=next(iter(aoi_model_versions), None),
                        aoi_recipe_version=next(iter(aoi_recipe_versions), None),
                        stage2_model_versions=sorted({item.get("model_version") for item in stage2_files
                                                      if item.get("model_version")}),
                        source_id=aoi_source_id, recorded_by=actor,operator=actor,reviewer=actor,
                        owner_device_id=stage2.get('owner_device_id'),notes=payload.get('notes',''),
                        manual_review=dict(decision="qualified", reviewer=reviewer.strip(),
                                           measured_at=measured_at.isoformat(), notes=notes.strip(),
                                           grading_basis=manual.get('grading_basis',''),
                                           customer_grade=manual.get('customer_grade')))
        measured=datetime.fromisoformat(evidence['manual_review']['measured_at'])
        expires_at=min(measured+timedelta(hours=QUALIFICATION_MAX_AGE_HOURS),
                       datetime.fromisoformat(evidence['stage1_finished_at'])+timedelta(hours=QUALIFICATION_MAX_AGE_HOURS),
                       datetime.fromisoformat(evidence['stage2_finished_at'])+timedelta(hours=QUALIFICATION_MAX_AGE_HOURS)).astimezone(timezone.utc).isoformat()
        return self._commit_evidence(evidence,expires_at,actor,replace_id,replacement_reason)

    def _commit_evidence(self,evidence,expires_at,actor,replace_id=None,replacement_reason=None):
        """Internal atomic lifecycle commit; public create validates real upstream evidence first."""
        qualification_id=uuid.uuid4().hex;created_at=datetime.now(timezone.utc).isoformat()
        product_id=evidence['product_id'];aoi_id=evidence['aoi_task_id'];stage2_id=evidence['stage2_task_id']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if replace_id:
                old=db.execute('SELECT product_id,status,expires_at FROM qualifications WHERE qualification_id=?',(replace_id,)).fetchone()
                if not old or old[0]!=product_id or old[1]!='ACTIVE' or self.expired(old[2]):
                    raise QualificationError('仅能替换同产品有效 ACTIVE 证据','QUALIFICATION_NOT_ACTIVE',409)
            db.execute("INSERT INTO qualifications(qualification_id,product_id,aoi_task_id,stage2_task_id,evidence_json,created_at,status,updated_at,expires_at,owner_device_id) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE',?,?,?)",
                       (qualification_id, product_id, aoi_id, stage2_id,
                        json.dumps(evidence, ensure_ascii=False, default=str), created_at,created_at,expires_at,evidence.get('owner_device_id')))
            self.event(db,qualification_id,actor,'create',evidence.get('notes',''))
            if replace_id:
                db.execute("UPDATE qualifications SET status='SUPERSEDED',superseded_by=?,updated_at=?,reason=? WHERE qualification_id=?",(qualification_id,created_at,replacement_reason,replace_id))
                self.event(db,replace_id,actor,'replace',replacement_reason,new_qualification_id=qualification_id)
            from services.idempotency import link_domain_receipt
            value={**evidence,'qualification_id':qualification_id,'created_at':created_at,'updated_at':created_at,
                   'expires_at':expires_at,'status':'ACTIVE','reason':None,'superseded_by':None}
            link_domain_receipt(db,value,201)
        return self.get(qualification_id)

    def get(self, qualification_id: str) -> dict | None:
        if not isinstance(qualification_id,str): return None
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            row = db.execute("SELECT * FROM qualifications WHERE qualification_id=?",
                             (qualification_id,)).fetchone()
            if not row: return None
            stamp=datetime.now(timezone.utc).isoformat()
            if row['status']=='ACTIVE' and self.expired(row['expires_at']):
                changed=db.execute("UPDATE qualifications SET status='EXPIRED',updated_at=?,reason='VALIDITY_EXPIRED' WHERE qualification_id=? AND status='ACTIVE'",(stamp,qualification_id)).rowcount
                if changed: self.event(db,qualification_id,'system','expire','VALIDITY_EXPIRED')
                row=db.execute('SELECT * FROM qualifications WHERE qualification_id=?',(qualification_id,)).fetchone()
        return {**json.loads(row['evidence_json']),**{k:row[k] for k in row.keys() if k!='evidence_json'}}

    @staticmethod
    def event(db,qid,actor,operation,reason,**values):
        entry=dict(qualification_id=qid,actor=actor,operation=operation,reason=reason,
                   timestamp=datetime.now(timezone.utc).isoformat(),**values)
        db.execute('INSERT INTO qualification_events(qualification_id,event_json) VALUES (?,?)',(qid,json.dumps(entry,ensure_ascii=False)))

    def revoke(self,qid,actor,reason):
        value=self.get(qid)
        if not value: raise QualificationError('合格证据不存在','QUALIFICATION_NOT_FOUND',404)
        if value['status']=='REVOKED': return {**value,'replayed':True}
        with self.connect() as db:
            changed=db.execute("UPDATE qualifications SET status='REVOKED',updated_at=?,reason=? WHERE qualification_id=? AND status IN ('ACTIVE','EXPIRED')",(datetime.now(timezone.utc).isoformat(),reason,qid)).rowcount
            if not changed: raise QualificationError('证据已被替换','QUALIFICATION_NOT_ACTIVE',409)
            self.event(db,qid,actor,'revoke',reason)
        return self.get(qid)

    def active(self,qid,product_id):
        value=self.get(qid)
        if not value or value['product_id']!=product_id:
            raise QualificationError('分级需要该产品的服务端合格证据编号','QUALIFICATION_REQUIRED')
        if value['status']!='ACTIVE':
            raise QualificationError('合格证据当前状态：'+value['status'],'QUALIFICATION_'+value['status'],409)
        # Revalidate upstream source of truth at use time without creating another record.
        aoi=self.tasks.get(value['aoi_task_id']); stage2=self.tasks.get(value['stage2_task_id'])
        is_stream=aoi and (aoi.get('result') or {}).get('stream_id')
        product=self.frames.product(value['aoi_task_id'],product_id) if is_stream else None
        if not aoi or not stage2 or stage2['status']!='COMPLETED' or (is_stream and (not product or product['status']!='COMPLETED')) or (not is_stream and aoi['status']!='COMPLETED'):
            raise QualificationError('前序证据已失效','QUALIFICATION_UPSTREAM_INVALID',409)
        if stage2['product_id']!=product_id or not is_stream and (aoi['product_id']!=product_id or (aoi.get('result') or {}).get('qualified') is not True):
            raise QualificationError('前序产品或 AOI 合格结果失效','QUALIFICATION_UPSTREAM_INVALID',409)
        files=(stage2.get('result') or {}).get('files') or []
        if not files or any(item.get('recipe_version')!=value.get('recipe_version') for item in files):
            raise QualificationError('前序配方版本失效','QUALIFICATION_UPSTREAM_INVALID',409)
        for item in files:
            _,count=self.tasks.index.predictions(stage2['task_id'],item.get('file_index',1),item.get('channel',0),1,0)
            if count<1 or count!=item.get('prediction_count'):
                raise QualificationError('前序预测记录失效','QUALIFICATION_UPSTREAM_INVALID',409)
        if is_stream:
            count,failed,unqualified=self.frames.qualification_counts(value['aoi_task_id'],product_id)
            if not count or failed or unqualified: raise QualificationError('AOI 合格证据失效','QUALIFICATION_UPSTREAM_INVALID',409)
        return value

    def history(self,product_id=None,owner=None,limit=100,offset=0):
        clauses=[];args=[]
        for key,v in (('product_id',product_id),('owner_device_id',owner)):
            if v is not None:clauses.append(key+'=?');args.append(v)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.connect() as db:
            total=db.execute('SELECT COUNT(*) FROM qualifications'+where,args).fetchone()[0]
            ids=[r[0] for r in db.execute('SELECT qualification_id FROM qualifications'+where+' ORDER BY created_at DESC LIMIT ? OFFSET ?',(*args,limit,offset))]
        return [self.get(qid) for qid in ids],total

    def events(self,qid,limit,offset):
        with self.connect() as db:
            total=db.execute('SELECT COUNT(*) FROM qualification_events WHERE qualification_id=?',(qid,)).fetchone()[0]
            rows=db.execute('SELECT event_json FROM qualification_events WHERE qualification_id=? ORDER BY id DESC LIMIT ? OFFSET ?',(qid,limit,offset)).fetchall()
        return [json.loads(r[0]) for r in rows],total
