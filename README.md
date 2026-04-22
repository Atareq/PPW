# PPW (Bird Auction & Marketplace Platform)

## 📌 Overview

PPW is a scalable backend system designed for:

* **Bird auctions (live bidding system)**
* **E-commerce marketplace** for bird supplements and accessories

The platform supports a unified user model with **role switching (customer ↔ seller)** and shared features such as comments, assets, and listings.

---

## 🏗 Architecture Principles

* **Modular Django apps structure**
* **Asset-centric design** (core entity for birds & products)
* **Separation of concerns** (assets, users, logs, etc.)
* **Scalable foundation** for future real-time bidding and payments

---

## ⚙️ Tech Stack

* Python 3.11 (managed via pyenv)
* Django
* Django REST Framework
* PostgreSQL
* pre-commit (code quality automation)

---

## 🚀 Setup Instructions

### 1. Clone the repository

```
git clone git@github.com:Atareq/PPW.git
cd PPW
```

---

### 2. Setup Python environment

** You can use `pyenv` or `venv`**
```
pyenv install 3.11.8
pyenv virtualenv 3.11.8 ppw-env
pyenv local ppw-env
```

---

### 3. Install dependencies

Development:

```
pip install -r requirements/dev.txt
```

---

### 4. Setup environment variables

Use `.env.example` as the template for every environment:

```bash
cp .env.example .env
```

For production, create `.env.prod` from the same template and switch:

- `DJANGO_SETTINGS_MODULE=ppw.settings.production`
- `DEBUG=False`
- database and host values to the production ones


### 5. Setup PostgreSQL

Create database and user:

---

### 6. Run migrations

---

### 7. Run server

This project uses one shared Compose base file plus environment-specific
overrides.

Local:

```bash
docker compose -f compose.yaml -f compose.local.yaml up --build
```

Production:

```bash
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

The shared services are:

- `app` for Django/Gunicorn
- `celery_worker` for task execution
- `celery_beat` for scheduled jobs
- `redis` as the broker/backend

Local also adds:

- `db` for PostgreSQL

Production also adds:

- `nginx` in front of the Django app

The periodic auction closing job runs once per minute through Celery Beat.

### 8. Craete super_user

python manage.py shell_plus


from apps.authentication.models import UserAccount

user = UserAccount.objects.create_superuser(
    username="super_user",
    phone_number="012345678",
    password="123",
)

print("Superuser created:", user.id)

---------------------------------------------------------

---

## 🔧 Development Tools

### Pre-commit hooks

Install hooks:

```
pre-commit install
```

Run manually:

```
pre-commit run --all-files
```

Includes:

* black (formatter)
* isort (imports)
* flake8 (linting)
* pytest (test suite)

---

## 🧠 Core Concepts

### Asset-Based System

All marketplace entities derive from a central **Asset model**:

* Birds
* Product items

This allows:

* unified comments
* shared images
* consistent listings

---

### Role Switching

A single user can act as:

* Customer
* Seller

Without re-authentication, while maintaining:

* separate wallets (future)
* isolated logs and actions

---


## ⚠️ Notes

* No payment system implemented yet (logs only)
* No KYC or reporting system (by design for Phase 1)
* Images stored in S3 (only paths saved in DB)

---


## 📄 License

Private project (for now)

---
