/* Stage2 folder discovery and paged prediction results. */
window.Stage2Folders = (() => {
    let manifest=null, busy=false, watchTask=null, showPrediction=null;
    let resultTask=null, results=[], page=0, pageRequest=0, sources=[];
    let pendingRequestId=null;
    const element=id=>document.getElementById(id);
    async function readBody(response){
        if(!response.headers.get('Content-Type')?.includes('application/json'))
            throw new Error(`服务暂不可用（HTTP ${response.status}），请稍后刷新。`);
        return response.json();
    }
    function tableInto(id, headings, rows) {
        const table=document.createElement('table');
        table.style.cssText='width:100%;border-collapse:collapse;font-size:13px;text-align:left;';
        const head=table.createTHead().insertRow();
        for(const title of headings){const th=document.createElement('th');th.textContent=title;th.style.padding='9px';head.append(th)}
        const body=table.createTBody();
        for(const values of rows){const row=body.insertRow();for(const value of values){const cell=row.insertCell();cell.textContent=value;cell.style.cssText='padding:9px;border-top:1px solid #334155;overflow-wrap:anywhere;'}}
        element(id).replaceChildren(table);
    }
    function payload(){const product=element('stage2-product').value.trim();return {source_id:element('stage2-source').value,recursive:element('stage2-recursive').checked,...(product?{product_id:product}:{})}}
    function updateButtons(){
        element('scan-folder').disabled=busy;
        element('stage2-source').disabled=busy;
        element('stage2-recursive').disabled=busy;
        element('run-folder').disabled=busy||!manifest?.total_files||Boolean(manifest?.invalid_files);
    }
    function showManifest(value){
        manifest=value;
        element('stage2-source-path').textContent=value.directory;
        element('stage2-scan-info').textContent=`识别 ${value.total_files} 个文件；${value.ready_files} 个模型就绪，${value.missing_model_files} 个缺少模型，${value.invalid_files} 个需处理。`;
        tableInto('stage2-input-files',['数据文件','通道','文件大小','识别结果'],value.files.map(item=>[item.file_name,item.channel===null?'未识别':'CH'+item.channel,(item.size_bytes/1024).toFixed(1)+' KB',item.message]));
        updateButtons();
    }
    async function loadSourceLogs(){
        const source=element('stage2-source').value;
        if(!source)return;
        try{
            const response=await fetch(`/api/v1/inference/ea-bias/sources/${encodeURIComponent(source)}/logs?limit=30`,{cache:'no-store'}),body=await readBody(response);
            if(!response.ok)throw new Error(body.message||'读取接入日志失败');
            if(source!==element('stage2-source').value)return;
            tableInto('stage2-source-logs',['时间','操作','结果 / 任务'],body.data.items.map(item=>[new Date(item.timestamp).toLocaleString(),item.operation,item.code===0?(item.task_id||'成功'):(item.message||item.code)]));
        }catch(err){element('stage2-source-logs').textContent=err.message;}
    }
    function showSource(){
        const source=sources.find(item=>item.source_id===element('stage2-source').value);
        element('stage2-source-meta').textContent=source?`来源 ${source.source_id} · 配置版本 ${source.revision} · ${source.owner_device_id||'管理员来源'}`:'暂无数据源';
        element('stage2-mes-endpoint').textContent=`登记目录：POST ${location.origin}/api/v1/inference/ea-bias/sources\n触发预测：POST ${location.origin}/api/v1/inference/ea-bias/folder`;
    }
    async function loadSources(){
        try{
            const response=await fetch('/api/v1/inference/ea-bias/sources',{cache:'no-store'}),body=await readBody(response);
            if(!response.ok)throw new Error(body.message||'无法读取数据源');
            sources=body.data.sources;
            element('stage2-source').replaceChildren(...body.data.sources.map(source=>{const option=document.createElement('option');option.value=source.source_id;option.textContent=source.source_id+' · '+source.directory+(source.exists?'':'（目录不存在）');return option}));
            if(!element('stage2-source').value)throw new Error('尚未配置输入文件夹');
            showSource();
            await scan();
        }catch(err){element('stage2-scan-info').textContent=err.message;}
    }
    async function scan(){
        if(busy)return;
        busy=true;manifest=null;updateButtons();
        element('stage2-scan-info').textContent='正在自动识别文件…';
        try{
            const response=await fetch('/api/v1/inference/ea-bias/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())}),body=await readBody(response);
            if(!response.ok)throw new Error(body.message||'识别失败');
            showManifest(body.data);
        }catch(err){element('stage2-input-files').textContent='暂无识别结果。';element('stage2-scan-info').textContent=err.message;}
        finally{busy=false;updateButtons();loadSourceLogs();}
    }
    async function predict(){
        if(busy)return;
        busy=true;updateButtons();element('loading').style.display='flex';
        try{
            pendingRequestId=pendingRequestId||crypto.randomUUID();
            const response=await fetch('/api/v1/inference/ea-bias/folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload(),request_id:pendingRequestId})}),body=await readBody(response);
            if(body.data?.files)showManifest(body.data);
            if(!response.ok)throw new Error(body.message||'无法创建文件夹预测任务');
            if(body.data.source)showManifest(body.data.source);
            watchTask('stage2',body.data.task_id);
            pendingRequestId=null;
        }catch(err){element('stage2-scan-info').textContent=err.message;}
        finally{element('loading').style.display='none';busy=false;updateButtons();loadSourceLogs();}
    }
    function renderResults(id,result){
        resultTask=id;results=(result.files||[]).filter(item=>item.status!=='FAILED');page=0;
        const completed=results.length, failed=(result.files||[]).length-completed;
        const rowCount=results.reduce((sum,item)=>sum+(item.prediction_count||0),0);
        element('stage2-result').textContent=`成功 ${completed} 项，失败 ${failed} 项，共 ${rowCount} 条预测。\n选择文件可分页查看完整逐行结果。`;
        tableInto('file-results',['数据文件','通道','状态','预测行数 / 原因'],(result.files||[]).map(item=>[item.source?.file_name||item.file_path.split(/[\\/]/).pop(),'CH'+item.channel,item.status==='FAILED'?'失败':'完成',item.error_message||String(item.prediction_count)]));
        element('stage2-result-file').replaceChildren(...results.map((item,index)=>{const option=document.createElement('option');option.value=String(index);option.textContent=(item.source?.file_name||item.file_path)+' · CH'+item.channel;return option}));
        loadPage();
    }
    async function loadPage(){
        const serial=++pageRequest, item=results[Number(element('stage2-result-file').value)];
        const info=element('stage2-page-info');
        element('stage2-prev').disabled=true;element('stage2-next').disabled=true;
        if(!item){info.textContent='暂无成功的预测结果。';element('stage2-prediction-rows').replaceChildren();return;}
        info.textContent='正在读取逐行预测…';
        try{
            const endpoint=item.predictions_endpoint||`/api/v1/tasks/${encodeURIComponent(resultTask)}/predictions?file_index=${item.file_index}&channel=${item.channel}`;
            const response=await fetch(endpoint+`&limit=100&offset=${page*100}`),body=await readBody(response);
            if(!response.ok)throw new Error(body.message||'读取结果失败');
            if(serial!==pageRequest)return;
            const {items,total}=body.data;
            tableInto('stage2-prediction-rows',['文件行号','EA','Bias'],items.map(row=>[row.row,row.ea.toFixed(4),row.bias.toFixed(4)]));
            info.textContent=`共 ${total} 行，当前 ${page*100+1}–${page*100+items.length} 行。`;
            element('stage2-prev').disabled=page===0;
            element('stage2-next').disabled=(page+1)*100>=total;
            if(items[0]){showPrediction(items[0]);element('stage2-preview-label').textContent=`${item.source?.file_name||item.file_path} · CH${item.channel} · 文件第 ${items[0].row} 行预览`;}
        }catch(err){if(serial===pageRequest)info.textContent=err.message;}
    }
    function initialize(watch,show){
        watchTask=watch;showPrediction=show;
        element('scan-folder').addEventListener('click',scan);
        element('run-folder').addEventListener('click',predict);
        element('stage2-source').addEventListener('change',()=>{showSource();scan()});
        element('stage2-refresh-source-logs').addEventListener('click',loadSourceLogs);
        element('stage2-recursive').addEventListener('change',scan);
        element('stage2-result-file').addEventListener('change',()=>{page=0;loadPage()});
        element('stage2-prev').addEventListener('click',()=>{page--;loadPage()});
        element('stage2-next').addEventListener('click',()=>{page++;loadPage()});
        loadSources();
    }
    return {initialize,renderResults};
})();
