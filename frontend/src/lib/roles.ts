export type UserRole = 'INFRA_ADMIN' | 'ADMIN' | 'USER'

export function hasAdminAccess(role: string | undefined): boolean {
  return role === 'INFRA_ADMIN' || role === 'ADMIN'
}

export function isInfraAdmin(role: string | undefined): boolean {
  return role === 'INFRA_ADMIN'
}
