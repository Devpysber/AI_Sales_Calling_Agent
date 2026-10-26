FROM node:20-bookworm-slim

# whatsapp-web.js drives a real Chromium; the bundled download is skipped in favour of the distro build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.js ./

EXPOSE 4050
CMD ["node", "server.js"]
