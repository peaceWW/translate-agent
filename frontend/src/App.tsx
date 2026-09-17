import { NavLink, Outlet, useLocation } from 'react-router-dom'
import './App.css'

const navItems = [
  { to: '/translate', label: '文档翻译' },
  { to: '/models', label: '模型配置' },
]

export default function App() {
  const location = useLocation()
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">AT</div>
          <div>
            <div className="brand-title">AcademicTranslate</div>
            <div className="brand-sub">专业学术论文翻译智能体</div>
          </div>
        </div>
        <nav className="nav">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => ((isActive || (item.to === '/translate' && location.pathname === '/')) ? 'nav-item active' : 'nav-item')}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">AI for Better Research</div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
