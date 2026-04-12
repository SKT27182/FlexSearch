# API Documentation

## Authentication

All protected endpoints require a JWT token in the `Authorization` header:
```
Authorization: Bearer <access_token>
```

### Register

**POST** `/api/auth/register`

Creates a new user. First user becomes ADMIN.

```json
// Request
{
  "email": "user@example.com",
  "password": "securepassword"
}

// Response (201)
{
  "id": "uuid",
  "email": "user@example.com",
  "role": "ADMIN",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### Login

**POST** `/api/auth/login`

Form data: `username`, `password`

```json
// Response (200)
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

### Get Current User

**GET** `/api/auth/me`

```json
// Response (200)
{
  "id": "uuid",
  "email": "user@example.com",
  "role": "USER"
}
```

---

## Projects

### List Projects

**GET** `/api/projects`

Returns all projects for the authenticated user.

### Create Project

**POST** `/api/projects`

```json
// Request
{
  "name": "My Project",
  "description": "Optional description"
}

// Response (201)
{
  "id": "uuid",
  "name": "My Project",
  "description": "Optional description",
  "owner_id": "uuid",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

### Get Project

**GET** `/api/projects/{project_id}`

### Update Project

**PATCH** `/api/projects/{project_id}`

```json
{
  "name": "New Name",
  "description": "New description"
}
```

### Delete Project

**DELETE** `/api/projects/{project_id}`

Returns 204 No Content.

---

## Documents

### List Documents

**GET** `/api/projects/{project_id}/documents`

### Upload Document

**POST** `/api/projects/{project_id}/documents/upload`

Multipart form data with `file` field.

Supported formats: PDF, TXT, MD, PNG, JPG, JPEG

```json
// Response (201)
{
  "id": "uuid",
  "filename": "document.pdf",
  "status": "PENDING",
  "size_bytes": 12345,
  "chunk_count": 0,
  "created_at": "2024-01-01T00:00:00Z"
}
```

### Delete Document

**DELETE** `/api/projects/{project_id}/documents/{document_id}`

---

## Retrieval

### Query Retrieval

**POST** `/api/retrieval/query`

```json
// Request
{
  "project_id": "4fbf8b16-33d1-4f01-8f6a-cf2a8bdc93ec",
  "query": "What does the onboarding document say about approvals?",
  "top_k": 5
}
```

```json
// Response
{
  "project_id": "4fbf8b16-33d1-4f01-8f6a-cf2a8bdc93ec",
  "query": "What does the onboarding document say about approvals?",
  "retrieval_strategy": "dense",
  "total": 2,
  "chunks": [
    {
      "chunk_id": "chunk-uuid",
      "document_id": "doc-uuid",
      "content": "Relevant excerpt...",
      "score": 0.91,
      "metadata": {
        "filename": "onboarding.pdf",
        "chunk_index": 3
      }
    }
  ]
}
```

---

## Admin (ADMIN role required)

### System Stats

**GET** `/api/admin/stats`

```json
{
  "users": {"total": 10, "admins": 2, "regular": 8},
  "projects": 25,
  "documents": {"total": 150, "by_status": {"COMPLETED": 140, "PENDING": 10}}
}
```

### User Stats

**GET** `/api/admin/users/stats/all`

Returns per-user statistics.

### Create User

**POST** `/api/admin/users`

```json
{
  "email": "newuser@example.com",
  "password": "password123",
  "role": "USER"
}
```

### Update User Role

**PATCH** `/api/admin/users/{user_id}/role?role=ADMIN`

### Delete User

**DELETE** `/api/admin/users/{user_id}`
