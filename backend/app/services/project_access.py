"""Project access checks for owner vs admin hierarchy."""

from uuid import UUID

from app.db.models import Project, User, UserRole


def has_admin_access(user: User) -> bool:
    """FlexSearch admin or infra-hub admin."""
    return user.role in (UserRole.INFRA_ADMIN, UserRole.ADMIN)


def user_can_access_project(user: User, project: Project) -> bool:
    """Owners and admins may access any project."""
    if has_admin_access(user):
        return True
    return project.owner_id == user.id


def user_owns_project(user: User, project: Project) -> bool:
    return project.owner_id == user.id
