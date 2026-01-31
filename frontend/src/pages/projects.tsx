import { useState } from 'react';
import { Link } from 'react-router-dom';
import { FolderOpen, Plus, Trash2, FileText, MessageSquare } from 'lucide-react';
import { useProjectStore } from '@/stores';
import { Button, Input, Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter, buttonVariants } from '@/components/ui';
import { formatRelativeTime, cn } from '@/lib/utils';

export function ProjectsPage() {
  const { projects, isLoading, createProject, deleteProject } = useProjectStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [creating, setCreating] = useState(false);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;

    setCreating(true);
    try {
      await createProject(newName.trim(), newDescription.trim() || undefined);
      setNewName('');
      setNewDescription('');
      setShowCreate(false);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (confirm(`Delete project "${name}"? This cannot be undone.`)) {
      await deleteProject(id);
    }
  };

  return (
    <div className="p-8 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold">Projects</h1>
          <p className="text-muted-foreground mt-1">Manage your knowledge bases</p>
        </div>
        <Button onClick={() => setShowCreate(!showCreate)}>
          <Plus className="h-4 w-4 mr-2" />
          New Project
        </Button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <Card className="mb-8 animate-slide-up">
          <form onSubmit={handleCreate}>
            <CardHeader>
              <CardTitle>Create New Project</CardTitle>
              <CardDescription>A project contains your documents and chat sessions</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <label htmlFor="name" className="text-sm font-medium">
                  Project Name
                </label>
                <Input
                  id="name"
                  placeholder="My Research Project"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <label htmlFor="description" className="text-sm font-medium">
                  Description (optional)
                </label>
                <Input
                  id="description"
                  placeholder="A brief description of this project"
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                />
              </div>
            </CardContent>
            <CardFooter className="flex gap-3">
              <Button type="submit" isLoading={creating}>
                Create Project
              </Button>
              <Button type="button" variant="outline" onClick={() => setShowCreate(false)}>
                Cancel
              </Button>
            </CardFooter>
          </form>
        </Card>
      )}

      {/* Projects Grid */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
        </div>
      ) : projects.length === 0 ? (
        <div className="text-center py-20">
          <FolderOpen className="h-16 w-16 mx-auto mb-4 text-muted-foreground opacity-50" />
          <h2 className="text-xl font-semibold mb-2">No projects yet</h2>
          <p className="text-muted-foreground mb-6">
            Create your first project to start building your knowledge base
          </p>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-2" />
            Create Project
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {projects.map((project) => (
            <Card key={project.id} className="group hover:border-primary/50 transition-colors">
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-gradient-to-br from-primary/20 to-accent/20">
                      <FolderOpen className="h-5 w-5 text-primary" />
                    </div>
                    <div>
                      <CardTitle className="text-lg">{project.name}</CardTitle>
                      <CardDescription className="text-xs">
                        Created {formatRelativeTime(project.created_at)}
                      </CardDescription>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="opacity-0 group-hover:opacity-100 transition-opacity"
                    onClick={() => handleDelete(project.id, project.name)}
                  >
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground line-clamp-2">
                  {project.description || 'No description'}
                </p>
              </CardContent>
              <CardFooter className="flex gap-2">
                <Link 
                  to={`/projects/${project.id}`}
                  className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), "flex-1")}
                >
                  <FileText className="h-4 w-4 mr-2" />
                  Documents
                </Link>
                <Link 
                  to={`/chat?project=${project.id}`}
                  className={cn(buttonVariants({ size: 'sm' }), "flex-1")}
                >
                  <MessageSquare className="h-4 w-4 mr-2" />
                  Chat
                </Link>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
