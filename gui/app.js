
        document.documentElement.dataset.lianteScriptStarted = 'yes';
        if (window.lucide) lucide.createIcons();
        
        // Initialize Chart
        const ctx = document.getElementById('outputChart').getContext('2d');
        const chart = window.Chart ? new Chart(ctx, {
            type: 'bar',
            data: {
                labels: ['EA (Error)', 'BIAS (Shift)'],
                datasets: [{
                    label: 'Magnitude',
                    data: [0, 0],
                    backgroundColor: ['rgba(16, 185, 129, 0.6)', 'rgba(139, 92, 246, 0.6)'],
                    borderColor: ['#10b981', '#8b5cf6'],
                    borderWidth: 2,
                    borderRadius: 8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { color: '#94a3b8' }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: '#94a3b8' }
                    }
                },
                plugins: {
                    legend: { display: false }
                }
            }
        }) : {data:{datasets:[{data:[0,0]}]},update(){
            const canvas=ctx.canvas,rect=canvas.parentElement.getBoundingClientRect();
            canvas.width=Math.max(240,Math.round(rect.width));canvas.height=220;
            const w=canvas.width,h=canvas.height,values=this.data.datasets[0].data.map(Number);
            const scale=Math.max(1,...values.map(Math.abs)),zero=h/2;
            ctx.clearRect(0,0,w,h);ctx.strokeStyle='#475569';ctx.beginPath();ctx.moveTo(30,zero);ctx.lineTo(w-20,zero);ctx.stroke();
            values.forEach((value,index)=>{
                if(!Number.isFinite(value)) return;
                const x=w*(index?0.68:0.28),height=value/scale*(h/2-35);
                ctx.fillStyle=index?'#8b5cf6':'#10b981';ctx.fillRect(x-25,Math.min(zero,zero-height),50,Math.abs(height));
                ctx.fillStyle='#cbd5e1';ctx.font='14px sans-serif';ctx.textAlign='center';
                ctx.fillText(index?'BIAS':'EA',x,h-8);ctx.fillText(value.toFixed(4),x,height>=0?zero-height-8:zero-height+16);
            });
        },resize(){this.update();}};

        const stageTabs = [...document.querySelectorAll('.stage-tab')];
        function showStage(stage) {
            document.getElementById('app-main').dataset.activeStage = stage;
            for (const tab of stageTabs) {
                const active = tab.dataset.stage === stage;
                tab.classList.toggle('active', active);
                tab.setAttribute('aria-pressed', String(active));
            }
            for (const view of document.querySelectorAll('.stage-view')) {
                view.hidden = view.dataset.stageView !== stage;
            }
            history.replaceState(null, '', '#'+stage);
            window.scrollTo(0, 0);
            if (stage === 'stage2' && typeof chart.resize === 'function') {
                requestAnimationFrame(() => chart.resize());
            }
        }
        stageTabs.forEach(tab => tab.addEventListener('click', () => showStage(tab.dataset.stage)));
        const initialStage = ['stage1','stage2','stage3'].includes(location.hash.slice(1))
            ? location.hash.slice(1) : 'stage1';
        showStage(initialStage);

        async function loadStageHealth() {
            const ids = ['stage1','stage2','stage3'];
            try {
                const response = await fetch('/api/v1/health', {cache:'no-store'});
                if (!response.ok) throw new Error('健康检查失败');
                const health = (await response.json()).data;
                const label = status => ({ok:'模型已就绪', configured:'模型已配置',
                    not_configured:'模型未配置', model_missing:'模型文件缺失'})[status] || '状态待确认';
                document.getElementById('stage1-model-state').textContent = label(health.stage1);
                document.getElementById('stage2-model-state').textContent = health.stage2 === 'ok'
                    ? '模型已就绪 · CH'+(health.stage2_channels || []).join('/CH') : label(health.stage2);
                document.getElementById('stage3-model-state').textContent = label(health.stage3);
                const grading=document.getElementById('run-grading');
                grading.disabled=health.stage3!=='ok';
                grading.textContent=health.stage3==='ok'?'提交质量分级':'真实分级模型未配置，暂不可推理';
                document.getElementById('overall-status').textContent =
                    health.stage1==='ok' && health.stage2==='ok' && health.stage3==='ok'
                        ? '三阶段已就绪' : '服务可用 · 部分模型未就绪';
            } catch (err) {
                for (const id of ids) document.getElementById(id+'-model-state').textContent = '服务状态暂不可用';
                document.getElementById('overall-status').textContent = '服务状态不可用';
            }
        }
        loadStageHealth();

        function showPrediction(prediction) {
            document.getElementById('res-ea').innerText = prediction.ea.toFixed(4);
            document.getElementById('res-bias').innerText = prediction.bias.toFixed(4);
            chart.data.datasets[0].data = [prediction.ea, prediction.bias];
            chart.update();
        }

        const statusText = {PENDING:'等待中',QUEUED:'排队中',RUNNING:'处理中',COMPLETED:'已完成',FAILED:'处理失败',REVIEW_REQUIRED:'需要复核',CANCEL_REQUESTED:'正在取消',CANCELLED:'已取消',TIMEOUT:'已超时'};
        const taskTimers = {};
        let lastAOIFetched = 0;
        let aoiDisplayQueue = [];
        let aoiDisplaying = false;
        let aoiDisplayRunId = null;
        let aoiFrameNames = {};
        function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
        async function displayAOIFrames(id){
            if(aoiDisplaying)return;
            aoiDisplaying=true;
            try{
                while(aoiDisplayQueue.length){
                    if(aoiDisplayRunId!==id)break;
                    const frame=aoiDisplayQueue.shift();
                    const image=document.getElementById('stage1-preview-image');
                    await new Promise((resolve,reject)=>{
                        image.onload=resolve;image.onerror=()=>reject(new Error('原始图像读取失败'));
                        image.src=`/api/v1/aoi/streams/${encodeURIComponent(id)}/frames/${frame.sequence}/image`;
                    });
                    image.hidden=false;
                    document.querySelector('#stage1-preview .stage1-image-card:first-child span').hidden=true;
                    renderStage1Prediction(image,frame);
                    document.getElementById('stage1-sequence').textContent=`第 ${frame.sequence} 张`;
                    if(aoiFrameNames[frame.sequence])
                        document.getElementById('stage1-current-file').textContent=aoiFrameNames[frame.sequence];
                    const judgement=document.getElementById('stage1-judgement');
                    const qualified=frame.output?.qualified;
                    judgement.textContent=frame.status==='FAILED'?'处理失败':qualified===true?'合格':qualified===false?'发现缺陷':'未判定';
                    judgement.className=frame.status==='FAILED'||qualified===false?'ng':qualified===true?'ok':'';
                    const defects=Array.isArray(frame.output?.defects)?frame.output.defects:[];
                    document.getElementById('stage1-defect-types').textContent=defects.length
                        ? [...new Set(defects.map(item=>item.type||'未知'))].join('、') : '—';
                    document.getElementById('stage1-result').textContent=JSON.stringify(frame,null,2);
                    await delay(450);
                }
            }catch(err){document.getElementById('stage1-meta').textContent=err.message}
            finally{
                aoiDisplaying=false;
                if(aoiDisplayQueue.length && aoiDisplayRunId && aoiDisplayRunId!==id)
                    displayAOIFrames(aoiDisplayRunId);
            }
        }
        async function fetchAOIFrames(id){
            const response=await fetch(`/api/v1/aoi/streams/${encodeURIComponent(id)}/frames?limit=100&offset=${lastAOIFetched}`,{cache:'no-store'});
            const body=await response.json();
            if(!response.ok)throw new Error(body.message||'逐帧结果读取失败');
            for(const frame of body.data.items){
                if(!['COMPLETED','FAILED'].includes(frame.status))break;
                lastAOIFetched++;
                aoiDisplayQueue.push(frame);
            }
            if(aoiDisplayQueue.length)displayAOIFrames(id);
            return body.data.total>lastAOIFetched;
        }
        function renderStage1Prediction(image, frame) {
            const canvas=document.getElementById('stage1-prediction-canvas'), placeholder=document.getElementById('stage1-prediction-placeholder');
            const output=frame?.output&&typeof frame.output==='object'?frame.output:null;
            const defects=Array.isArray(output?.defects)?output.defects:[];
            const ready=Boolean(frame?.model_version)&&frame?.status==='COMPLETED';
            if(!image?.naturalWidth||!ready){canvas.hidden=true;placeholder.hidden=false;placeholder.textContent=ready?'暂无缺陷框':'真实 Stage1 模型未配置或当前帧尚无结果';return}
            canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.hidden=false;placeholder.hidden=true;
            const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);ctx.lineWidth=Math.max(3,Math.round(Math.min(canvas.width,canvas.height)/240));ctx.font=`${Math.max(14,Math.round(Math.min(canvas.width,canvas.height)/34))}px Microsoft YaHei`;
            for(const defect of defects){const box=Array.isArray(defect.bbox)?defect.bbox.map(Number):[];if(box.length!==4||box.some(Number.isNaN))continue;const [x1,y1,x2,y2]=box,w=Math.max(2,x2-x1),h=Math.max(2,y2-y1);ctx.strokeStyle='#ff405c';ctx.fillStyle='rgba(255,64,92,.16)';ctx.fillRect(x1,y1,w,h);ctx.strokeRect(x1,y1,w,h);const label=`${defect.type||'defect'} ${(Number(defect.confidence)*100).toFixed(1)}%`,tw=ctx.measureText(label).width+16;ctx.fillStyle='#ff405c';ctx.fillRect(x1,Math.max(0,y1-30),tw,30);ctx.fillStyle='#fff';ctx.fillText(label,x1+8,Math.max(21,y1-8))}
        }
        async function watchTask(stage, id) {
            if(stage==='stage1' && aoiDisplayRunId!==id){
                aoiDisplayRunId=id;lastAOIFetched=0;aoiDisplayQueue=[];aoiFrameNames={};
            }
            localStorage.setItem('liante-'+stage+'-task', id);
            if (taskTimers[stage]) clearTimeout(taskTimers[stage]);
            document.getElementById(stage+'-logs').textContent='';
            let logOffset=0, retryDelay=1500, resultLoaded=false;
            async function poll() {
                try {
                    let stage1MoreFrames=false;
                    const [taskResponse, logResponse] = await Promise.all([
                        fetch('/api/v1/tasks/'+encodeURIComponent(id)+(stage==='stage2'?'?summary=1':'')),
                        fetch('/api/v1/tasks/'+encodeURIComponent(id)+'/logs?limit=200&offset='+logOffset)]);
                    const taskBody = await taskResponse.json(), logBody = await logResponse.json();
                    if (!taskResponse.ok) throw new Error(taskBody.message);
                    const t = taskBody.data;
                    if(stage==='stage2' && ['COMPLETED','FAILED'].includes(t.status) && !resultLoaded){
                        const resultResponse=await fetch('/api/v1/tasks/'+encodeURIComponent(id)+'/result');
                        const resultBody=await resultResponse.json();
                        if(!resultResponse.ok && !resultBody.data?.result && t.status!=='FAILED') throw new Error(resultBody.message);
                        t.result=resultBody.data.result; resultLoaded=true;
                    }
                    const elapsedMs = Number.isFinite(t.duration_ms) ? t.duration_ms
                        : t.finish_time ? Date.parse(t.finish_time)-Date.parse(t.start_time)
                        : Date.now()-Date.parse(t.start_time);
                    const elapsed = (Math.max(0, elapsedMs)/1000).toFixed(1);
                    const lines = [`当前任务：${id}`,`产品编号：${t.product_id}`,`当前阶段：${t.stage_name}`,
                        `运行状态：${statusText[t.status]||t.status}`];
                    if (stage === 'stage2') lines.push(`当前文件：${t.current_file||'无'}`,
                        `文件进度：${t.file_index}/${t.file_total}`,`当前通道：${t.current_channel||'无'}`,
                        `通道进度：${t.channel_index}/${t.channel_total}`);
                    lines.push(`当前操作：${t.operation}`,`总体进度：${t.progress}%`,`运行时间：${elapsed} 秒`);
                    if (t.error_code) lines.push(`错误码：${t.error_code}`,`原因：${t.error_message}`);
                    if(stage!=='stage1')document.getElementById(stage+'-meta').textContent = lines.join('\n');
                    const logPanel = document.getElementById(stage+'-logs');
                    const newLogs=logBody.data?.items||[];
                    if(newLogs.length){
                        if(stage==='stage1')for(const entry of newLogs){
                            if(entry.operation==='folder_frame' && entry.frame_sequence && entry.file_name)
                                aoiFrameNames[entry.frame_sequence]=entry.file_name;
                        }
                        logPanel.textContent += (logPanel.textContent?'\n':'')+newLogs.map(e=>
                            `${e.timestamp} [${e.level}] ${e.stage_name} ${e.file_name||''} ${e.channel||''} ${e.operation} ${e.message}`).join('\n');
                        logOffset+=newLogs.length;
                        logPanel.textContent=logPanel.textContent.split('\n').slice(-500).join('\n');
                    }
                    logPanel.scrollTop = logPanel.scrollHeight;
                    if (stage==='stage1' && t.result?.stream_id) {
                        const stream = t.result;
                        document.getElementById('stage1-endpoint').textContent =
                            `设备推帧地址：POST ${location.origin}/api/v1/aoi/streams/${id}/frames；multipart 字段 image 与 sequence（从 1 连续递增），跨产品时每帧附带 product_id。队列满时按 Retry-After 重试同序号。`;
                        for (const [key,value] of Object.entries({accepted:stream.accepted,
                            processed:stream.processed,failed:stream.failed,rejected:stream.rejected})) {
                            document.getElementById('aoi-'+key).textContent = value;
                        }
                        document.getElementById('aoi-queue').textContent = `${stream.queue_depth}/${stream.queue_capacity}`;
                        document.getElementById('aoi-fps').textContent = `${stream.processing_fps} FPS`;
                        document.getElementById('run-aoi').disabled = stream.accepting;
                        document.getElementById('stop-aoi').disabled = !stream.accepting;
                        const live=document.getElementById('stage1-live');
                        live.classList.toggle('running',stream.accepting);
                        live.textContent=stream.accepting?'逐张检测中':t.status==='COMPLETED'?'检测完成':t.status==='FAILED'?'检测结束 · 有失败帧':'正在排空队列';
                        let currentFile=aoiFrameNames[lastAOIFetched]||'设备推送';
                        try{
                            const folderResponse=await fetch(`/api/v1/aoi/folder-runs/${encodeURIComponent(id)}`,{cache:'no-store'});
                            if(folderResponse.ok){const folder=(await folderResponse.json()).data;currentFile=folder.current_file||'等待读取图片';
                                if(folder.error)currentFile=`回放失败：${folder.error}`}
                        }catch{}
                        document.getElementById('stage1-current-file').textContent=currentFile;
                        document.getElementById('stage1-meta').textContent=`任务 ${id.slice(0,8)} · ${statusText[t.status]||t.status}\n已接收 ${stream.accepted} 张，已处理 ${stream.processed} 张${t.error_message?'\n'+t.error_message:''}`;
                        stage1MoreFrames=await fetchAOIFrames(id);
                    }
                    if (t.result) {
                        if (stage==='stage3') document.getElementById(stage+'-result').textContent = JSON.stringify(t.result,null,2);
                        if (stage==='stage3') {
                            document.getElementById('stage3-decision').textContent = t.result.eligible_for_grading
                                ? '已入围' : '未进入分级';
                            document.getElementById('stage3-grade').textContent = t.result.grade || '未分级';
                            document.getElementById('stage3-reason').textContent = t.result.reason || '暂无';
                        }
                        if (stage==='stage2') {
                            Stage2Folders.renderResults(id,t.result);
                        }
                    } else if (stage==='stage1' && t.status==='FAILED') {
                        document.getElementById('stage1-result').textContent = t.error_message || 'AOI 检测失败';
                    } else if (stage==='stage3' && t.status==='FAILED') {
                        document.getElementById('stage3-decision').textContent =
                            t.error_code==='MODEL_NOT_CONFIGURED' ? '前序已合格' : '未进入分级';
                        document.getElementById('stage3-grade').textContent = '未分级';
                        document.getElementById('stage3-reason').textContent = t.error_message || '分级失败';
                    }
                    retryDelay=1500;
                    if (['RUNNING','PENDING','QUEUED','CANCEL_REQUESTED'].includes(t.status) || newLogs.length===200 || stage1MoreFrames)
                        taskTimers[stage]=setTimeout(poll,newLogs.length===200?100:stage==='stage1'?(stage1MoreFrames&&!t.result?.accepting?250:650):1500);
                } catch (err) {
                    document.getElementById(stage+'-meta').textContent = '状态查询失败，正在重试：'+err.message;
                    taskTimers[stage]=setTimeout(poll,retryDelay);
                    retryDelay=Math.min(retryDelay*2,30000);
                }
            }
            poll();
        }
        async function runAOI() {
            const productId=document.getElementById('stage1-product').value.trim();
            const sourceId=document.getElementById('stage1-source').value.trim();
            const directory=document.getElementById('stage1-directory').value.trim();
            const interval=Number(document.getElementById('stage1-interval').value);
            if(!productId||!directory){alert('请填写图片目录和产品编号');return}
            try {
                const response=await fetch('/api/v1/aoi/folder-runs',{method:'POST',headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({directory,product_id:productId,source_id:sourceId,interval_seconds:interval,request_id:crypto.randomUUID()})});
                const data=await response.json();
                if(!response.ok) throw new Error(data.message||'启动 AOI 文件夹检测失败');
                aoiDisplayRunId=data.data.task_id;lastAOIFetched=0;aoiDisplayQueue=[];aoiFrameNames={};
                document.getElementById('stage1-preview-image').hidden=true;
                document.querySelector('#stage1-preview .stage1-image-card:first-child span').hidden=false;
                document.getElementById('stage1-prediction-canvas').hidden=true;
                document.getElementById('stage1-prediction-placeholder').hidden=false;
                document.getElementById('stage1-prediction-placeholder').textContent='等待模型结果';
                document.getElementById('stage1-judgement').textContent='待检测';
                document.getElementById('stage1-defect-types').textContent='—';
                document.getElementById('stage1-sequence').textContent='等待图片';
                watchTask('stage1',data.data.task_id);
            } catch(err) { alert(err.message); }
        }
        async function stopAOI() {
            const id=localStorage.getItem('liante-stage1-task');
            if(!id){alert('当前没有 AOI 检测流');return}
            try {
                const response=await fetch('/api/v1/aoi/streams/'+encodeURIComponent(id)+'/close',{method:'POST'});
                const data=await response.json();
                if(!response.ok) throw new Error(data.message||'停止 AOI 流失败');
                watchTask('stage1',id);
            } catch(err) { alert(err.message); }
        }
        async function runGrading() {
            try {
                const body={product_id:document.getElementById('stage3-product').value.trim(),
                    qualification_id:document.getElementById('stage3-qualification').value.trim()};
                if(!body.product_id) throw new Error('请输入产品编号');
                if(!body.qualification_id) throw new Error('请输入合格证据编号');
                const response=await fetch('/api/v1/inference/grading',{method:'POST',
                    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
                const data=await response.json(); const id=data.data?.task_id;
                if(id) watchTask('stage3',id); else throw new Error(data.message||'提交分级失败');
            } catch(err) {alert(err.message)}
        }
        async function showFusionEvidence() {
            const id=document.getElementById('stage3-qualification').value.trim();
            const target=document.getElementById('stage3-fusion-evidence');
            if(!id){target.textContent='融合依据：等待合格证据；包含 Stage1、Stage2、四项调测参数和客户分级。';return}
            try {
                const response=await fetch('/api/v1/qualifications/'+encodeURIComponent(id),{cache:'no-store'});
                const body=await response.json();
                if(!response.ok)throw new Error(body.message||'无法读取合格证据');
                const q=body.data, p=q.tuning_parameters||{}, m=q.manual_review||{};
                target.textContent=`融合依据：AOI ${q.aoi_task_id} · Stage2 ${q.stage2_task_id} · `+
                    `硅光 ${p.silicon_power_dbm??'未录入'} dBm / ${p.silicon_extinction_ratio_db??'未录入'} dB · `+
                    `非硅光 ${p.non_silicon_power_dbm??'未录入'} dBm / ${p.non_silicon_extinction_ratio_db??'未录入'} dB · `+
                    `客户分级 ${m.customer_grade||'未录入'} · 依据 ${m.grading_basis||'未录入'}`;
            } catch(err){target.textContent='融合依据读取失败：'+err.message}
        }
        async function createQualification() {
            const value=id=>document.getElementById(id).value.trim();
            const checked=id=>document.getElementById(id).checked;
            const power=Number(value('stage3-optical-power'));
            const voltage=Number(value('stage3-electrical-voltage'));
            const tuningFields={silicon_power_dbm:'tuning-silicon-power',
                silicon_extinction_ratio_db:'tuning-silicon-er',
                non_silicon_power_dbm:'tuning-nonsilicon-power',
                non_silicon_extinction_ratio_db:'tuning-nonsilicon-er'};
            try {
                if(!value('stage3-product')||!value('stage3-aoi-task')||!value('stage3-stage2-task'))
                    throw new Error('请填写产品编号及已完成的 AOI、Stage2 任务编号');
                if(!value('stage3-optical-power')||!value('stage3-electrical-voltage')||
                   !Number.isFinite(power)||!Number.isFinite(voltage))
                    throw new Error('请填写真实的光学功率和电压测量值');
                if(!checked('stage3-optical-qualified')||!checked('stage3-electrical-qualified')||
                   !checked('stage3-tuning-complete'))
                    throw new Error('只有全部人工确认合格且已收到外部调测完成回执的产品才能登记');
                if(!value('stage3-recipe-id')||!value('stage3-recipe-version')||!value('stage3-measured-at')||!value('stage3-review-notes'))
                    throw new Error('请填写配方编号、版本、测量时间及复核说明；复核身份由服务器绑定');
                const tuningParameters={};
                for(const [name,id] of Object.entries(tuningFields)){
                    if(!value(id)||!Number.isFinite(Number(value(id))))
                        throw new Error('请先填写硅光、非硅光的功率和消光比四项实测值');
                    tuningParameters[name]=Number(value(id));
                }
                if(!value('stage3-customer-grade')||!value('stage3-grading-basis'))
                    throw new Error('请填写客户 A/B/C 分级及其判定依据');
                const body={product_id:value('stage3-product'),aoi_task_id:value('stage3-aoi-task'),
                    stage2_task_id:value('stage3-stage2-task'),recipe_id:value('stage3-recipe-id'),
                    recipe_version:value('stage3-recipe-version'),request_id:crypto.randomUUID(),
                    optical_features:{qualified:true,power_dbm:power},
                    electrical_features:{qualified:true,voltage_v:voltage},
                    tuning_parameters:tuningParameters,
                        tuning_result:{status:'complete',notes:'外部调测系统回执已确认'},
                    manual_review:{decision:'qualified',
                        measured_at:new Date(value('stage3-measured-at')).toISOString(),
                        notes:value('stage3-review-notes'),
                        customer_grade:value('stage3-customer-grade'),
                        grading_basis:value('stage3-grading-basis')}};
                const response=await fetch('/api/v1/qualifications',{method:'POST',
                    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
                const data=await response.json();
                if(!response.ok) throw new Error(data.message||'证据登记失败');
                document.getElementById('stage3-qualification').value=data.data.qualification_id;
                document.getElementById('stage3-qualification-meta').textContent=
                    '已登记证据 '+data.data.qualification_id+'；可以提交质量分级';
                document.getElementById('stage3-fusion-evidence').textContent=
                    `融合依据：AOI ${body.aoi_task_id} · Stage2 ${body.stage2_task_id} · `+
                    `硅光 ${tuningParameters.silicon_power_dbm} dBm / ${tuningParameters.silicon_extinction_ratio_db} dB · `+
                    `非硅光 ${tuningParameters.non_silicon_power_dbm} dBm / ${tuningParameters.non_silicon_extinction_ratio_db} dB · `+
                    `客户分级 ${body.manual_review.customer_grade} · ${body.manual_review.grading_basis}`;
            } catch(err) {
                document.getElementById('stage3-qualification-meta').textContent='登记失败：'+err.message;
            }
        }
        document.getElementById('stage3-measured-at').value=
            new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
        Stage2Folders.initialize(watchTask,showPrediction);
        document.getElementById('run-aoi').addEventListener('click', runAOI);
        document.getElementById('stop-aoi').addEventListener('click', stopAOI);
        document.getElementById('run-grading').addEventListener('click', runGrading);
        document.getElementById('create-qualification').addEventListener('click', createQualification);
        document.getElementById('stage3-qualification').addEventListener('change',showFusionEvidence);
        document.documentElement.dataset.lianteScriptReady = 'yes';
        for (const stage of ['stage1','stage2','stage3']) {
            const id=localStorage.getItem('liante-'+stage+'-task'); if(id) watchTask(stage,id);
        }
