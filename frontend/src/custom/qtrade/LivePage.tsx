import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, ArrowLeft, CircleStop, Download, Pause, Play, RefreshCw, ShieldCheck } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { LivePolicy, LiveRisk, LiveState } from '@/lib/qtradeTypes'
import './Qtrade.css'
import './Live.css'

const labels: Record<string, string> = { shadow:'影子意图', planned:'等待执行', submitting:'正在提交', unknown:'结果未知 · 停止', accepted:'已报 · 未证明成交', partial:'部分成交', filled:'已成交', cancelled:'已撤', rejected:'已拒绝', blocked:'风控阻止', cancel_requested:'待确认撤单' }
const fields: Array<[keyof LiveRisk, string, number, number]> = [
  ['capital','策略资金预算（元）',1,1], ['max_order','单笔最大金额（元）',1,1],
  ['max_symbol_pct','单股仓位上限（%）',100,1], ['max_exposure_pct','总敞口上限（%）',100,1],
  ['max_daily_loss','监控日内最大亏损（元）',1,1], ['max_daily_turnover','每日最大申报金额（元）',1,1],
]

export function LivePage() {
  const qc = useQueryClient()
  const query = useQuery({queryKey:QK.qtradeLive,queryFn:api.qtradeLiveState,refetchInterval:5000,retry:false})
  const [draft,setDraft] = useState<LivePolicy | null>(null)
  const [base,setBase] = useState('')
  const [symbolText,setSymbolText] = useState('')
  const [advanced,setAdvanced] = useState('')
  const [localError,setLocalError] = useState('')
  const [minutes,setMinutes] = useState(15)
  const [ack,setAck] = useState(false)
  const [resolution,setResolution] = useState('')
  const [evidence,setEvidence] = useState('')
  const [checkedBroker,setCheckedBroker] = useState(false)
  const state = query.data
  useEffect(() => { if (state && !draft) {setDraft(structuredClone(state.policy));setBase(state.revision);setSymbolText(state.policy.symbols.join(', '))} },[state,draft])
  const action = useMutation({ mutationFn:(fn:()=>Promise<LiveState>)=>fn(), onSuccess:value=>{qc.setQueryData(QK.qtradeLive,value);setAck(false)} })
  const save = useMutation({mutationFn:()=>api.qtradeLiveConfig(draft!,base),onSuccess:value=>{
    qc.setQueryData(QK.qtradeLive,value);setDraft(structuredClone(value.policy));setBase(value.revision);setSymbolText(value.policy.symbols.join(', '));setAdvanced('');setLocalError('');setAck(false)
  }})
  const busy = action.isPending || save.isPending
  const dirty = !!draft && !!state && JSON.stringify(draft)!==JSON.stringify(state.policy)
  const run = (fn:()=>Promise<LiveState>)=>{setLocalError('');action.mutate(fn)}
  const submit = (e:FormEvent)=>{e.preventDefault();save.mutate()}
  const exportAudit = ()=>{
    if(!state)return
    const url=URL.createObjectURL(new Blob([JSON.stringify({revision:state.revision,intents:state.intents,events:state.events},null,2)],{type:'application/json'}))
    const a=document.createElement('a');a.href=url;a.download='qtrade-execution-audit.json';a.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000)
  }
  return <div className="qt-workbench qt-live">
    <header className="qt-heading"><div><h1><ShieldCheck size={25}/>券商与 AI 自动执行</h1><p>研究产生意图，风控决定权限，券商回报证明成交。</p></div><Link className="qt-button" to="/qtrade"><ArrowLeft size={15}/>返回研究</Link></header>
    <p className="qt-live-boundary">当前提供 QMT 官方 SDK 适配。尚未开通券商权限时可完成配置和影子研究；不能据此认定真实账户已经接入。资金与风险额度由你配置。</p>
    {query.isLoading && <p className="qt-empty">正在读取执行器状态…</p>}
    {query.isError && <div className="qt-error" role="alert">{query.error.message}<button onClick={()=>void query.refetch()}>重试读取</button></div>}
    {state && <>
      <section className="qt-live-state" aria-label="执行状态">
        <dl><div><dt>通道</dt><dd>{state.connected?'已连接':state.sdk_available?'SDK 已发现 · 未连接':'未发现 XtQuant SDK'}</dd></div><div><dt>后台监控</dt><dd>{state.monitoring?'运行中':'已停止'}</dd></div><div><dt>真实报单授权</dt><dd>{state.killed?'紧急停止锁定':state.armed?'限时授权中':'关闭'}</dd></div><div><dt>账户</dt><dd>{state.account_fingerprint?`指纹 ${state.account_fingerprint}`:'未绑定'}</dd></div></dl>
        <p role="status">{state.message}</p><small>最近扫描 {state.last_scan??'尚未扫描'} · 授权到期 {state.armed_until??'未授权'}</small>
        <div className="qt-live-actions">
          <button className="qt-button" disabled={busy} onClick={()=>run(api.qtradeLiveAction.bind(null,'connect'))}>连接本机 QMT</button>
          <button className="qt-button" disabled={busy||dirty||state.killed} onClick={()=>run(()=>api.qtradeLiveMonitor(!state.monitoring))}>{state.monitoring?<Pause size={15}/>:<Play size={15}/ >}{state.monitoring?'停止监控':'启动后台监控'}</button>
          <button className="qt-button" disabled={busy||dirty||state.killed} onClick={()=>run(()=>api.qtradeLiveAction('scan'))}><Activity size={15}/>扫描一次</button>
          <button className="qt-button" disabled={busy||!state.connected} onClick={()=>run(()=>api.qtradeLiveAction('reconcile'))}><RefreshCw size={15}/>券商对账</button>
          <button className="qt-button" onClick={()=>run(()=>api.qtradeLiveAction('pause'))}><Pause size={15}/>暂停与撤销授权</button>
          <button className="qt-stop" onClick={()=>run(()=>api.qtradeLiveKill(!state.killed))}><CircleStop size={15}/>{state.killed?'解除停止锁':'紧急停止'}</button>
        </div>
        <p className="qt-hint">暂停与紧急停止阻止新的报单承诺；已进入 SDK 的请求可能仍完成。已有委托需查询并单独撤单，撤单请求成功也不等于已撤。</p>
      </section>
      {(action.isError||save.isError||localError)&&<div role="alert" className="qt-error">{localError||action.error?.message||save.error?.message}</div>}
      {draft&&<form className="qt-live-config" onSubmit={submit}>
        <div className="qt-section-title"><div><h2>策略、AI 与资金边界</h2><p>保存配置会暂停监控并撤销已有授权。默认 0 额度用于配置草稿，不能报单。</p></div><button type="submit" className="qt-primary" disabled={busy||!dirty}>保存配置</button></div>
        {base!==state.revision&&<p className="qt-error">配置已在其他窗口改变，请刷新草稿后编辑。</p>}
        <div className="qt-live-form-grid">
          <label>执行模式<select value={draft.mode} onChange={e=>setDraft({...draft,mode:e.target.value as 'shadow'|'auto'})}><option value="shadow">影子模式 · 无报撤单</option><option value="auto">通过风控后自动报单</option></select></label>
          <label className="qt-live-wide">沪深主板白名单<input value={symbolText} placeholder="例如 600000.SH, 000001.SZ" onChange={e=>{setSymbolText(e.target.value);setDraft({...draft,symbols:e.target.value.split(/[,，\s]+/).filter(Boolean).map(s=>s.toUpperCase())})}}/><small>最多 30 个完整代码；不自动扩大股票池。</small></label>
          {fields.map(([key,label,scale,step])=><label key={key}>{label}<input type="number" min="0" step={step} max={scale===100?100:1000000000} value={Number((draft.risk[key]*scale).toFixed(4))} onChange={e=>setDraft({...draft,risk:{...draft.risk,[key]:Number(e.target.value)/scale}})}/></label>)}
          <label>扫描间隔（秒）<input type="number" min="30" max="3600" value={draft.interval_seconds} onChange={e=>setDraft({...draft,interval_seconds:Number(e.target.value)})}/></label>
          <label>每天最多 AI 调用<input type="number" min="1" max="200" value={draft.ai_calls_per_day} onChange={e=>setDraft({...draft,ai_calls_per_day:Number(e.target.value)})}/></label>
          <label className="qt-check"><input type="checkbox" checked={draft.ai_enabled} onChange={e=>setDraft({...draft,ai_enabled:e.target.checked})}/>启用已配置 API 模型进行风险复核</label>
        </div>
        <p className="qt-hint">AI 只能允许、否决或缩减风险，不能生成任意订单或放宽限额。启用后按监控间隔调用你的模型，计入调用预算；无有效输出时停止交易。</p>
        <details className="qt-live-advanced"><summary>高级配置：因子权重、阈值、时效、冷却与全部风控参数</summary><p>可编辑完整 JSON；只允许声明式参数，不执行代码。点击“应用高级草稿”后再保存。</p><textarea aria-label="完整执行配置 JSON" rows={15} value={advanced||JSON.stringify(draft,null,2)} onChange={e=>setAdvanced(e.target.value)}/><button className="qt-button" type="button" onClick={()=>{try{const value=JSON.parse(advanced);if(!value?.risk||!value?.analysis||!Array.isArray(value.symbols)||value.symbols.some((s:unknown)=>typeof s!=='string')||Object.keys(draft.risk).some(k=>typeof value.risk[k]!=='number'||!Number.isFinite(value.risk[k])))throw new Error('缺少 risk、analysis 或 symbols');setDraft(value);setSymbolText(value.symbols.join(', '));setAdvanced('');setLocalError('')}catch(e){setLocalError(e instanceof Error?e.message:'JSON 无效')}}}>应用高级草稿</button></details>
        <button type="button" className="qt-button" disabled={busy} onClick={()=>{setDraft(structuredClone(state.policy));setBase(state.revision);setSymbolText(state.policy.symbols.join(', '));setAdvanced('');setLocalError('')}}>刷新草稿为服务端配置</button>
      </form>}
      <section className="qt-live-arm"><h2>自动执行授权</h2><p>需要已登录的本机 QMT、券商仿真验收、完整额度、实时行情、AI 复核与环境开关。每次重启或修改配置均需重新授权。</p><div className="qt-live-actions"><label>授权时长（分钟）<input type="number" min="1" max="60" value={minutes} onChange={e=>setMinutes(Number(e.target.value))}/></label><label className="qt-check"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>我已核对当前配置与资金风险</label><button className="qt-primary" disabled={busy||dirty||!ack||!state.connected||state.killed||state.policy.mode!=='auto'} onClick={()=>run(()=>api.qtradeLiveArm(state.revision,minutes))}>开启限时自动执行</button></div><small>{state.broker_acceptance} · 环境报单开关{state.live_environment_enabled?'已开启':'关闭'}</small></section>
      <section className="qt-strategies"><div className="qt-section-title"><div><h2>交易意图与券商状态</h2><p>研究、风控拒绝、已报、部分成交与真实成交分别记录。</p></div><button className="qt-button" onClick={exportAudit}><Download size={15}/>导出执行审计</button></div>
        {!state.intents.length?<p className="qt-empty">暂无交易意图。先保存白名单和策略配置，再启动监控；无券商连接时只记录研究结果。</p>:<div className="qt-table-scroll"><table><thead><tr><th>证券 / 方向</th><th>数量 / 限价</th><th>状态</th><th>依据与结果</th><th>操作</th></tr></thead><tbody>{state.intents.map(r=><tr key={r.client_id}><td>{r.intent.symbol}<small>{r.intent.side==='buy'?'买入':'卖出'}</small></td><td>{r.intent.qty} 股<small>{r.intent.price.toFixed(2)} 元</small></td><td>{labels[r.state]??r.state}<small>{r.broker_id?`券商编号 ${r.broker_id}`:r.client_id}</small></td><td className="qt-live-reason">{r.intent.reason}<small>{r.message}</small></td><td><button className="qt-button" disabled={busy||!r.broker_id||!['accepted','partial','unknown','cancel_requested'].includes(r.state)||state.policy.mode==='shadow'} onClick={()=>run(()=>api.qtradeLiveCancel(r.client_id))}>请求撤单</button></td></tr>)}</tbody></table></div>}
      </section>
      {state.intents.some(r=>r.state==='unknown')&&<section className="qt-live-arm"><h2>未知报单人工核对</h2><p>空查询不能证明未受理。先核对券商客户端的当日及历史委托、成交和资金；只有明确未受理时，才可记录人工声明。处理后不会自动重发或恢复授权。</p><div className="qt-live-form-grid"><label>待核对意图<select value={resolution} onChange={e=>setResolution(e.target.value)}><option value="">选择未知意图</option>{state.intents.filter(r=>r.state==='unknown').map(r=><option value={r.client_id} key={r.client_id}>{r.intent.symbol} · {r.client_id}</option>)}</select></label><label className="qt-live-wide">核对凭据说明<input value={evidence} minLength={20} maxLength={1000} placeholder="记录券商查询日期、流水核对范围与未受理依据，不填密码" onChange={e=>setEvidence(e.target.value)}/></label></div><label className="qt-check"><input type="checkbox" checked={checkedBroker} onChange={e=>setCheckedBroker(e.target.checked)}/>已在券商端核对，明确声明该意图未受理</label><button className="qt-button" disabled={busy||!state.connected||!resolution||evidence.length<20||!checkedBroker} onClick={()=>run(()=>api.qtradeLiveResolve(resolution,evidence,checkedBroker))}>记录人工声明并保持暂停</button></section>}
      <details className="qt-live-events" open><summary>最近审计记录（{state.events.length}）</summary>{state.events.slice(0,20).map(e=><div key={e.id}><span>{e.at.slice(0,19).replace('T',' ')} · {e.kind}</span><pre>{JSON.stringify(e.body,null,2)}</pre></div>)}</details>
    </>}
    <footer>Qtrade / TSP · 只支持普通主板股票限价整手交易 · 不把本机软件测试等同于券商接入验收</footer>
  </div>
}
