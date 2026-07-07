export type UserRole = 'INFRA_ADMIN' | 'ADMIN' | 'USER'

export function hasAdminAccess(role: string | undefined): boolean {
  return role === 'INFRA_ADMIN' || role === 'ADMIN'
}

export function isInfraAdmin(role: string | undefined): boolean {
  return role === 'INFRA_ADMIN'
}

export function canAdministerUser(
  currentRole: string | undefined,
  targetRole: string
): boolean {
  if (targetRole === 'INFRA_ADMIN') return false
  if (isInfraAdmin(currentRole)) return true
  if (currentRole === 'ADMIN') return targetRole === 'USER'
  return false
}

export function canDeleteUser(
  currentRole: string | undefined,
  targetRole: string
): boolean {
  if (targetRole === 'INFRA_ADMIN') return false
  if (targetRole === 'ADMIN') return isInfraAdmin(currentRole)
  return hasAdminAccess(currentRole)
}
