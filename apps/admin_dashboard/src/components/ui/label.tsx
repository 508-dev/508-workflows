import type * as React from "react"

import { cn } from "@/lib/utils"

function Label({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      data-slot="label"
      className={cn("grid gap-1.5 text-xs font-bold text-muted-foreground", className)}
      {...props}
    />
  )
}

export { Label }
