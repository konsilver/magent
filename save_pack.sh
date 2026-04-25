docker save \
  jingxin-agent-frontend:latest \
  jingxin-agent-backend:latest \
  grafana/grafana:latest \
  prom/prometheus:latest \
  postgres:15-alpine \
  jaegertracing/all-in-one:latest \
  -o jingxin-images.tar

gzip -f jingxin-images.tar
