import type { ReactNode } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { cn } from '@/lib/utils'

interface ResizableShellProps {
  left: ReactNode
  main: ReactNode
  storageKey?: string
  className?: string
}

export function ResizableShell({
  left,
  main,
  storageKey = 'flexsearch-panels',
  className,
}: ResizableShellProps) {
  return (
    <Group
      orientation="horizontal"
      className={cn('min-h-[480px]', className)}
      id={storageKey}
      defaultLayout={{ left: 35, main: 65 }}
    >
      <Panel id="left" defaultSize={35} minSize={25} maxSize={55} collapsible>
        <div className="h-full overflow-auto pr-2">{left}</div>
      </Panel>
      <Separator className="w-1 bg-border hover:bg-primary/20" />
      <Panel id="main" minSize={35}>
        <div className="h-full overflow-auto pl-2">{main}</div>
      </Panel>
    </Group>
  )
}
