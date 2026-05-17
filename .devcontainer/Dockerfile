FROM python:3.11-slim

ARG CLAUDE_CODE_VERSION=latest

# Install basic development tools (without nodejs/npm from apt)
# Retry logic for transient network failures
RUN apt-get update && \
    for i in 1 2 3; do \
    apt-get install -y --no-install-recommends \
    build-essential git procps sudo curl zsh gh postgresql-client \
    iptables ipset iproute2 dnsutils vim locales nodejs npm \
    # Playwright Chromium system dependencies
    libglib2.0-0t64 libnss3 libnspr4 libdbus-1-3 libatk1.0-0t64 \
    libatk-bridge2.0-0t64 libcups2t64 libxcb1 libxkbcommon0 \
    libatspi2.0-0t64 libx11-6 libxcomposite1 libxdamage1 libxext6 \
    libxfixes3 libxrandr2 libgbm1 libdrm2 libcairo2 libpango-1.0-0 \
    libpangocairo-1.0-0 libasound2t64 \
    && break || { echo "Retry $i..."; sleep 5; apt-get update; }; \
    done && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Generate locale
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen en_US.UTF-8

# Docker CLI client only (no daemon) — the work-sandbox manager shells
# out to `docker` against the sandbox-dind sidecar via DOCKER_HOST.
ARG DOCKER_CLI_VERSION=27.5.1
RUN ARCH="$(uname -m)" && \
    curl -fsSL "https://download.docker.com/linux/static/stable/${ARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar xz -C /tmp && \
    mv /tmp/docker/docker /usr/local/bin/docker && \
    rm -rf /tmp/docker

ARG USERNAME=vscode
ARG GROUPNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

RUN groupadd -g $USER_GID $USERNAME \
    && useradd -m -u $USER_UID -g $USER_GID -d /home/$USERNAME -s /bin/bash $USERNAME

# Persist bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
    && mkdir /commandhistory \
    && touch /commandhistory/.bash_history \
    && chown -R $USER_UID:$USER_GID /commandhistory \
    && echo $SNIPPET >> /home/$USERNAME/.bashrc

# Set up npm global directory
RUN mkdir -p /usr/local/share/npm-global && \
    chown -R $USER_UID:$USER_GID /usr/local/share
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH=$PATH:/usr/local/share/npm-global/bin

# Create workspace and config directories
RUN mkdir -p /workspace /home/$USERNAME/.claude && \
    chown -R $USER_UID:$USER_GID /workspace /home/$USERNAME/.claude

# Set up non-root user
USER $USERNAME

# Install uv (fast Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH=/home/$USERNAME/.local/bin:$PATH

# Install pnpm
RUN npm install -g pnpm

# Install Claude Code
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Install homebrew (for dotfiles)
RUN mkdir -p $HOME/.linuxbrew \
    && git clone https://github.com/Homebrew/brew $HOME/.linuxbrew/Homebrew \
    && mkdir -p $HOME/.linuxbrew/bin \
    && ln -s $HOME/.linuxbrew/Homebrew/bin/brew $HOME/.linuxbrew/bin/brew \
    && eval "$($HOME/.linuxbrew/bin/brew shellenv)"

ENV PATH=/home/$USERNAME/.linuxbrew/bin:/home/$USERNAME/.linuxbrew/sbin:$PATH

RUN brew install openssl@3 --force-bottle
RUN brew install git-delta --force-bottle

# Copy and set up firewall script (for Claude Code)
COPY scripts/init-firewall.sh /usr/local/bin/
USER root
RUN chmod +x /usr/local/bin/init-firewall.sh && \
    echo "$USERNAME ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh" > /etc/sudoers.d/firewall && \
    chmod 0440 /etc/sudoers.d/firewall && \
    echo "$USERNAME ALL=(root) NOPASSWD: /usr/bin/apt-get" > /etc/sudoers.d/apt && \
    chmod 0440 /etc/sudoers.d/apt

USER $USERNAME
