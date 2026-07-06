type EmptyProps = {
  children: string
  hidden: boolean
}

function Empty({ children, hidden }: EmptyProps) {
  if (hidden) return null
  return <div className="px-4 py-7 text-center text-sm text-muted-foreground">{children}</div>
}

export { Empty }
