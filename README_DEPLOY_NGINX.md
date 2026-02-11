# Production deploy with Nginx + Let's Encrypt (Docker)

Target domain: https://cpatterns.lifesev.info

## 1) DNS
Create an A record:
- cpatterns.lifesev.info -> <YOUR_SERVER_PUBLIC_IP>

Wait for DNS propagation.

## 2) Firewall
Open ports:
- 80/tcp
- 443/tcp

## 3) Start
From the project folder on the server:
- docker compose up -d --build
- then run: ./scripts/01_init_https.sh

## 4) Update
./scripts/02_update.sh
