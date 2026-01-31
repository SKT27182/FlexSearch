import { useState, useEffect } from 'react';
import { Shield, Users, FileText, Activity, BarChart3, Plus, Trash2 } from 'lucide-react';
import { Button, Input, Card, CardHeader, CardTitle, CardContent } from '@/components/ui';
import { cn, formatRelativeTime } from '@/lib/utils';
import { api } from '@/lib/api';

interface UserStats {
  user_id: string;
  email: string;
  role: string;
  project_count: number;
  document_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_requests: number;
  avg_latency_ms: number;
  created_at: string;
}

interface SystemStats {
  users: { total: number; admins: number; regular: number };
  projects: number;
  documents: { total: number; by_status: Record<string, number> };
  token_usage: {
    total_input_tokens: number;
    total_output_tokens: number;
    total_requests: number;
    average_latency_ms: number;
  };
  last_24h: {
    input_tokens: number;
    output_tokens: number;
    requests: number;
  };
}

export function AdminPage() {
  const [activeTab, setActiveTab] = useState<'overview' | 'users' | 'documents'>('overview');
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [userStats, setUserStats] = useState<UserStats[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [newUserRole, setNewUserRole] = useState<'USER' | 'ADMIN'>('USER');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [statsRes, usersRes] = await Promise.all([
        api.get<SystemStats>('/admin/stats'),
        api.get<UserStats[]>('/admin/users/stats/all'),
      ]);
      setStats(statsRes.data);
      setUserStats(usersRes.data);
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
      await api.post('/admin/users', {
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
    if (!confirm(`Delete user "${email}"? This cannot be undone.`)) return;

    try {
      await api.delete(`/admin/users/${userId}`);
      loadData();
    } catch (error) {
      console.error('Failed to delete user:', error);
    }
  };

  const handleChangeRole = async (userId: string, newRole: string) => {
    try {
      await api.patch(`/admin/users/${userId}/role?role=${newRole}`);
      loadData();
    } catch (error) {
      console.error('Failed to update role:', error);
    }
  };

  const formatNumber = (n: number) => {
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return n.toString();
  };

  const tabs = [
    { id: 'overview', label: 'Overview', icon: BarChart3 },
    { id: 'users', label: 'Users', icon: Users },
    { id: 'documents', label: 'Documents', icon: FileText },
  ] as const;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="p-8 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <div className="p-3 rounded-xl bg-gradient-to-br from-primary to-accent">
          <Shield className="h-6 w-6 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-3xl font-bold">Admin Panel</h1>
          <p className="text-muted-foreground">System management and analytics</p>
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
        <div className="space-y-8">
          {/* Stats Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
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

            <Card>
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-muted-foreground">API Requests</p>
                    <p className="text-3xl font-bold">{formatNumber(stats.token_usage.total_requests)}</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      {formatNumber(stats.last_24h.requests)} last 24h
                    </p>
                  </div>
                  <div className="p-3 rounded-xl bg-green-500/10">
                    <BarChart3 className="h-6 w-6 text-green-500" />
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Token Usage */}
          <Card>
            <CardHeader>
              <CardTitle>Token Usage</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="p-4 rounded-lg bg-secondary">
                  <p className="text-sm text-muted-foreground">Total Input Tokens</p>
                  <p className="text-2xl font-bold">{formatNumber(stats.token_usage.total_input_tokens)}</p>
                  <p className="text-xs text-primary mt-1">
                    +{formatNumber(stats.last_24h.input_tokens)} last 24h
                  </p>
                </div>
                <div className="p-4 rounded-lg bg-secondary">
                  <p className="text-sm text-muted-foreground">Total Output Tokens</p>
                  <p className="text-2xl font-bold">{formatNumber(stats.token_usage.total_output_tokens)}</p>
                  <p className="text-xs text-primary mt-1">
                    +{formatNumber(stats.last_24h.output_tokens)} last 24h
                  </p>
                </div>
                <div className="p-4 rounded-lg bg-secondary">
                  <p className="text-sm text-muted-foreground">Avg Latency</p>
                  <p className="text-2xl font-bold">{stats.token_usage.average_latency_ms.toFixed(0)}ms</p>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Document Status */}
          <Card>
            <CardHeader>
              <CardTitle>Document Processing Status</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex gap-4">
                {Object.entries(stats.documents.by_status).map(([status, count]) => (
                  <div
                    key={status}
                    className={cn(
                      'flex-1 p-4 rounded-lg text-center',
                      status === 'COMPLETED' && 'bg-green-500/10 text-green-500',
                      status === 'PROCESSING' && 'bg-blue-500/10 text-blue-500',
                      status === 'PENDING' && 'bg-amber-500/10 text-amber-500',
                      status === 'FAILED' && 'bg-red-500/10 text-red-500'
                    )}
                  >
                    <p className="text-2xl font-bold">{count}</p>
                    <p className="text-sm capitalize">{status.toLowerCase()}</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Users Tab */}
      {activeTab === 'users' && (
        <div className="space-y-6">
          {/* Create User Form */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>User Management</CardTitle>
              <Button onClick={() => setShowCreateUser(!showCreateUser)}>
                <Plus className="h-4 w-4 mr-2" />
                Add User
              </Button>
            </CardHeader>
            {showCreateUser && (
              <CardContent>
                <form onSubmit={handleCreateUser} className="flex gap-4 items-end">
                  <div className="flex-1">
                    <label className="text-sm font-medium mb-1 block">Email</label>
                    <Input
                      type="email"
                      placeholder="user@example.com"
                      value={newUserEmail}
                      onChange={(e) => setNewUserEmail(e.target.value)}
                      required
                    />
                  </div>
                  <div className="flex-1">
                    <label className="text-sm font-medium mb-1 block">Password</label>
                    <Input
                      type="password"
                      placeholder="••••••••"
                      value={newUserPassword}
                      onChange={(e) => setNewUserPassword(e.target.value)}
                      required
                    />
                  </div>
                  <div className="w-32">
                    <label className="text-sm font-medium mb-1 block">Role</label>
                    <select
                      value={newUserRole}
                      onChange={(e) => setNewUserRole(e.target.value as 'USER' | 'ADMIN')}
                      className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
                    >
                      <option value="USER">User</option>
                      <option value="ADMIN">Admin</option>
                    </select>
                  </div>
                  <Button type="submit" isLoading={creating}>
                    Create
                  </Button>
                </form>
              </CardContent>
            )}
          </Card>

          {/* Users Table */}
          <Card>
            <CardContent className="p-0">
              <table className="w-full">
                <thead className="border-b border-border">
                  <tr className="text-left text-sm text-muted-foreground">
                    <th className="p-4">User</th>
                    <th className="p-4">Role</th>
                    <th className="p-4">Projects</th>
                    <th className="p-4">Documents</th>
                    <th className="p-4">Tokens Used</th>
                    <th className="p-4">Requests</th>
                    <th className="p-4">Joined</th>
                    <th className="p-4"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {userStats.map((user) => (
                    <tr key={user.user_id} className="hover:bg-secondary/50">
                      <td className="p-4">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center text-primary-foreground text-sm font-medium">
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
                            'px-2 py-1 rounded text-xs font-medium',
                            user.role === 'ADMIN'
                              ? 'bg-primary/10 text-primary'
                              : 'bg-secondary text-muted-foreground'
                          )}
                        >
                          <option value="USER">User</option>
                          <option value="ADMIN">Admin</option>
                        </select>
                      </td>
                      <td className="p-4">{user.project_count}</td>
                      <td className="p-4">{user.document_count}</td>
                      <td className="p-4">
                        {formatNumber(user.total_input_tokens + user.total_output_tokens)}
                      </td>
                      <td className="p-4">{user.total_requests}</td>
                      <td className="p-4 text-muted-foreground text-sm">
                        {formatRelativeTime(user.created_at)}
                      </td>
                      <td className="p-4">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDeleteUser(user.user_id, user.email)}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Documents Tab */}
      {activeTab === 'documents' && (
        <Card>
          <CardHeader>
            <CardTitle>All Documents</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-center py-8">
              Document management view coming soon
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
