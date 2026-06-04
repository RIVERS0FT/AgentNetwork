FROM python:3.12-slim

WORKDIR /app

# 安装依赖
RUN pip install --no-cache-dir fastapi uvicorn requests anthropic prometheus_client elasticsearch aiohttp

# 复制 Agent 运行时
COPY agent_network/ /app/agent_network/
COPY tools/ /app/tools/
COPY skills/ /app/skills/
COPY agent_server.py /app/
COPY message_bus.py /app/
COPY packet_monitor_server.py /app/
COPY log_collector_server.py /app/

# 每个容器通过环境变量注入 Agent 身份
ENV AGENT_ID="agent-001"
ENV AGENT_ROLE="scout"
ENV AGENT_NAME="侦察兵"
ENV MESSAGE_BUS_URL="http://host.docker.internal:9000"

EXPOSE 8000

CMD ["python", "agent_server.py"]
