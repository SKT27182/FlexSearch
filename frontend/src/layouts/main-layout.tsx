import { useEffect, useRef, useState } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Menu, Loader2 } from 'lucide-react'
import { useAuthStore, useProjectStore } from '@/stores'
import { cn } from '@/lib/utils'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { Button } from '@/components/ui'
import { AppSidebar } from '@/components/layout/AppSidebar'

const SIDEBAR_COLLAPSED_KEY = 'flexsearch-sidebar-collapsed'

export function MainLayout() {
  const { user, isInitialized, isLoading, loadUser, logout } = useAuthStore()
  const fetchProjects = useProjectStore((state) => state.fetchProjects)
  const location = useLocation()
  const projectsFetchedForUser = useRef<string | null>(null)
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true'
    } catch {
      return false
    }
  })

  const titleMap: Record<string, string> = {
    '/': 'Dashboard — FlexSearch',
    '/projects': 'Projects — FlexSearch',
    '/settings': 'Settings — FlexSearch',
    '/admin': 'Admin — FlexSearch',
  }
  const pageTitle =
    titleMap[location.pathname] ??
    (location.pathname.startsWith('/projects/')
      ? 'Project — FlexSearch'
      : 'FlexSearch')
  useDocumentTitle(pageTitle)

  const isAuthenticated = user !== null

  useEffect(() => {
    loadUser()
  }, [loadUser])

  useEffect(() => {
    if (!user?.id) {
      projectsFetchedForUser.current = null
      return
    }
    if (projectsFetchedForUser.current === user.id) return
    projectsFetchedForUser.current = user.id
    void fetchProjects()
  }, [user?.id, fetchProjects])

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(sidebarCollapsed))
    } catch {
      /* ignore */
    }
  }, [sidebarCollapsed])

  if (!isInitialized || isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-muted-foreground">Loading...</p>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  const closeMobileSidebar = () => setIsSidebarOpen(false)

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <header className="fixed top-0 left-0 right-0 z-50 flex h-14 items-center border-b bg-card px-4 md:hidden">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setIsSidebarOpen(true)}
          className="mr-2"
          aria-label="Open menu"
        >
          <Menu className="h-5 w-5" />
        </Button>
        <div className="font-semibold">FlexSearch</div>
      </header>

      <div
        className={cn(
          'fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity md:hidden',
          isSidebarOpen ? 'opacity-100' : 'pointer-events-none opacity-0'
        )}
        onClick={closeMobileSidebar}
        aria-hidden={!isSidebarOpen}
      />

      <AppSidebar
        className={cn(
          'fixed inset-y-0 left-0 z-50 w-64 transform transition-transform duration-300 ease-in-out md:hidden',
          isSidebarOpen ? 'translate-x-0' : '-translate-x-full'
        )}
        collapsed={false}
        userName={user?.name}
        userEmail={user?.email}
        userRole={user?.role}
        onLogout={logout}
        onNavigate={closeMobileSidebar}
      />

      <aside
        className={cn(
          'hidden md:flex shrink-0 h-full transition-[width] duration-300 ease-in-out overflow-hidden',
          sidebarCollapsed ? 'w-[4.5rem]' : 'w-64'
        )}
      >
        <AppSidebar
          className="w-full"
          collapsed={sidebarCollapsed}
          showCollapseToggle
          onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
          userName={user?.name}
          userEmail={user?.email}
          userRole={user?.role}
          onLogout={logout}
        />
      </aside>

      <main className="flex-1 min-w-0 overflow-auto pt-14 md:pt-0">
        <Outlet />
      </main>
    </div>
  )
}
