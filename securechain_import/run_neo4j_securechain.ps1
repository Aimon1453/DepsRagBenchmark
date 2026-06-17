$ErrorActionPreference = "Stop"

docker rm -f neo4j-securechain *>$null

docker run -d `
  --name neo4j-securechain `
  -p 7476:7474 `
  -p 7689:7687 `
  -e NEO4J_AUTH="neo4j/password" `
  -e NEO4J_server_http_advertised__address="localhost:7476" `
  -e NEO4J_server_bolt_advertised__address="localhost:7689" `
  neo4j:5.26-community

Write-Host "neo4j-securechain starting. Bolt: bolt://localhost:7689 Browser: http://localhost:7476 (~20s warmup)."
