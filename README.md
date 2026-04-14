# ♟️ Chess Web App

## 📌 Overview

A real-time chess web application built with a modern backend and containerized infrastructure.
The project demonstrates full-stack development along with DevOps practices such as container orchestration and environment-based configuration.

---

## 🧱 Tech Stack

* **Backend:** FastAPI
* **Frontend:** HTML / CSS / JavaScript
* **Real-time communication:** WebSockets
* **Database:** MySQL
* **Containerization:** Docker & Docker Compose

---

## 🚀 Features

* Real-time chess gameplay using WebSockets
* Room-based system (players and spectators)
* Game state persistence using FEN
* Role assignment (white / black / spectator)
* Reconnect handling and session continuity
* Backend + frontend served together

---

## 🏗️ Architecture

The application is currently built as a **monolith**:

* FastAPI handles HTTP + WebSocket connections
* Game logic and state management are implemented server-side
* MySQL is used as a persistent storage for users, rooms, and game state
* Frontend communicates with backend via WebSocket messages (JSON-based protocol)
* Docker Compose orchestrates backend and database services

---

## ⚙️ Environment Variables

The project uses environment variables for configuration.

A sample file is provided:

```bash
.env.sample
```

Copy it to `.env`:

```bash
cp .env.sample .env
```

Then update the values inside `.env`, especially:

* `PASSWORD`

⚠️ Note:
If you change the database password in `.env`, make sure it is also reflected in `compose.yaml`.

---

## ▶️ Run Locally

Make sure you have Docker installed.

```bash
docker compose up --build
```

Then open your browser:

```
http://localhost:8000
```

---

## 📂 Project Structure

```
.
├── static/ # Frontend assets (JS, CSS)
├── templates/ # HTML templates
├── app.py # Main FastAPI entrypoint
├── db.py # Database connection setup
├── models.py # SQLAlchemy models
├── schemas.py # Pydantic schemas
├── security.py # Auth / security logic
├── requirements.txt # Python dependencies
├── Dockerfile # Backend container config
├── compose.yaml # Docker Compose orchestration
├── .env.example # Example environment variables
├── .gitignore
└── README.md
```

---

## 🔮 Future Improvements

* 🔹 Split monolith into microservices:

  * game-service (WebSocket)
  * room-service
  * auth-service

* 🔹 Introduce Redis for caching and fast state access

* 🔹 Add authentication & user management (JWT / OAuth)

* 🔹 Implement CI/CD pipeline (GitHub Actions)

* 🔹 Push Docker images to container registry

* 🔹 Deploy to Kubernetes (Deployment + Service + Ingress)

* 🔹 Add monitoring & observability:

  * Prometheus
  * Grafana

* 🔹 Improve frontend (UI/UX, modern framework)

---

## 🧠 Learning Goals

This project was built to practice:

* Real-time systems with WebSockets
* Backend architecture with FastAPI
* Database integration and persistence
* Containerization with Docker
* Foundations for DevOps and cloud deployment

---

## 📬 Author

Alexander Sharapov
