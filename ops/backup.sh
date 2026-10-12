#!/bin/sh
set -eu
umask 077

cd /opt/tapsyrma
backup_dir=/var/backups/tapsyrma
mkdir -p "$backup_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
database="$backup_dir/$stamp.database.dump"
media="$backup_dir/$stamp.media.tar.gz"
environment="$backup_dir/$stamp.env"
trap 'rm -f "$database.partial" "$media.partial" "$environment.partial"' EXIT

docker compose exec -T db pg_dump -U tapsyrma -d tapsyrma -Fc > "$database.partial"
docker compose exec -T db pg_restore -l < "$database.partial" > /dev/null
media_dir=$(docker volume inspect tapsyrma_media_data --format '{{.Mountpoint}}')
tar -C "$media_dir" -czf "$media.partial" .
tar -tzf "$media.partial" > /dev/null
cp .env "$environment.partial"

mv "$database.partial" "$database"
mv "$media.partial" "$media"
mv "$environment.partial" "$environment"
trap - EXIT
find "$backup_dir" -maxdepth 1 -type f -mtime +13 -delete
