import { Link } from 'react-router-dom';
import { FolderOpen, FileText, Activity, Plus } from 'lucide-react';
import { useAuthStore, useProjectStore } from '@/stores';
import { Card, CardHeader, CardTitle, CardContent, buttonVariants } from '@/components/ui';
import { formatRelativeTime, cn } from '@/lib/utils';

export function DashboardPage() {
  const { user } = useAuthStore();
  const { projects } = useProjectStore();

  const stats = [
    { label: 'Projects', value: projects.length, icon: FolderOpen, color: 'from-blue-500 to-cyan-500' },
    { label: 'Documents', value: projects.reduce((acc, p) => acc + (p.document_count || 0), 0), icon: FileText, color: 'from-amber-500 to-orange-500' },
  ];

  return (
    <div className="p-8 animate-fade-in">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">
          Welcome back, <span className="text-primary">{user?.email?.split('@')[0]}</span>
        </h1>
        <p className="text-muted-foreground mt-1">Here's what's happening with your projects</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        {stats.map(({ label, value, icon: Icon, color }) => (
          <Card key={label} className="overflow-hidden">
            <div className={`h-1 bg-gradient-to-r ${color}`} />
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">{label}</p>
                  <p className="text-3xl font-bold mt-1">{value}</p>
                </div>
                <div className={`p-3 rounded-xl bg-gradient-to-br ${color} bg-opacity-10`}>
                  <Icon className="h-6 w-6 text-white" />
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Projects */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-lg">Recent Projects</CardTitle>
            <Link 
              to="/projects" 
              className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
            >
              <Plus className="h-4 w-4 mr-2" />
              New Project
            </Link>
          </CardHeader>
          <CardContent>
            {projects.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <FolderOpen className="h-12 w-12 mx-auto mb-3 opacity-50" />
                <p>No projects yet</p>
                <p className="text-sm">Create your first project to get started</p>
              </div>
            ) : (
              <div className="space-y-3">
                {projects.slice(0, 5).map((project) => (
                  <Link
                    key={project.id}
                    to={`/projects/${project.id}`}
                    className="flex items-center justify-between p-3 rounded-lg hover:bg-secondary transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-lg bg-primary/10">
                        <FolderOpen className="h-4 w-4 text-primary" />
                      </div>
                      <div>
                        <p className="font-medium">{project.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {formatRelativeTime(project.updated_at)}
                        </p>
                      </div>
                    </div>
                    <Activity className="h-4 w-4 text-muted-foreground" />
                  </Link>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Getting Started */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Getting Started</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-start gap-4 p-4 rounded-lg bg-secondary/50">
                <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary text-primary-foreground font-bold text-sm">
                  1
                </div>
                <div>
                  <h3 className="font-medium">Create a Project</h3>
                  <p className="text-sm text-muted-foreground">
                    Projects organize your documents and knowledge base
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-4 p-4 rounded-lg bg-secondary/50">
                <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary text-primary-foreground font-bold text-sm">
                  2
                </div>
                <div>
                  <h3 className="font-medium">Upload Documents</h3>
                  <p className="text-sm text-muted-foreground">
                    Add PDFs, text files, or images to your project
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-4 p-4 rounded-lg bg-secondary/50">
                <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary text-primary-foreground font-bold text-sm">
                  3
                </div>
                <div>
                  <h3 className="font-medium">Query Knowledge Base</h3>
                  <p className="text-sm text-muted-foreground">
                    Ask questions and retrieve relevant information from your documents
                  </p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
