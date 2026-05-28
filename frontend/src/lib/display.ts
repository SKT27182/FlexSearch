/** Display name for sidebar and headers. */
export function userDisplayName(
  user: { name?: string; email?: string } | null | undefined
): string {
  if (!user) return 'User'
  const trimmed = user.name?.trim()
  if (trimmed) return trimmed
  const local = user.email?.split('@', 1)[0]
  return local || 'User'
}

export function userInitial(
  user: { name?: string; email?: string } | null | undefined
): string {
  const label = userDisplayName(user)
  return label[0]?.toUpperCase() ?? '?'
}
