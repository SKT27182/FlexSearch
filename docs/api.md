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

**GET** `/api/documents/{project_id}`

### Upload Document

**POST** `/api/documents/upload/{project_id}`

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

**DELETE** `/api/documents/{document_id}`

---

## Sessions

### List Sessions

**GET** `/api/sessions/{project_id}`

### Create Session

**POST** `/api/sessions/{project_id}`

```json
{
  "name": "Chat Session 1"
}
```

### Delete Session

**DELETE** `/api/sessions/{session_id}`

---

## Chat

### WebSocket Connection

**WS** `/api/chat/ws/{session_id}`

Connect with access token as query param or in first message.

#### Messages

**Send:**
```json
{
  "message": "What is in these documents?"
}
```

**Receive:**
```json
// Stream start
{"type": "start"}

// Content chunks
{"type": "chunk", "content": "Based on "}
{"type": "chunk", "content": "the documents..."}

// Stream end with sources
{
  "type": "end",
  "sources": [
    {
      "filename": "doc.pdf",
      "chunk_index": 3,
      "content": "Relevant excerpt..."
    }
  ]
}

// Error
{"type": "error", "content": "Error message"}
```

### Get Chat History

**GET** `/api/chat/{session_id}`

```json
// Response
[
  {"role": "user", "content": "Hello"},
  {"role": "assistant", "content": "Hi there!"}
]
```

---

## Admin (ADMIN role required)

### System Stats

**GET** `/api/admin/stats`

```json
{
  "users": {"total": 10, "admins": 2, "regular": 8},
  "projects": 25,
  "documents": {"total": 150, "by_status": {"COMPLETED": 140, "PENDING": 10}},
  "token_usage": {
    "total_input_tokens": 1000000,
    "total_output_tokens": 500000,
    "total_requests": 5000,
    "average_latency_ms": 250
  }
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
