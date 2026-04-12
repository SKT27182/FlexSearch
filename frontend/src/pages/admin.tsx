import { useState, useEffect } from 'react';
import { Shield, Users, FileText, Activity, BarChart3, Plus, Trash2, Search, ExternalLink, Loader2 } from 'lucide-react';
import { Button, Input, Card, CardHeader, CardTitle, CardContent } from '@/components/ui';
import { cn, formatRelativeTime, formatFileSize } from '@/lib/utils';
import { adminApi, type AdminUserStats, type AdminSystemStats, type AdminDocument } from '@/lib/api';

export function AdminPage() {
  const [activeTab, setActiveTab] = useState<'overview' | 'users' | 'documents'>('overview');
  const [stats, setStats] = useState<AdminSystemStats | null>(null);
  const [userStats, setUserStats] = useState<AdminUserStats[]>([]);
  const [documents, setDocuments] = useState<AdminDocument[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  
  // User creation state
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [newUserRole, setNewUserRole] = useState<'USER' | 'ADMIN'>('USER');
  const [creating, setCreating] = useState(false);

  // Document search/filter
  const [docSearch, setDocSearch] = useState('');

  useEffect(() => {
    loadData();
  }, [activeTab]);

  const loadData = async () => {
    setIsLoading(true);
    try {
      if (activeTab === 'overview') {
        const statsRes = await adminApi.getStats();
        setStats(statsRes);
      } else if (activeTab === 'users') {
        const usersRes = await adminApi.getAllUserStats();
        setUserStats(usersRes);
      } else if (activeTab === 'documents') {
        const docsRes = await adminApi.listDocuments();
        setDocuments(docsRes || []);
      }
    } catch (error) {
      console.error('Failed to load admin data:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUserEmail || !newUserPassword) return;

    setCreating(true);
    try {
      await adminApi.createUser({
        email: newUserEmail,
        password: newUserPassword,
        role: newUserRole,
      });
      setNewUserEmail('');
      setNewUserPassword('');
      setShowCreateUser(false);
      loadData();
    } catch (error) {
      console.error('Failed to create user:', error);
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteUser = async (userId: string, email: string) => {
    if (!confirm(`Delete user "${email}"? This will delete all their projects and documents.`)) return;

    try {
      await adminApi.deleteUser(userId);
      loadData();
    } catch (error) {
      console.error('Failed to delete user:', error);
    }
  };

  const handleChangeRole = async (userId: string, newRole: string) => {
    try {
      await adminApi.updateUserRole(userId, newRole);
      loadData();
    } catch (error) {
      console.error('Failed to update role:', error);
    }
  };

  const handleDeleteDocument = async (docId: string, filename: string) => {
    if (!confirm(`Delete document "${filename}"?`)) return;

    try {
      await adminApi.deleteDocument(docId);
      setDocuments(prev => prev.filter(d => d.id !== docId));
    } catch (error) {
      console.error('Failed to delete document:', error);
    }
  };

  const filteredDocs = (documents || []).filter(doc => {
    const search = docSearch.toLowerCase();
    return (
      (doc.filename?.toLowerCase() || '').includes(search) ||
      (doc.owner_email?.toLowerCase() || '').includes(search) ||
      (doc.project_name?.toLowerCase() || '').includes(search)
    );
  });

  const tabs = [
    { id: 'overview', label: 'Overview', icon: BarChart3 },
    { id: 'users', label: 'Users', icon: Users },
    { id: 'documents', label: 'Documents', icon: FileText },
  ] as const;

  // Global loading state for initial load
  const isInitialLoading = isLoading && !stats && userStats.length === 0 && documents.length === 0;

  if (isInitialLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="p-8 animate-fade-in max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <div className="p-3 rounded-xl bg-gradient-to-br from-primary to-accent">
          <Shield className="h-6 w-6 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-3xl font-bold">Admin Panel</h1>
          <p className="text-muted-foreground">System management and global overview</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-8 border-b border-border pb-2">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
              activeTab === id
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-secondary'
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && stats && (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <Card>
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-muted-foreground">Total Users</p>
                    <p className="text-3xl font-bold">{stats.users.total}</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      {stats.users.admins} admins, {stats.users.regular} users
                    </p>
                  </div>
                  <div className="p-3 rounded-xl bg-blue-500/10">
                    <Users className="h-6 w-6 text-blue-500" />
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-muted-foreground">Total Projects</p>
                    <p className="text-3xl font-bold">{stats.projects}</p>
                  </div>
                  <div className="p-3 rounded-xl bg-purple-500/10">
                    <Activity className="h-6 w-6 text-purple-500" />
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-muted-foreground">Total Documents</p>
                    <p className="text-3xl font-bold">{stats.documents.total}</p>
                  </div>
                  <div className="p-3 rounded-xl bg-amber-500/10">
                    <FileText className="h-6 w-6 text-amber-500" />
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Document Processing Summary</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-4">
                {Object.entries(stats.documents.by_status).map(([status, count]) => (
                  <div
                    key={status}
                    className={cn(
                      'flex-1 min-w-[150px] p-6 rounded-xl text-center border',
                      status === 'completed' && 'bg-emerald-500/5 border-emerald-500/20 text-emerald-600',
                      status === 'processing' && 'bg-blue-500/5 border-blue-500/20 text-blue-600',
                      status === 'pending' && 'bg-amber-500/5 border-amber-500/20 text-amber-600',
                      status === 'failed' && 'bg-red-500/5 border-red-500/20 text-red-600'
                    )}
                  >
                    <p className="text-3xl font-bold mb-1">{count as number}</p>
                    <p className="text-xs font-semibold uppercase tracking-wider opacity-80">{status}</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Users Tab */}
      {activeTab === 'users' && (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>User Accounts</CardTitle>
                <p className="text-sm text-muted-foreground mt-1">Manage users and their permissions</p>
              </div>
              <Button onClick={() => setShowCreateUser(!showCreateUser)}>
                {showCreateUser ? 'Cancel' : (
                  <>
                    <Plus className="h-4 w-4 mr-2" />
                    Create User
                  </>
                )}
              </Button>
            </CardHeader>
            {showCreateUser && (
              <CardContent className="border-b border-border bg-secondary/20">
                <form onSubmit={handleCreateUser} className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
                  <div className="md:col-span-1">
                    <label className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-1.5 block">Email</label>
                    <Input
                      type="email"
                      placeholder="user@example.com"
                      value={newUserEmail}
                      onChange={(e) => setNewUserEmail(e.target.value)}
                      required
                    />
                  </div>
                  <div className="md:col-span-1">
                    <label className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-1.5 block">Password</label>
                    <Input
                      type="password"
                      placeholder="••••••••"
                      value={newUserPassword}
                      onChange={(e) => setNewUserPassword(e.target.value)}
                      required
                    />
                  </div>
                  <div className="md:col-span-1">
                    <label className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-1.5 block">Role</label>
                    <select
                      value={newUserRole}
                      onChange={(e) => setNewUserRole(e.target.value as 'USER' | 'ADMIN')}
                      className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
                    >
                      <option value="USER">Regular User</option>
                      <option value="ADMIN">Administrator</option>
                    </select>
                  </div>
                  <Button type="submit" disabled={creating} className="w-full">
                    {creating ? 'Creating...' : 'Create Account'}
                  </Button>
                </form>
              </CardContent>
            )}
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="text-left text-xs font-bold uppercase tracking-wider text-muted-foreground border-b border-border">
                      <th className="p-4">User</th>
                      <th className="p-4">Role</th>
                      <th className="p-4">Projects</th>
                      <th className="p-4">Docs</th>
                      <th className="p-4">Joined</th>
                      <th className="p-4 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border text-sm">
                    {isLoading && userStats.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="p-12 text-center">
                          <Loader2 className="h-6 w-6 animate-spin mx-auto text-primary" />
                        </td>
                      </tr>
                    ) : userStats.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="p-12 text-center text-muted-foreground">
                          No users found
                        </td>
                      </tr>
                    ) : (
                      userStats.map((user) => (
                        <tr key={user.user_id} className="hover:bg-secondary/30 transition-colors">
                          <td className="p-4">
                            <div className="flex items-center gap-3">
                              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary/20 to-accent/20 flex items-center justify-center text-primary font-bold text-xs">
                                {user.email[0].toUpperCase()}
                              </div>
                              <span className="font-medium">{user.email}</span>
                            </div>
                          </td>
                          <td className="p-4">
                            <select
                              value={user.role}
                              onChange={(e) => handleChangeRole(user.user_id, e.target.value)}
                              className={cn(
                                'px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider',
                                user.role === 'ADMIN'
                                  ? 'bg-primary/10 text-primary border border-primary/20'
                                  : 'bg-secondary text-muted-foreground border border-border'
                              )}
                            >
                              <option value="USER">User</option>
                              <option value="ADMIN">Admin</option>
                            </select>
                          </td>
                          <td className="p-4 font-mono">{user.project_count}</td>
                          <td className="p-4 font-mono">{user.document_count}</td>
                          <td className="p-4 text-muted-foreground">
                            {formatRelativeTime(user.created_at)}
                          </td>
                          <td className="p-4 text-right">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive hover:bg-destructive/10"
                              onClick={() => handleDeleteUser(user.user_id, user.email)}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Documents Tab */}
      {activeTab === 'documents' && (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
          <Card>
            <CardHeader className="pb-4">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <CardTitle>Global Document Registry</CardTitle>
                  <p className="text-sm text-muted-foreground mt-1">Review and manage all system documents</p>
                </div>
                <div className="relative w-full md:w-72">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Search documents, owners..."
                    className="pl-9"
                    value={docSearch}
                    onChange={(e) => setDocSearch(e.target.value)}
                  />
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="text-left text-xs font-bold uppercase tracking-wider text-muted-foreground border-b border-border">
                      <th className="p-4">File Name</th>
                      <th className="p-4">Owner</th>
                      <th className="p-4">Project</th>
                      <th className="p-4">Size</th>
                      <th className="p-4">Status</th>
                      <th className="p-4 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border text-sm">
                    {isLoading && documents.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="p-12 text-center">
                          <Loader2 className="h-6 w-6 animate-spin mx-auto text-primary" />
                        </td>
                      </tr>
                    ) : filteredDocs.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="p-12 text-center text-muted-foreground">
                          {isLoading ? (
                            <Loader2 className="h-6 w-6 animate-spin mx-auto text-primary" />
                          ) : (
                            'No documents found'
                          )}
                        </td>
                      </tr>
                    ) : (
                      filteredDocs.map((doc) => (
                        <tr key={doc.id} className="hover:bg-secondary/30 transition-colors">
                          <td className="p-4">
                            <div className="flex items-center gap-3">
                              <FileText className="h-4 w-4 text-primary shrink-0" />
                              <span className="font-medium truncate max-w-[200px]">{doc.filename}</span>
                            </div>
                          </td>
                          <td className="p-4">
                            <div className="flex flex-col">
                              <span className="truncate max-w-[150px]">{doc.owner_email}</span>
                            </div>
                          </td>
                          <td className="p-4">
                            <div className="flex items-center gap-1.5 text-muted-foreground">
                              <span className="truncate max-w-[150px]">{doc.project_name}</span>
                            </div>
                          </td>
                          <td className="p-4 font-mono text-xs">{formatFileSize(doc.size_bytes)}</td>
                          <td className="p-4">
                            <span className={cn(
                              "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border",
                              doc.status === 'completed' && "bg-emerald-500/10 text-emerald-600 border-emerald-500/20",
                              doc.status === 'failed' && "bg-red-500/10 text-red-600 border-red-500/20",
                              doc.status === 'processing' && "bg-blue-500/10 text-blue-600 border-blue-500/20",
                              doc.status === 'pending' && "bg-amber-500/10 text-amber-600 border-amber-500/20"
                            )}>
                              {doc.status}
                            </span>
                          </td>
                          <td className="p-4 text-right">
                            <div className="flex justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                asChild
                              >
                                <a href={`/projects/${doc.project_id}`} target="_blank" rel="noreferrer">
                                  <ExternalLink className="h-4 w-4" />
                                </a>
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-destructive hover:bg-destructive/10"
                                onClick={() => handleDeleteDocument(doc.id, doc.filename)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
