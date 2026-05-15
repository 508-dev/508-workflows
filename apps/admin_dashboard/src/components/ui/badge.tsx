import { cva, type VariantProps } from "class-variance-authority"
import type * as React from "react"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex min-h-[22px] items-center rounded-full border px-2 py-0.5 text-[11px] font-extrabold uppercase leading-tight",
  {
    variants: {
      variant: {
        neutral: "border-border bg-secondary text-muted-foreground",
        succeeded: "border-emerald-400/35 bg-emerald-500/15 text-emerald-300",
        failed: "border-red-400/40 bg-red-500/15 text-red-300",
        dead: "border-red-400/40 bg-red-500/15 text-red-300",
        missing: "border-red-400/40 bg-red-500/15 text-red-300",
        running: "border-amber-400/40 bg-amber-500/15 text-amber-300",
        queued: "border-teal-400/40 bg-teal-500/15 text-teal-200",
        canceled: "border-border bg-secondary text-muted-foreground",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  },
)

function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant, className }))} {...props} />
}

export { Badge }
