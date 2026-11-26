import { lazy } from 'react'
import { Workflow, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { FrontendExtension } from '@/extensions/types'

const Page = lazy(() => import('./Page').then(m => ({ default: m.Qtrade })))
const Live = lazy(() => import('./LivePage').then(m => ({ default: m.LivePage })))
const extension: FrontendExtension = {
  id: 'qtrade.workbench', apiVersion: 1,
  routes: [{ id: 'qtrade-workbench', path: '/qtrade', component: Page }, { id: 'qtrade-live', path: '/qtrade/live', component: Live }],
  navigation: [{ id: 'qtrade-workbench', routeId: 'qtrade-workbench', label: 'Qtrade 量化', icon: Workflow, order: 0 }, { id: 'qtrade-live', routeId: 'qtrade-live', label: '券商与 AI 执行', icon: ShieldCheck, order: 1 }],
  slots: [{ name: 'layout.header.quick', id: 'qtrade-quick', component: () => <Link to="/qtrade" className="text-xs text-accent hover:underline">Qtrade 量化工作台</Link> }],
}
export default extension
