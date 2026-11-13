#!/bin/bash
# HEAD requests only: status, type and size. Nothing is downloaded.
UA="class-platform-benchmark-check/1.0"
while IFS='|' read -r label url; do
  [ -z "$label" ] && continue
  out=$(curl -sIL -m 25 -A "$UA" -o /dev/null -w "%{http_code} %{content_type} %{size_download}" "$url" 2>&1)
  hdr=$(curl -sIL -m 25 -A "$UA" "$url" 2>/dev/null | grep -i "^content-length" | tail -1 | tr -d '\r' | awk '{print $2}')
  code=$(echo "$out" | awk '{print $1}'); ctype=$(echo "$out" | awk '{print $2}')
  mb=$(python -c "import sys; n=int('${hdr:-0}' or 0); print('%.1f MB'%(n/1048576) if n else 'size unknown')")
  echo "$label | $code | $ctype | $mb | $url"
done
