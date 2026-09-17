# Vikingchat + OpenViking in one container.
#
# OpenViking (Python) listens on 127.0.0.1:1933 inside the container and is
# never exposed. The Node app is the public process on $PORT and talks to it.
# Mount a persistent disk at /app/.openviking to keep memories, documents and
# chat history across restarts.
FROM ghcr.io/volcengine/openviking:latest

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node --version

WORKDIR /app/vikingchat
COPY package.json server.js ./
COPY public ./public
COPY docker/start.sh /usr/local/bin/vikingchat-start
RUN chmod +x /usr/local/bin/vikingchat-start

ENV HOST=0.0.0.0 \
    PORT=3000 \
    DATA_DIR=/app/.openviking/vikingchat \
    OPENVIKING_URL=http://127.0.0.1:1933 \
    OPENVIKING_WITH_BOT=0

EXPOSE 3000
ENTRYPOINT ["/usr/local/bin/vikingchat-start"]
