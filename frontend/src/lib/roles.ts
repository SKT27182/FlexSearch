export type UserRole = 'INFRA_ADMIN' | 'ADMIN' | 'USER'

export function hasAdminAccess(role: string | undefined): boolean {
  return role === 'INFRA_ADMIN' || role === 'ADMIN'
}

export function isInfraAdmin(role: string | undefined): boolean {
  return role === 'INFRA_ADMIN'
}

export function canDeleteUser(
  currentRole: string | undefined,
  targetRole: string
): boolean {
  if (targetRole === 'INFRA_ADMIN') return false
  if (targetRole === 'ADMIN') return isInfraAdmin(currentRole)
  return hasAdminAccess(currentRole)
}
