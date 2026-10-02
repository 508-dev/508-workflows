export type AgentScheduleRunResponse = {
  status?: string
  job_id?: string | null
  dispatch_pending?: boolean
  run?: {
    id?: string
    status?: string
    job_id?: string | null
  }
}

export function agentScheduleRunToastMessage(response: AgentScheduleRunResponse) {
  if (response.status === "queued") return "Queued recurring agent schedule run"
  if (response.status === "already_queued") return "Recurring agent schedule run is already queued"
  if (response.status === "already_requested")
    return "A recent recurring agent schedule run already exists"
  return "Recurring agent schedule run request accepted"
}
