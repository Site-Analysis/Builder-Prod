#!/bin/bash
# One-shot EC2 bootstrap for builder.qnit.site
# Run as ubuntu user on fresh Ubuntu 22.04 t2.micro/t3.small instance.
# DNS A records for auth.builder.qnit.site and api.builder.qnit.site
# must already point to this instance's public IP before running certbot.
set -euo pipefail

# 1. Swap (4 GB) — critical on t2.micro
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 2. Docker + Certbot
sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2 certbot python3-certbot-nginx git
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker

# 3. Data directories
sudo mkdir -p /opt/builder/data/cadastral_lake_v2
sudo mkdir -p /opt/builder/db
sudo chown -R "$USER":"$USER" /opt/builder

# 4. Clone repo (replace URL with actual repo)
# git clone https://github.com/<your-org>/builder-prod /opt/builder/app
# Alternative — rsync from local:
# rsync -avz --exclude='.git' --exclude='.env' ./ ubuntu@<EC2-IP>:/opt/builder/app/

echo ""
echo "=== Next: SCP data files ==="
echo "scp -r <local>/cadastral_lake_v2   ubuntu@<EC2-IP>:/opt/builder/data/"
echo "scp <local>/lgd_villages.parquet    ubuntu@<EC2-IP>:/opt/builder/data/"
echo "scp <local>/karnataka_lands_full.db ubuntu@<EC2-IP>:/opt/builder/db/"
echo "scp <local>/echawadi_village_list.json ubuntu@<EC2-IP>:/opt/builder/data/"
echo ""
echo "=== Then: get certs (DNS must be live) ==="
echo "sudo certbot certonly --standalone \\"
echo "  -d auth.builder.qnit.site \\"
echo "  -d api.builder.qnit.site \\"
echo "  --non-interactive --agree-tos -m admin@qnit.site"
echo ""
echo "=== Then: deploy ==="
echo "cd /opt/builder/app"
echo "cp .env.prod.example .env"
echo "# Edit .env — set KEYCLOAK_ADMIN_PASSWORD"
echo "newgrp docker  # if docker group just added"
echo "docker compose -f docker-compose.prod.yml up -d --build"
