#!/usr/bin/env bash
# ============================================
# SPSE Inaproc Crawler — Server Setup Script
# Tested on: Ubuntu 22.04/24.04, Debian 12
# ============================================
set -euo pipefail

APP_NAME="spse-crawler"
APP_DIR="/opt/${APP_NAME}"
VENV_DIR="${APP_DIR}/venv"
REPO_URL=""  # Set your git repo URL here, or leave empty for manual upload
SERVICE_USER="crawler"
PORT=8000

echo "========================================"
echo " SPSE Crawler — Server Setup"
echo "========================================"

# --- 1. System dependencies ---
echo ""
echo "[1/8] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-venv python3-pip \
    nginx certbot python3-certbot-nginx \
    git curl wget \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    fonts-liberation

# --- 2. Create service user ---
echo ""
echo "[2/8] Creating service user '${SERVICE_USER}'..."
if ! id "${SERVICE_USER}" &>/dev/null; then
    sudo useradd -r -m -s /bin/bash "${SERVICE_USER}"
    echo "User '${SERVICE_USER}' created"
else
    echo "User '${SERVICE_USER}' already exists"
fi

# --- 3. Clone / copy app ---
echo ""
echo "[3/8] Setting up application directory..."
sudo mkdir -p "${APP_DIR}"
sudo chown "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

if [ -n "${REPO_URL}" ]; then
    if [ ! -d "${APP_DIR}/.git" ]; then
        sudo -u "${SERVICE_USER}" git clone "${REPO_URL}" "${APP_DIR}"
    else
        sudo -u "${SERVICE_USER}" git -C "${APP_DIR}" pull
    fi
else
    echo "  → No REPO_URL set. Copy files manually to ${APP_DIR}/"
    echo "  → Or upload the project zip and extract:"
    echo "      sudo cp spse-crawler.zip /opt/"
    echo "      sudo unzip /opt/spse-crawler.zip -d ${APP_DIR}"
fi

# --- 4. Python virtual environment ---
echo ""
echo "[4/8] Creating Python virtual environment..."
if [ ! -d "${VENV_DIR}" ]; then
    sudo -u "${SERVICE_USER}" python3 -m venv "${VENV_DIR}"
fi

echo "  → Installing Python dependencies..."
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install gunicorn

# --- 5. Playwright browsers ---
echo ""
echo "[5/8] Installing Playwright Chromium browser..."
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/playwright" install --with-deps chromium

# --- 6. Django setup ---
echo ""
echo "[6/8] Running Django migrations..."
cd "${APP_DIR}"
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/python" manage.py migrate --noinput
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/python" manage.py collectstatic --noinput 2>/dev/null || true

# --- 7. Seed production data ---
echo ""
echo "[7/8] Seeding production data (users, companies, qualifications, KBLI)..."
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/python" manage.py seed_data

# --- 8. Systemd service ---
echo ""
echo "[8/8] Installing systemd service..."
sudo tee /etc/systemd/system/${APP_NAME}.service > /dev/null <<EOF
[Unit]
Description=SPSE Inaproc Crawler & Dashboard
After=network.target
Wants=network-online.target

[Service]
Type=notify
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/bin:/usr/local/bin"
Environment="DJANGO_SETTINGS_MODULE=web_ui.settings"
Environment="DJANGO_DEBUG=0"
Environment="SPSE_LOG_LEVEL=INFO"
Environment="SPSE_HEADLESS=1"
ExecStart=${VENV_DIR}/bin/gunicorn web_ui.wsgi:application --config gunicorn.conf.py
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=on-failure
RestartSec=10
KillMode=mixed
TimeoutStopSec=30
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${APP_NAME}
sudo systemctl start ${APP_NAME}

echo ""
echo "========================================"
echo " Setup Complete!"
echo "========================================"
echo ""
echo "  App directory:  ${APP_DIR}"
echo "  Python venv:    ${VENV_DIR}"
echo "  Service:        ${APP_NAME}"
echo "  Port:           ${PORT}"
echo ""
echo "  Dashboard:      http://YOUR_SERVER_IP:${PORT}"
echo "  Admin:          http://YOUR_SERVER_IP:${PORT}/admin"
echo "  Username:       admin"
echo "  Password:       admin123"
echo ""
echo "  Service commands:"
echo "    sudo systemctl status  ${APP_NAME}"
echo "    sudo systemctl restart ${APP_NAME}"
echo "    sudo systemctl logs    ${APP_NAME} -f"
echo ""
echo "  Next: Configure Nginx reverse proxy (see DEPLOYMENT.md)"
echo ""
