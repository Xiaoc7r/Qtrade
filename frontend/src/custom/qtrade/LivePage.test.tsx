// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { LiveState } from '@/lib/qtradeTypes'
import { LivePage } from './LivePage'

vi.mock('@/lib/api',()=>({api:{qtradeLiveState:vi.fn(),qtradeLiveConfig:vi.fn(),qtradeLiveAction:vi.fn(),qtradeLiveMonitor:vi.fn(),qtradeLiveKill:vi.fn(),qtradeLiveArm:vi.fn(),qtradeLiveCancel:vi.fn()}}))
const initial:LiveState={policy:{mode:'shadow',symbols:[],analysis:{weights:{trend:30,momentum:25,liquidity:20,lowvol:25,value:0},limit:20,exclude_st:true,min_amount:50000000,demo:false},risk:{capital:0,max_order:0,max_symbol_pct:0,max_exposure_pct:0,max_daily_loss:0,max_daily_turnover:0,max_orders_per_day:20,cooldown_seconds:300,quote_max_age:10,max_price_deviation:.005,fee_buffer_pct:.003,stop_loss_pct:.05},interval_seconds:300,top_n:5,entry_score:70,exit_score:30,ai_enabled:false,ai_min_confidence:.75,ai_calls_per_day:20,max_signal_age_hours:96},revision:'a'.repeat(64),connected:false,monitoring:false,armed:false,armed_until:null,killed:false,message:'尚未连接',sdk_available:false,account_fingerprint:null,last_scan:null,live_environment_enabled:false,broker_acceptance:'尚未完成券商验收',intents:[],events:[]}
let host:HTMLDivElement,root:Root,qc:QueryClient
beforeEach(()=>{
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true})
  vi.mocked(api.qtradeLiveState).mockResolvedValue(structuredClone(initial))
  vi.mocked(api.qtradeLiveAction).mockResolvedValue(structuredClone(initial))
  vi.mocked(api.qtradeLiveKill).mockResolvedValue({...structuredClone(initial),killed:true})
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
  qc=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})
})
afterEach(async()=>{await act(async()=>root.unmount());qc.clear();host.remove();vi.clearAllMocks()})
async function flush(){await act(async()=>{await new Promise(r=>setTimeout(r,20))})}
async function render(){await act(async()=>root.render(<QueryClientProvider client={qc}><MemoryRouter><LivePage/></MemoryRouter></QueryClientProvider>));await flush()}
function button(name:string){return [...host.querySelectorAll('button')].find(b=>b.textContent===name)!}
async function click(name:string){await act(async()=>button(name).click());await flush()}
async function input(el:HTMLInputElement,value:string){await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}))});await flush()}

it('shows missing SDK and cannot arm without a connected account',async()=>{
  await render()
  expect(host.textContent).toContain('未发现 XtQuant SDK')
  expect(host.textContent).toContain('尚未完成券商验收')
  expect(button('开启限时自动执行').disabled).toBe(true)
  expect(api.qtradeLiveArm).not.toHaveBeenCalled()
  await click('连接本机 QMT')
  expect(api.qtradeLiveAction).toHaveBeenCalledWith('connect')
})

it('preserves separators while typing a whitelist and saves the base revision',async()=>{
  vi.mocked(api.qtradeLiveConfig).mockImplementation(async policy=>({...structuredClone(initial),policy,revision:'b'.repeat(64)}))
  await render()
  const el=host.querySelector('input[placeholder]') as HTMLInputElement
  await input(el,'600000.SH, ')
  expect(el.value).toBe('600000.SH, ')
  await input(el,'600000.SH, 000001.SZ')
  await act(async()=>host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));await flush()
  expect(api.qtradeLiveConfig).toHaveBeenCalledWith(expect.objectContaining({symbols:['600000.SH','000001.SZ']}),'a'.repeat(64))
})

it('sends emergency stop even while a regular operation is pending',async()=>{
  let finish!:(value:LiveState)=>void
  vi.mocked(api.qtradeLiveAction).mockReturnValue(new Promise(resolve=>{finish=resolve}))
  await render();await click('扫描一次')
  expect(button('紧急停止').disabled).toBe(false)
  await click('紧急停止')
  expect(api.qtradeLiveKill).toHaveBeenCalledWith(true)
  finish({...structuredClone(initial),killed:true});await flush()
  expect(host.textContent).toContain('紧急停止锁定')
})

it('retains a draft across polling updates and exposes request errors',async()=>{
  vi.mocked(api.qtradeLiveAction).mockRejectedValue(new Error('券商 SDK 未就绪'))
  await render()
  const el=host.querySelector('input[placeholder]') as HTMLInputElement
  await input(el,'600000.SH')
  await act(async()=>qc.setQueryData(['qtrade','live'],{...initial,message:'后台刷新'}));await flush()
  expect(el.value).toBe('600000.SH')
  await click('连接本机 QMT')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('SDK 未就绪')
})

it('keeps cancel requested distinct from cancelled and does not offer shadow cancellation',async()=>{
  vi.mocked(api.qtradeLiveState).mockResolvedValue({...structuredClone(initial),intents:[{client_id:'qt1',state:'cancel_requested',broker_id:'123',message:'等待回报',updated_at:'now',attempted:1,intent:{symbol:'600000.SH',side:'buy',qty:100,price:10,reason:'信号',revision:'a'}}]})
  await render()
  expect(host.textContent).toContain('待确认撤单')
  expect(button('请求撤单').disabled).toBe(true)
})

it('refresh replaces both displayed whitelist and outgoing draft',async()=>{
  vi.mocked(api.qtradeLiveConfig).mockImplementation(async policy=>({...structuredClone(initial),policy}))
  await render()
  const el=host.querySelector('input[placeholder]') as HTMLInputElement
  await input(el,'600000.SH')
  await click('刷新草稿为服务端配置')
  expect(el.value).toBe('')
  const capital=[...host.querySelectorAll('label')].find(l=>l.textContent==='策略资金预算（元）')!.querySelector('input')!
  await input(capital,'10000')
  await act(async()=>host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));await flush()
  expect(api.qtradeLiveConfig).toHaveBeenCalledWith(expect.objectContaining({symbols:[],risk:expect.objectContaining({capital:10000})}),initial.revision)
})

it('advanced JSON updates the visible whitelist and rejects incomplete risk objects',async()=>{
  await render()
  const area=host.querySelector('textarea')!
  const setArea=async(value:string)=>{await act(async()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(area,value);area.dispatchEvent(new Event('input',{bubbles:true}))});await flush()}
  await setArea(JSON.stringify({...initial.policy,symbols:['000001.SZ']}))
  await click('应用高级草稿')
  expect((host.querySelector('input[placeholder]') as HTMLInputElement).value).toBe('000001.SZ')
  await setArea(JSON.stringify({...initial.policy,risk:{}}))
  await click('应用高级草稿')
  expect(host.querySelector('[role="alert"]')).not.toBeNull()
  expect((host.querySelector('input[placeholder]') as HTMLInputElement).value).toBe('000001.SZ')
})
