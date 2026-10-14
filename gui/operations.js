'use strict';
(() => {
const el=id=>document.getElementById(id), size=50;
const states={PENDING:'等待',QUEUED:'排队',RUNNING:'处理中',COMPLETED:'完成',FAILED:'失败',CANCELLED:'取消',TIMEOUT:'超时',REVIEW_REQUIRED:'待复核',ACTIVE:'有效',EXPIRED:'过期',REVOKED:'撤销',SUPERSEDED:'已替换',RECEIVING:'接收中',PROCESSING:'处理中',INCOMPLETE:'视角不完整'};
const pages={history:0,trace:0,evidence:0,review:0,log:0}, totals={};
let historyQuery=new URLSearchParams(),selectedTask=null;
async function api(path,body){const response=await fetch('/api/v1'+path,{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const value=await response.json();if(!response.ok)throw new Error(`${value.message||'请求失败'} (${value.code||response.status})`);return value.data}
function action(fn){return async event=>{event?.preventDefault();el('notice').textContent='';try{await fn(event)}catch(error){el('notice').textContent=error.message}}}
function table(id,headers,rows){const box=document.createElement('table'),head=box.createTHead().insertRow();for(const title of headers){const th=document.createElement('th');th.textContent=title;head.append(th)}const body=box.createTBody();for(const values of rows){const tr=body.insertRow();for(const value of values){const td=tr.insertCell();if(value instanceof Node)td.append(value);else td.textContent=value??'—'}}el(id).replaceChildren(box);if(!rows.length){const text=document.createElement('p');text.textContent='当前页无记录。';el(id).append(text)}}
function link(text,fn){const button=document.createElement('button');button.type='button';button.textContent=text;button.onclick=action(fn);return button}
function pager(name,total){totals[name]=total;el(name+'-page').textContent=`第 ${pages[name]+1} 页 · 共 ${total} 条`;el(name+'-prev').disabled=pages[name]===0;el(name+'-next').disabled=(pages[name]+1)*size>=total}
function params(name){return `limit=${size}&offset=${pages[name]*size}`}
async function download(path,name){const response=await fetch('/api/v1'+path);if(!response.ok){const body=await response.json();throw new Error(body.message)}const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function health(){const value=await api('/health'),cards=el('health-cards');cards.replaceChildren();for(const [title,text] of [['Web 服务',value.web==='ok'?'服务可用':'状态异常'],['Stage1 AOI',value.stage1==='not_configured'?'真实模型未配置':value.stage1],['Stage2 EA/Bias',`${value.stage2} · 通道 ${(value.stage2_channels||[]).join(', ')}`],['Stage3 分级',value.stage3==='not_configured'?'真实模型未配置':value.stage3],['磁盘容量',value.resources.disk_capacity.status],['运行告警',(value.alerts||[]).join(' / ')||'无'],['AOI 队列',`${value.aoi_streams.queued_frames} 帧 / ${value.aoi_streams.active_streams} 条流`]]){const card=document.createElement('article');card.textContent=title;const b=document.createElement('b');b.textContent=text;card.append(b);cards.append(card)}}
async function history(){const query=new URLSearchParams(historyQuery);query.set('limit',size);query.set('offset',pages.history*size);const value=await api('/tasks?'+query);table('history-table',['任务编号','产品','阶段 / 状态','来源 / 通道','进度 / 当前文件','更新时间'],value.items.map(row=>[link(row.task_id,async()=>{selectedTask=row.task_id;el('task-detail').textContent=JSON.stringify(await api('/tasks/'+row.task_id+'?summary=1'),null,2)}),row.product_id,`${row.stage} / ${states[row.status]||row.status}`,`${row.source_id||'—'} / ${row.current_channel??'—'}`,`${row.progress}% / ${row.current_file||'—'}`,row.update_time]));pager('history',value.total)}
async function trace(){
    const product=el('trace-product').value.trim();if(!product)throw new Error('请填写产品编号');
    const value=await api('/products/'+encodeURIComponent(product)+'/trace?'+params('trace'));
    el('trace-result').replaceChildren();let total=0;
    const taskLog=id=>link(id,async()=>{document.querySelector('nav button[data-view="logs"]').click();el('log-type').value='task';el('log-id').value=id;pages.log=0;await logs()});
    const groups=[
        ['tasks','关联任务',['任务 / 日志','阶段','状态','开始','结束'],row=>[taskLog(row.task_id),row.stage,states[row.status]||row.status,row.start_time,row.finish_time]],
        ['aoi_products','AOI 产品',['产品','流 / 日志','状态','接收视角','缺失视角','更新时间'],row=>[row.product_id,taskLog(row.stream_id),states[row.status]||row.status,row.received_views.join(', '),row.missing_views.join(', ')||'无',row.updated_at]],
        ['qualifications','合格证据',['证据编号','状态','配方','登记人','有效期'],row=>[row.qualification_id,states[row.status]||row.status,`${row.recipe_id||'—'} / ${row.recipe_version||'—'}`,row.recorded_by,row.expires_at]],
        ['reviews','人工复核',['复核编号','决定','建议 / 最终等级','复核人','时间','理由'],row=>[row.review_id,row.decision,`${row.suggested_grade||'无'} / ${row.final_grade||'无'}`,row.reviewer,row.created_at,row.comment]]
    ];
    for(const [key,title,headers,map] of groups){
        const h=document.createElement('h2');h.textContent=`${title} · ${value[key].total} 条`;
        const box=document.createElement('div');box.id='trace-'+key;el('trace-result').append(h,box);
        table(box.id,headers,value[key].items.map(map));total=Math.max(total,value[key].total);
    }
    const h=document.createElement('h2');h.textContent='Stage2 实际预测摘要（每文件最多 3 行预览）';
    const box=document.createElement('div');box.id='trace-stage2-summary';el('trace-result').append(h,box);
    table(box.id,['任务 / 日志','通道','预测条数','模型 / 配方','EA / Bias 预览'],(value.stage2_results||[]).flatMap(task=>task.files.map(file=>[
        taskLog(task.task_id),file.channel,file.prediction_count,`${file.model_version||'—'} / ${file.recipe_version||'—'}`,
        file.preview.map(row=>`${row.ea} / ${row.bias}`).join('；')||'无真实预测'
    ])));
    pager('trace',total);
}
async function evidence(){const value=await api('/qualifications?product_id='+encodeURIComponent(el('evidence-product').value.trim())+'&'+params('evidence'));table('evidence-table',['证据编号','产品','状态','配方','复核身份','到期时间'],value.items.map(row=>[link(row.qualification_id,async()=>{el('evidence-id').value=row.qualification_id;await getEvidence()}),row.product_id,states[row.status]||row.status,`${row.recipe_id||'—'} / ${row.recipe_version||'—'}`,row.reviewer||row.recorded_by,row.expires_at]));pager('evidence',value.total)}
async function getEvidence(){const id=encodeURIComponent(el('evidence-id').value.trim());const [value,events]=await Promise.all([api('/qualifications/'+id),api('/qualifications/'+id+'/history?limit=50')]);el('evidence-detail').textContent=JSON.stringify({evidence:value,audit:events},null,2)}
async function reviews(){const value=await api('/reviews?product_id='+encodeURIComponent(el('review-product').value.trim())+'&'+params('review'));table('review-table',['复核编号','产品','决定','建议 / 最终等级','复核身份 / 时间','说明'],value.items.map(row=>[row.review_id,row.product_id,row.decision,`${row.suggested_grade||'无'} / ${row.final_grade||'无'}`,`${row.reviewer} / ${row.created_at}`,row.comment]));pager('review',value.total)}
async function logs(){const id=encodeURIComponent(el('log-id').value.trim()),path=el('log-type').value==='source'?'/inference/ea-bias/sources/'+id+'/logs':'/tasks/'+id+'/logs';const value=await api(path+'?'+params('log'));table('log-table',['时间','级别','阶段 / 产品 / 文件 / 通道','操作 / 进度','状态 / 错误','说明'],value.items.map(row=>[row.timestamp,row.level,`${row.stage_name||row.stage||'来源'} / ${row.product_id||'—'} / ${row.file_name||'—'} / ${row.channel??'—'}`,`${row.operation||'—'} / ${row.progress??'—'}%`,row.error_code||row.code||row.status,row.message]));pager('log',value.total)}
for(const button of document.querySelectorAll('nav button'))button.onclick=()=>{for(const view of document.querySelectorAll('.view'))view.hidden=view.id!==button.dataset.view;for(const tab of document.querySelectorAll('nav button'))tab.classList.toggle('active',tab===button);window.history.replaceState(null,'','#'+button.dataset.view)};
el('refresh-status').onclick=action(health);el('load-system-info').onclick=action(async()=>{el('system-info').textContent=JSON.stringify(await api('/system/info'),null,2)});
el('history-filter').onsubmit=action(async()=>{historyQuery=new URLSearchParams();for(const [key,value] of new FormData(el('history-filter'))){if(value)historyQuery.set(key,key.endsWith('_time')?new Date(value).toISOString():value)}pages.history=0;await history()});
el('trace-form').onsubmit=action(async()=>{pages.trace=0;await trace()});el('evidence-form').onsubmit=action(async()=>{pages.evidence=0;await evidence()});el('review-search').onsubmit=action(async()=>{pages.review=0;await reviews()});el('log-form').onsubmit=action(async()=>{pages.log=0;await logs()});
el('get-evidence').onclick=action(getEvidence);el('revoke-evidence').onclick=action(async()=>{await api('/qualifications/'+encodeURIComponent(el('evidence-id').value.trim())+'/revoke',{reason:el('evidence-reason').value.trim(),request_id:crypto.randomUUID()});await getEvidence();await evidence()});
el('replace-evidence').onclick=action(async()=>{const body=JSON.parse(el('replacement-payload').value);body.reason=el('evidence-reason').value.trim();body.request_id=crypto.randomUUID();const value=await api('/qualifications/'+encodeURIComponent(el('evidence-id').value.trim())+'/replace',body);el('evidence-id').value=value.qualification_id;await getEvidence();await evidence()});
el('review-form').onsubmit=action(async()=>{const body=Object.fromEntries(new FormData(el('review-form')));for(const field of ['qualification_id','stage3_task_id','final_grade'])body[field]=body[field]||null;body.request_id=crypto.randomUUID();const value=await api('/reviews',body);el('notice').textContent='复核记录已保存：'+value.review_id;el('review-product').value=body.product_id;pages.review=0;await reviews()});
el('export-history').onclick=action(()=>download('/exports/tasks.csv?'+historyQuery+'&'+params('history'),'tasks.csv'));el('export-trace').onclick=action(()=>download('/exports/products/'+encodeURIComponent(el('trace-product').value.trim())+'/trace.json?'+params('trace'),'trace.json'));el('export-evidence').onclick=action(()=>download('/exports/qualifications/'+encodeURIComponent(el('evidence-id').value.trim())+'.json','qualification.json'));
for(const [name,fn] of Object.entries({history,trace,evidence,review:reviews,log:logs})){for(const direction of ['prev','next'])el(name+'-'+direction).onclick=action(async()=>{pages[name]+=direction==='prev'?-1:1;await fn()})}
document.querySelector(`nav button[data-view="${['status','history','trace','evidence','reviews','logs'].includes(location.hash.slice(1))?location.hash.slice(1):'status'}"]`).click();
action(health)();document.documentElement.dataset.lianteOperationsReady='yes';
})();
