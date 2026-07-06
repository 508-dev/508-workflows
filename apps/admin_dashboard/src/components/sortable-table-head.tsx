import { TableHead } from "@/components/ui/table"

type SortDirection = "asc" | "desc"

type SortButtonProps = {
  label: string
  scope: string
  sort: { key: string; direction: SortDirection }
  sortKey: string
  onSort: (scope: string, key: string) => void
}

function SortButton({ label, scope, sort, sortKey, onSort }: SortButtonProps) {
  const active = sort.key === sortKey
  const arrow = sort.direction === "asc" ? "↑" : "↓"
  return (
    <button
      type="button"
      data-sort-scope={scope}
      data-sort-key={sortKey}
      className="text-left font-[inherit] text-inherit hover:text-foreground"
      onClick={() => onSort(scope, sortKey)}
    >
      {active ? `${label} ${arrow}` : label}
    </button>
  )
}

type SortableTableHeadProps = SortButtonProps & {
  className?: string
}

function SortableTableHead({
  className,
  label,
  scope,
  sort,
  sortKey,
  onSort,
}: SortableTableHeadProps) {
  const ariaSort =
    sort.key === sortKey ? (sort.direction === "asc" ? "ascending" : "descending") : "none"
  return (
    <TableHead className={className} aria-sort={ariaSort}>
      <SortButton label={label} scope={scope} sort={sort} sortKey={sortKey} onSort={onSort} />
    </TableHead>
  )
}

export { SortableTableHead }
