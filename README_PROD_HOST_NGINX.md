# Stable v1.1 – Host Nginx Reverse Proxy

This version is designed to run behind an existing Nginx server (not Docker Nginx).

FastAPI container listens on:
127.0.0.1:8002

Nginx host should proxy:
https://cpatterns.lifesev.info -> http://127.0.0.1:8002

Start:
docker compose up -d --build

Update:
git pull
docker compose up -d --build
