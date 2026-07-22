#!/bin/sh
set -eu

docker compose pull api
docker compose up -d --remove-orphans
docker compose ps
