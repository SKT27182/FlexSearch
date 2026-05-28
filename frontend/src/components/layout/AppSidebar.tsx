import { Link, useLocation } from 'react-router-dom'
import {
  Zap,
  LayoutDashboard,
  FolderOpen,
  Shield,
  PanelLeftClose,
  PanelLeft,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { hasAdminAccess } from '@/lib/roles'
import { Button } from '@/components/ui'
import { userDisplayName, userInitial } from '@/lib/display'
import { AppSidebarFooter } from './AppSidebarFooter'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/projects', icon: FolderOpen, label: 'Projects' },
]

interface AppSidebarProps {
  className?: string
  collapsed: boolean
  showCollapseToggle?: boolean
  onToggleCollapse?: () => void
  userName?: string
  userEmail?: string
  userRole?: string
  onLogout: () => void
  onNavigate?: () => void
}

export function AppSidebar({
  className,
  collapsed,
  showCollapseToggle,
  onToggleCollapse,
  userName,
  userEmail,
  userRole,
  onLogout,
  onNavigate,
}: AppSidebarProps) {
  const displayUser = { name: userName, email: userEmail }
  const location = useLocation()

  const handleNavClick = () => {
    onNavigate?.()
  }

  return (
    <div
      className={cn(
        'border-r border-border bg-card flex flex-col h-full',
        className
      )}
    >
      <div className="p-4 border-b border-border flex items-center justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <div className="p-2 rounded-lg bg-gradient-to-br from-primary to-accent shrink-0">
            <Zap className="h-5 w-5 text-primary-foreground" />
          </div>
          {!collapsed && (
            <div>
              <h1 className="text-lg font-bold">FlexSearch</h1>
              <p className="text-xs text-muted-foreground">RAG Platform</p>
            </div>
          )}
        </div>
        {showCollapseToggle && onToggleCollapse && (
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0"
            onClick={onToggleCollapse}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {collapsed ? (
              <PanelLeft className="h-4 w-4" />
            ) : (
              <PanelLeftClose className="h-4 w-4" />
            )}
          </Button>
        )}
      </div>

      <nav className="flex-1 p-4 space-y-1 overflow-y-auto">
        {navItems.map(({ to, icon: Icon, label }) => {
          const isActive =
            location.pathname === to ||
            (to !== '/' && location.pathname.startsWith(to))
          return (
            <Link
              key={to}
              to={to}
              onClick={handleNavClick}
              title={collapsed ? label : undefined}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                isActive
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                collapsed && 'justify-center px-2'
              )}
            >
              <Icon className="h-5 w-5 shrink-0" />
              {!collapsed && label}
            </Link>
          )
        })}

        {hasAdminAccess(userRole) && (
          <Link
            to="/admin"
            onClick={handleNavClick}
            title={collapsed ? 'Admin' : undefined}
            className={cn(
              'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
              location.pathname.startsWith('/admin')
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
              collapsed && 'justify-center px-2'
            )}
          >
            <Shield className="h-5 w-5 shrink-0" />
            {!collapsed && 'Admin'}
          </Link>
        )}
      </nav>

      <AppSidebarFooter
        collapsed={collapsed}
        displayName={userDisplayName(displayUser)}
        userInitial={userInitial(displayUser)}
        role={userRole}
        onLogout={onLogout}
        onNavigate={onNavigate}
      />
    </div>
  )
}
