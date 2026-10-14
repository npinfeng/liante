/* Additional lifecycle controls attached to the existing stage view. */
(() => {
    const source=document.getElementById('stage1-source');
    const extra=document.createElement('div');
    extra.innerHTML='<div class="form-group"><label>可用视角（逗号分隔）</label><input id="stage1-views" value="UNSPECIFIED"></div><div class="form-group"><label>必需视角（逗号分隔）</label><input id="stage1-required-views" value="UNSPECIFIED"></div><button type="button" id="close-current-product">关闭当前产品</button><pre id="aoi-product-status">产品关闭后会显示缺失视角和处理状态。</pre>';
    source.closest('.form-group').after(extra);
    const link=document.createElement('a');link.href='operations.html';link.textContent='管理中心 · 任务历史 / 产品追溯 / 合格证据 / 复核 / 日志';
    link.style.cssText='display:block;padding:12px;color:#93c5fd;text-align:center;';
    document.querySelector('header').after(link);
    document.getElementById('close-current-product').onclick=async()=>{
        const output=document.getElementById('aoi-product-status');
        try{
            const sid=localStorage.getItem('liante-stage1-task'),product=document.getElementById('stage1-product').value.trim();
            if(!sid||!product)throw new Error('请先建立流并填写要关闭的产品编号');
            const response=await fetch(`/api/v1/aoi/streams/${encodeURIComponent(sid)}/products/${encodeURIComponent(product)}/close`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:crypto.randomUUID()})});
            const body=await response.json();if(!response.ok)throw new Error(body.message);
            output.textContent=JSON.stringify(body.data,null,2);
        }catch(error){output.textContent=error.message;}
    };
})();
