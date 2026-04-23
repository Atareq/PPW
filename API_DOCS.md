# API Docs

Internal API documentation is exposed through drf-spectacular.

## Where To Access It

- Swagger UI in local development: `/api/docs/swagger/`
- Raw OpenAPI schema: `/api/schema/`

## Access Policy

- `/api/docs/swagger/` is mounted only when `DEBUG=True`
- `/api/schema/` is public when `DEBUG=True`
- `/api/schema/` requires any authenticated user when `DEBUG=False`

## Authentication

Swagger uses HTTP Bearer auth with JWT access tokens.

- In Swagger UI, open `Authorize` and paste an access token
- For direct schema requests outside debug:

```bash
curl \
  -H "Authorization: Bearer <access_token>" \
  http://localhost:8000/api/schema/
```

## Validate The Schema

Run:

```bash
python manage.py spectacular --validate --settings ppw.settings.local
```

Review any warnings after adding or changing endpoints. The current remaining warnings are enum naming collisions for repeated choice sets; they are benign, but new warnings should be checked before assuming the schema is complete.
