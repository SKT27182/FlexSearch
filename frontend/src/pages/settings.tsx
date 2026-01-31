import { useState } from 'react';
import { Settings, User, Bell, Palette, Key, Save } from 'lucide-react';
import { useAuthStore } from '@/stores';
import { Button, Input, Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui';
import { cn } from '@/lib/utils';

export function SettingsPage() {
  const { user, logout } = useAuthStore();
  const [activeTab, setActiveTab] = useState<'profile' | 'appearance' | 'notifications'>('profile');

  const tabs = [
    { id: 'profile', label: 'Profile', icon: User },
    { id: 'appearance', label: 'Appearance', icon: Palette },
    { id: 'notifications', label: 'Notifications', icon: Bell },
  ] as const;


  return (
    <div className="p-8 animate-fade-in max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <div className="p-3 rounded-xl bg-gradient-to-br from-primary to-accent">
          <Settings className="h-6 w-6 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-3xl font-bold">Settings</h1>
          <p className="text-muted-foreground">Manage your account and preferences</p>
        </div>
      </div>

      <div className="flex gap-8">
        {/* Sidebar */}
        <nav className="w-48 space-y-1">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                activeTab === id
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-secondary'
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </nav>

        {/* Content */}
        <div className="flex-1 space-y-6">
          {activeTab === 'profile' && (
            <>
              <Card>
                <CardHeader>
                  <CardTitle>Profile Information</CardTitle>
                  <CardDescription>Update your account details</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-center gap-4 mb-6">
                    <div className="w-16 h-16 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center text-primary-foreground text-2xl font-bold">
                      {user?.email?.[0].toUpperCase()}
                    </div>
                    <div>
                      <p className="font-medium">{user?.email}</p>
                      <p className="text-sm text-muted-foreground capitalize">
                        {user?.role?.toLowerCase()} account
                      </p>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium">Email</label>
                    <Input type="email" value={user?.email || ''} disabled />
                    <p className="text-xs text-muted-foreground">
                      Email cannot be changed
                    </p>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Key className="h-5 w-5" />
                    Change Password
                  </CardTitle>
                  <CardDescription>Update your account password</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Current Password</label>
                    <Input type="password" placeholder="••••••••" />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">New Password</label>
                    <Input type="password" placeholder="••••••••" />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Confirm New Password</label>
                    <Input type="password" placeholder="••••••••" />
                  </div>
                  <Button>
                    <Save className="h-4 w-4 mr-2" />
                    Update Password
                  </Button>
                </CardContent>
              </Card>

              <Card className="border-destructive/50">
                <CardHeader>
                  <CardTitle className="text-destructive">Danger Zone</CardTitle>
                  <CardDescription>Irreversible actions</CardDescription>
                </CardHeader>
                <CardContent>
                  <Button variant="destructive" onClick={logout}>
                    Sign out of all devices
                  </Button>
                </CardContent>
              </Card>
            </>
          )}

          {activeTab === 'appearance' && (
            <Card>
              <CardHeader>
                <CardTitle>Appearance</CardTitle>
                <CardDescription>Customize how FlexSearch looks</CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Theme</label>
                  <div className="flex gap-4">
                    <button className="p-4 rounded-lg border-2 border-primary bg-background">
                      <div className="w-16 h-10 rounded bg-card border border-border mb-2" />
                      <p className="text-sm font-medium">Dark</p>
                    </button>
                    <button className="p-4 rounded-lg border border-border bg-background opacity-50 cursor-not-allowed">
                      <div className="w-16 h-10 rounded bg-white border border-gray-200 mb-2" />
                      <p className="text-sm font-medium">Light</p>
                      <p className="text-xs text-muted-foreground">Coming soon</p>
                    </button>
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">Accent Color</label>
                  <div className="flex gap-2">
                    {['#38bdf8', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444'].map((color) => (
                      <button
                        key={color}
                        className="w-8 h-8 rounded-full border-2 border-border hover:border-foreground transition-colors"
                        style={{ backgroundColor: color }}
                      />
                    ))}
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {activeTab === 'notifications' && (
            <Card>
              <CardHeader>
                <CardTitle>Notifications</CardTitle>
                <CardDescription>Configure how you receive updates</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {[
                  { label: 'Document processing complete', description: 'Get notified when documents finish processing' },
                  { label: 'Weekly usage summary', description: 'Receive a weekly summary of your token usage' },
                  { label: 'System announcements', description: 'Important updates about FlexSearch' },
                ].map(({ label, description }) => (
                  <div key={label} className="flex items-center justify-between py-3 border-b border-border last:border-0">
                    <div>
                      <p className="font-medium">{label}</p>
                      <p className="text-sm text-muted-foreground">{description}</p>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input type="checkbox" className="sr-only peer" defaultChecked />
                      <div className="w-11 h-6 bg-secondary peer-focus:ring-2 peer-focus:ring-primary rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                    </label>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
