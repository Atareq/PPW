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

Create `.env` file according to the `.env.example` file:


### 5. Setup PostgreSQL

Create database and user:

---

### 6. Run migrations

---

### 7. Run server

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
