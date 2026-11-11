import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { AssignableUser } from '@/lib/types'

/** Active users in the caller's organisation, for populating a lead "assign to" control. */
export function useAssignableUsers(base: string) {
  return useQuery({ queryKey: ['assignable-users', base], queryFn: () => api<AssignableUser[]>(`${base}/leads/assignable-users`), staleTime: 60_000 })
}
