import { useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Check, Download, FlaskConical, Loader2, RefreshCw, ShieldCheck, Upload, Workflow } from 'lucide-react'
import { api, REGIME_STATE_LABELS } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { Dimension, QtradePaperImport, QtradePreset, QtradeRequest } from '@/lib/qtradeTypes'
import { openStockPreview } from '@/extensions/stockPreview'
import { StrategyExecutionPanel } from '../paper/StrategyExecutionPanel'
import './Qtrade.css'

const DIMENSIONS: Array<{ id: Dimension; label: string; detail: string }> = [
  { id: 'trend', label: '趋势', detail: '收盘价相对 MA20，越高排名越前' },
  { id: 'momentum', label: '动量', detail: '20 个交易日收益，越高排名越前' },
  { id: 'liquidity', label: '流动性', detail: '成交额，越高排名越前' },
  { id: 'lowvol', label: '低波动', detail: '20 日年化波动率，越低排名越前' },
  { id: 'value', label: '估值', detail: '正 PE(TTM)，越低排名越前；需要估值数据' },
]
const INITIAL: QtradeRequest = { weights: { trend: 30, momentum: 25, liquidity: 20, lowvol: 25, value: 0 }, limit: 20, exclude_st: true, min_amount: 50_000_000, demo: false }
const percent = (n: number | undefined) => n == null ? '—' : `${(n * 100).toFixed(2)}%`

function exportJson(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function Qtrade() {
  const qc = useQueryClient()
  const [config, setConfig] = useState<QtradeRequest>(INITIAL)
  const [submitted, setSubmitted] = useState<QtradeRequest>(INITIAL)
  const [account, setAccount] = useState('')
  const [name, setName] = useState('我的模拟账户')
  const [cash, setCash] = useState(100_000)
  const [notice, setNotice] = useState('')
  const [fileError, setFileError] = useState('')
  const upload = useRef<HTMLInputElement>(null)
  const result = useQuery({ queryKey: QK.qtradeAnalysis(submitted), queryFn: () => api.qtradeAnalyze(submitted), retry: false, staleTime: 30_000 })
  const regime = useQuery({ queryKey: QK.regimeLatest, queryFn: api.regimeLatest, retry: false })
  const accounts = useQuery({ queryKey: QK.paperAccounts, queryFn: api.paperAccounts, retry: false })
  const presets = useQuery({ queryKey: QK.qtradePresets, queryFn: api.qtradePresets, retry: false })
  const gateways = useQuery({ queryKey: QK.qtradeGateways, queryFn: api.qtradeGateways, retry: false })
  const install = useMutation({ mutationFn: (id: QtradePreset['id']) => api.qtradeInstall(id), onSuccess: async r => {
    setNotice(`策略已就绪：${r.strategy_id}。进入回测，选择同名策略验证。`)
    await Promise.all([qc.invalidateQueries({ queryKey: QK.qtradePresets }), qc.invalidateQueries({ queryKey: ['strategies'] }), qc.invalidateQueries({ queryKey: ['screener-strategies'] }), qc.invalidateQueries({ queryKey: QK.strategyPool })])
  } })
  const create = useMutation({ mutationFn: (body: QtradePaperImport) => api.qtradeImportAccount(body), onSuccess: async r => {
    await qc.invalidateQueries({ queryKey: QK.paperAccounts }); setAccount(r.account.id)
    setNotice('模拟账户已创建。先回测，再预览并绑定策略；启用后只生成模拟委托。'); setFileError('')
  } })
  const data = result.data
  const active = DIMENSIONS.filter(d => (data?.weights[d.id] ?? 0) > 0)
  const selected = accounts.data?.accounts.find(a => a.id === account)
  const changed = JSON.stringify(config) !== JSON.stringify(submitted)
  const submit = (e: FormEvent) => { e.preventDefault(); setSubmitted(structuredClone(config)); if (!changed) void result.refetch() }
  const importFile = async (file?: File) => {
    setFileError('')
    if (!file) return
    try {
      if (file.size > 16_384) throw new Error('账户配置限 16 KB，请选择模拟账户 JSON')
      const value = JSON.parse(await file.text())
      if (!value || value.mode !== 'paper') throw new Error('仅支持 mode 为 paper 的模拟账户配置；请勿导入券商密码或 Token')
      create.mutate(value)
    } catch (e) { setFileError(e instanceof Error ? e.message : '配置无法解析') }
  }
  return <div className="qt-workbench">
    <header className="qt-heading">
      <div><h1><Workflow size={25} />Qtrade 量化工作台</h1><p>从市场判断到模拟执行，让每一步都有依据。</p></div>
      <span className="qt-status"><ShieldCheck size={15} />模拟交易模式</span>
    </header>
    <nav className="qt-flow" aria-label="量化操作流程">
      <a href="#qt-market">看市场</a><ArrowRight size={14} /><a href="#qt-research">多维研究</a><ArrowRight size={14} /><Link to="/backtest">策略回测</Link><ArrowRight size={14} /><a href="#qt-execution">模拟执行</a>
    </nav>
    <section id="qt-market" className="qt-market">
      <div className="qt-section-title"><h2>市场与数据</h2><Link to="/regime">展开环境分析 <ArrowRight size={14} /></Link></div>
      <div className="qt-market-grid">
        <div><span>分析数据</span><strong>{data?.mode === 'demo' ? '虚构演示' : data?.as_of ?? '等待同步'}</strong><small>{data?.mode === 'demo' ? '不代表当前市场' : data?.is_current_date ? '日期为今日 · 非逐笔实时' : '核对交易日与同步状态'}</small></div>
        <div><span>样本涨 / 跌 / 平</span><strong className="qt-breadth"><b>{data?.market.up ?? '—'}</b><em>{data?.market.down ?? '—'}</em><i>{data?.market.unchanged ?? '—'}</i></strong><small>有效涨跌幅 {data?.market.observed ?? 0} / {data?.universe ?? 0} 只</small></div>
        <div><span>本地市场环境</span><strong>{regime.data?.row ? REGIME_STATE_LABELS[regime.data.row.state] : '尚未计算'}</strong><small>{regime.data?.row ? `独立环境数据日期 ${regime.data.row.date}` : '同步日 K 后，在市场环境页计算'}</small></div>
      </div>
      {regime.isError && <p className="qt-error">市场环境读取失败。<button onClick={() => void regime.refetch()}>重试</button></p>}
    </section>
    <div className="qt-research-grid" id="qt-research">
      <form className="qt-config" onSubmit={submit}>
        <h2>选择分析维度</h2><p>权重自动归一。设为 0 即关闭该维度。</p>
        {DIMENSIONS.map(d => <label className="qt-weight" key={d.id}><span><b>{d.label}</b><small>{d.detail}</small></span><input aria-label={`${d.label}权重`} type="number" min="0" max="100" step="1" required value={config.weights[d.id]} onChange={e => setConfig(c => ({ ...c, weights: { ...c.weights, [d.id]: Number(e.target.value) } }))} /></label>)}
        <label className="qt-field">最低成交额（万元）<input type="number" min="0" max="100000000" value={config.min_amount / 10000} onChange={e => setConfig(c => ({ ...c, min_amount: Number(e.target.value) * 10000 }))} /></label>
        <label className="qt-check"><input type="checkbox" checked={config.exclude_st} onChange={e => setConfig(c => ({ ...c, exclude_st: e.target.checked }))} />排除 ST 与退市名称</label>
        <label className="qt-field">数据来源<select value={config.demo ? 'demo' : 'local'} onChange={e => setConfig(c => ({ ...c, demo: e.target.value === 'demo' }))}><option value="local">本地已同步行情</option><option value="demo">虚构演示 · 不用于交易</option></select></label>
        <button className="qt-primary" type="submit" disabled={result.isFetching || Object.values(config.weights).every(v => v === 0)}>{result.isFetching ? <Loader2 className="animate-spin" size={16} /> : <RefreshCw size={16} />}运行分析</button>
        {changed && <p className="qt-hint">配置尚未应用，结果仍对应上一次运行。</p>}
      </form>
      <section className="qt-results" aria-busy={result.isFetching}>
        <div className="qt-section-title"><div><h2>多维研究结果</h2><p>{data ? `${data.eligible} 只通过基础过滤 · ${data.scored} 只数据完整` : '等待分析'}</p></div><button className="qt-button" disabled={!data || result.isFetching} onClick={() => data && exportJson({ request: submitted, result: data }, 'qtrade-research.json')}><Download size={15} />导出依据</button></div>
        {data?.mode === 'demo' && <p className="qt-demo"><FlaskConical size={16} />以下为虚构样例。演示结果不会进入行情、回测或模拟委托。</p>}
        {result.isError && <div className="qt-error" role="alert">{result.error.message}<button onClick={() => void result.refetch()}>重新分析</button></div>}
        {result.isLoading && <p className="qt-empty">正在读取数据与计算指标…</p>}
        {data && data.rows.length === 0 && <div className="qt-empty"><h3>还没有完整的研究样本</h3><p>先配置数据源、同步日 K，或选择虚构演示体验分析。</p><Link className="qt-button" to="/data">前往数据管理 <ArrowRight size={15} /></Link></div>}
        {!!data?.rows.length && <div className="qt-table-scroll"><table><caption className="sr-only">相同完整样本内的加权百分位研究评分</caption><thead><tr><th>标的</th><th>综合分</th>{active.map(d => <th key={d.id}>{d.label}<small>{Math.round((data.weights[d.id] ?? 0) * 100)}%</small></th>)}</tr></thead><tbody>{data.rows.map(row => <tr key={row.symbol}><td><button className="qt-symbol" disabled={data.mode === 'demo'} onClick={() => openStockPreview({ symbol: row.symbol, name: row.name })}>{row.name}</button><small>{row.symbol}</small></td><td className="qt-score">{row.score.toFixed(1)}</td>{active.map(d => { const item = row.dimensions[d.id]; return <td key={d.id}><b>{item?.score.toFixed(1) ?? '—'}</b><small>{d.id === 'liquidity' ? `${((item?.raw ?? 0) / 1e8).toFixed(2)} 亿` : d.id === 'value' ? `${item?.raw.toFixed(1)} 倍` : percent(item?.raw)}</small></td> })}</tr>)}</tbody></table></div>}
        <details className="qt-notes" open><summary>数据覆盖与分析口径</summary>{data?.notes.map(n => <p key={n}>{n}</p>)}{data && <p>{active.map(d => `${d.label} ${data.coverage[d.id] ?? 0}/${data.eligible}`).join(' · ')}。先对完整样本按维度排名，再按权重合成；分数越高只表示相对排序越前。</p>}</details>
      </section>
    </div>
    <section className="qt-strategies">
      <div className="qt-section-title"><div><h2>把研究变成可验证的策略</h2><p>研究权重仅影响上方排名。以下策略独立运行，买卖条件在回测与跟单预览中核对。</p></div><Link to="/screener">策略库 <ArrowRight size={14} /></Link></div>
      {presets.isError && <p className="qt-error">策略模板读取失败。<button onClick={() => void presets.refetch()}>重试</button></p>}
      {presets.isLoading && <p>正在加载策略模板…</p>}
      {presets.data?.presets.map(p => <div className="qt-preset" key={p.id}><div><h3>{p.name}</h3><p>{p.description}</p></div><div>{p.installed ? <span className="qt-installed"><Check size={15} />已安装</span> : <button className="qt-button" disabled={install.isPending} onClick={() => install.mutate(p.id)}>安装模板</button>}<Link className="qt-button" to="/backtest"><FlaskConical size={15} />进入回测</Link></div></div>)}
      {install.isError && <p className="qt-error" role="alert">{install.error.message}</p>}
    </section>
    <section id="qt-execution" className="qt-execution">
      <div className="qt-section-title"><div><h2>模拟执行</h2><p>创建独立账户 → 回测 → 预览策略快照 → 确认绑定与启用 → 核对订单。</p></div><Link to="/paper">完整模拟盘 <ArrowRight size={14} /></Link></div>
      <form className="qt-account-form" onSubmit={e => { e.preventDefault(); create.mutate({ mode: 'paper', account_id: `qt_${crypto.randomUUID().replaceAll('-', '').slice(0, 16)}`, name, initial_cash: cash }) }}>
        <label>账户名称<input value={name} maxLength={60} required onChange={e => setName(e.target.value)} /></label><label>初始资金（元）<input type="number" value={cash} min="1" max="10000000000" required onChange={e => setCash(Number(e.target.value))} /></label>
        <button type="submit" className="qt-primary" disabled={create.isPending}>创建模拟账户</button>
        <input hidden type="file" ref={upload} accept=".json,application/json" onChange={e => { void importFile(e.target.files?.[0]); e.target.value = '' }} />
        <button type="button" className="qt-button" disabled={create.isPending} onClick={() => upload.current?.click()}><Upload size={15} />导入模拟配置</button>
        <button type="button" className="qt-button" onClick={() => exportJson({ mode: 'paper', account_id: 'my_paper_account', name: '我的模拟账户', initial_cash: 100000, commission_pct: .00025, stamp_tax_pct: .0005, slippage_bps: 5 }, 'paper-account.example.json')}>下载配置示例</button>
      </form>
      <p className="qt-hint">导入只创建模拟账户配置，不恢复持仓或成交。请勿上传券商密码、Cookie 或 Token。</p>
      {(create.isError || fileError) && <p className="qt-error" role="alert">{fileError || create.error?.message}</p>}
      {notice && <p role="status" className="qt-notice">{notice}</p>}
      {accounts.isError && <p className="qt-error">账户列表读取失败。<button onClick={() => void accounts.refetch()}>重试</button></p>}
      <label className="qt-field qt-account-select">选择模拟账户<select value={account} onChange={e => setAccount(e.target.value)}><option value="">请选择账户</option>{accounts.data?.accounts.map(a => <option key={a.id} value={a.id}>{a.name} · {a.status === 'active' ? '可用' : '冻结'}</option>)}</select></label>
      {selected && <div className="qt-account-detail"><p>账户 {selected.name} · 初始资金 {selected.initial_cash?.toLocaleString('zh-CN') ?? '—'} 元 · {selected.status === 'frozen' ? '已冻结' : '可用'}</p><StrategyExecutionPanel key={account} account={account} /></div>}
      <div className="qt-gateways"><h3>交易通道</h3>{gateways.data?.gateways.map(g => <p key={g.id}><strong>{g.name}</strong><span>{g.available ? '可用' : '未接入'}</span><small>{g.message}</small></p>)}{gateways.isError && <p className="qt-error">通道状态读取失败。<button onClick={() => void gateways.refetch()}>重试</button></p>}</div>
    </section>
    <footer>Qtrade 基于 Tick Stock Panel / Tivelo · 研究、回测与模拟成交均有数据边界 · 策略效果需独立验证</footer>
  </div>
}
