#!/bin/bash
# 从 ADI 字标生成 macOS 应用图标（.icns），可选直接装进 .app。
#
#   ./scripts/build_icon.sh                    # 只生成 brand/adi.icns
#   ./scripts/build_icon.sh "/Applications/ADI.app"   # 生成并装进指定 App
#
# 依赖：sips、iconutil（macOS 自带）。源图必须 1024x1024。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/brand/icons/adi-mark-1024.png"
OUT="$ROOT/brand/adi.icns"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

[ -f "$SRC" ] || { echo "缺少源图：$SRC"; exit 1; }

mkdir -p "$TMP/adi.iconset"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$SRC" --out "$TMP/adi.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z "$((s*2))" "$((s*2))" "$SRC" --out "$TMP/adi.iconset/icon_${s}x${s}@2x.png" >/dev/null
done

iconutil -c icns "$TMP/adi.iconset" -o "$OUT"
echo "生成 $OUT ($(stat -f%z "$OUT") B)"

if [ $# -ge 1 ]; then
  APP="$1"
  [ -d "$APP" ] || { echo "不是 App 包：$APP"; exit 1; }
  NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$APP/Contents/Info.plist" 2>/dev/null || echo adidi)"
  NAME="${NAME%.icns}"
  cp "$OUT" "$APP/Contents/Resources/${NAME}.icns"
  touch "$APP"
  echo "已装进 $APP/Contents/Resources/${NAME}.icns（旧图标若有备份请自查）"
fi
