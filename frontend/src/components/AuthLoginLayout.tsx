import type { ReactNode } from 'react'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui'

interface AuthLoginLayoutProps {
  productName: string
  tagline: string
  icon: ReactNode
  children: ReactNode
  footer?: ReactNode
}

export function AuthLoginLayout({
  productName,
  tagline,
  icon,
  children,
  footer,
}: AuthLoginLayoutProps) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md animate-fade-in">
        <div className="flex items-center justify-center gap-3 mb-8">
          <div className="p-3 rounded-xl bg-gradient-to-br from-primary to-accent shadow-lg shadow-primary/20">
            {icon}
          </div>
          <div>
            <h1 className="text-3xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
              {productName}
            </h1>
            <p className="text-sm text-muted-foreground">{tagline}</p>
          </div>
        </div>
        <Card className="glass">
          {children}
        </Card>
        {footer && <div className="mt-4 text-center">{footer}</div>}
      </div>
    </div>
  )
}

export function AuthLoginCardHeader({ title, description }: { title: string; description: string }) {
  return (
    <CardHeader className="text-center">
      <CardTitle>{title}</CardTitle>
      <CardDescription>{description}</CardDescription>
    </CardHeader>
  )
}

export { CardContent, CardFooter }
