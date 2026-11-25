import { lazy } from 'react'
import { Workflow } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { FrontendExtension } from '@/extensions/types'

const Page = lazy(() => import('./Page').then(m => ({ default: m.Qtrade })))
const extension: FrontendExtension = {
  id: 'qtrade.workbench', apiVersion: 1,
  routes: [{ id: 'qtrade-workbench', path: '/qtrade', component: Page }],
  navigation: [{ id: 'qtrade-workbench', routeId: 'qtrade-workbench', label: 'Qtrade 量化', icon: Workflow, order: 0 }],
  slots: [{ name: 'layout.header.quick', id: 'qtrade-quick', component: () => <Link to="/qtrade" className="text-xs text-accent hover:underline">Qtrade 量化工作台</Link> }],
}
export default extension
