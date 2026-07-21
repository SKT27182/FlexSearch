"""Project access checks for owner vs admin hierarchy."""

from app.db.models import Project, User, UserRole


def has_admin_access(user: User) -> bool:
    """FlexSearch admin or infra-hub admin."""
    return user.role in (UserRole.INFRA_ADMIN, UserRole.ADMIN)


def user_owns_project(user: User, project: Project) -> bool:
    return project.owner_id == user.id


def user_can_access_project(user: User, project: Project) -> bool:
    """Regular API: owners only. Admins use /admin routes for other users' data."""
    return user_owns_project(user, project)


def user_can_administer_target(admin: User, target: User) -> bool:
    """INFRA_ADMIN > ADMIN > USER — admins may manage strictly lower tiers."""
    if target.role == UserRole.INFRA_ADMIN:
        return False
    if admin.role == UserRole.INFRA_ADMIN:
        return True
    if admin.role == UserRole.ADMIN:
        return target.role == UserRole.USER
    return False
