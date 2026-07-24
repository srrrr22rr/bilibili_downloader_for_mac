#!/bin/zsh
set -euo pipefail

readonly PACKAGE_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
readonly APP_PATH="${1:-$PACKAGE_ROOT/dist/b站downloader.app}"
readonly MAIN="$APP_PATH/Contents/MacOS/bstation-downloader"
readonly RUNTIME="$APP_PATH/Contents/Resources/runtime"
readonly CLI="$RUNTIME/bstation-downloader-cli"
readonly YTDLP="$RUNTIME/_internal/bin/yt-dlp"
readonly FFMPEG="$RUNTIME/_internal/bin/ffmpeg"
readonly ICON="$APP_PATH/Contents/Resources/bilibili-downloader.icns"
readonly SMOKE_ROOT="$(mktemp -d)"
readonly VERSION="$(/usr/libexec/PlistBuddy -c \
    'Print :CFBundleShortVersionString' "$APP_PATH/Contents/Info.plist")"
readonly ZIP_PATH="$PACKAGE_ROOT/dist/b站downloader-${VERSION}-macos-arm64.zip"

cleanup() {
    /bin/chmod -R u+w "$SMOKE_ROOT" 2>/dev/null || true
    /bin/rm -rf "$SMOKE_ROOT"
}
trap cleanup EXIT

for required in "$MAIN" "$CLI" "$YTDLP" "$FFMPEG"; do
    if [[ ! -x "$required" ]]; then
        echo "缺少可执行文件：$required" >&2
        exit 1
    fi
done

/usr/bin/plutil -lint "$APP_PATH/Contents/Info.plist"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_PATH"

readonly ICON_PNG="$SMOKE_ROOT/icon.png"
/usr/bin/sips -s format png "$ICON" --out "$ICON_PNG" >/dev/null
"$PACKAGE_ROOT/.venv-build/bin/python" -c \
    'from PIL import Image
import sys
image = Image.open(sys.argv[1]).convert("RGBA")
assert image.getpixel((0, 0))[3] == 0, "App 图标左上角不是透明背景"
assert image.getpixel((image.width - 1, 0))[3] == 0, "App 图标右上角不是透明背景"
assert image.getpixel((0, image.height - 1))[3] == 0, "App 图标左下角不是透明背景"
assert image.getpixel((image.width - 1, image.height - 1))[3] == 0, "App 图标右下角不是透明背景"' \
    "$ICON_PNG"

for binary in "$MAIN" "$CLI" "$YTDLP" "$FFMPEG"; do
    if [[ "$(lipo -archs "$binary")" != *arm64* ]]; then
        echo "缺少 arm64 架构：$binary" >&2
        exit 1
    fi
done

for forbidden_path in "$PACKAGE_ROOT" "${HOME:?HOME 未设置}/"; do
    if /usr/bin/grep -R -a -F -q "$forbidden_path" "$APP_PATH"; then
        echo "发布包包含开发机绝对路径：$forbidden_path" >&2
        exit 1
    fi
done

while IFS= read -r binary; do
    if /usr/bin/file "$binary" | /usr/bin/grep -q 'Mach-O'; then
        dependencies="$(otool -L "$binary" | /usr/bin/sed '1d')"
        if [[ "$dependencies" == *"/Users/"* ||
              "$dependencies" == *"/opt/homebrew/"* ||
              "$dependencies" == *"/usr/local/"* ]]; then
            echo "Mach-O 包含外部依赖：$binary" >&2
            echo "$dependencies" >&2
            exit 1
        fi
    fi
done < <(/usr/bin/find "$APP_PATH/Contents" -type f -print)

readonly DRY_RUN_OUTPUT="$(
    BSTATION_LAUNCHER_DRY_RUN=1 "$MAIN"
)"
if [[ "$DRY_RUN_OUTPUT" != "$APP_PATH/Contents/Resources/run.command" ]]; then
    echo "Finder 启动器未解析到包内 run.command。" >&2
    exit 1
fi

if [[ ! -f "$ZIP_PATH" || ! -f "$ZIP_PATH.sha256" ]]; then
    echo "缺少发布 ZIP 或 SHA-256 文件。" >&2
    exit 1
fi
(
    cd "$PACKAGE_ROOT/dist"
    /usr/bin/shasum -a 256 -c "$(basename "$ZIP_PATH.sha256")"
)
mkdir -p "$SMOKE_ROOT/archive"
/usr/bin/ditto -x -k "$ZIP_PATH" "$SMOKE_ROOT/archive"
readonly ARCHIVED_APP="$SMOKE_ROOT/archive/b站downloader.app"
/usr/bin/codesign --verify --deep --strict "$ARCHIVED_APP"
/bin/chmod -R a-w "$ARCHIVED_APP"
readonly SMOKE_CLI="$ARCHIVED_APP/Contents/Resources/runtime/"\
"bstation-downloader-cli"
readonly SMOKE_HOME="$SMOKE_ROOT/clean-home"
mkdir -p "$SMOKE_HOME"
readonly REPORT="$SMOKE_ROOT/self-test.json"
/usr/bin/env -i \
    HOME="$SMOKE_HOME" \
    LANG="zh_CN.UTF-8" \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    TMPDIR="$SMOKE_ROOT" \
    "$SMOKE_CLI" --self-test > "$REPORT"

/usr/bin/grep -q '"frozen": true' "$REPORT"
/usr/bin/grep -q \
    "$SMOKE_HOME/Downloads/b站downloader" \
    "$REPORT"
/usr/bin/grep -q \
    "$SMOKE_HOME/Library/Caches/b站downloader" \
    "$REPORT"
/usr/bin/grep -q '2026.07.04' "$REPORT"

readonly FFMPEG_ENCODERS="$(
    "$FFMPEG" -hide_banner -encoders 2>/dev/null
)"
if [[ "$FFMPEG_ENCODERS" != *libmp3lame* ]]; then
    echo "发布包 FFmpeg 缺少 libmp3lame。" >&2
    exit 1
fi
if [[ "$FFMPEG_ENCODERS" != *" flac "* ]]; then
    echo "发布包 FFmpeg 缺少 FLAC。" >&2
    exit 1
fi

readonly MEDIA_ROOT="$SMOKE_ROOT/media"
mkdir -p "$MEDIA_ROOT"
"$FFMPEG" -hide_banner -loglevel error \
    -f lavfi -i "sine=frequency=1000:duration=0.25" \
    -c:a flac "$MEDIA_ROOT/source.flac"
"$FFMPEG" -hide_banner -loglevel error \
    -i "$MEDIA_ROOT/source.flac" \
    -c:a libmp3lame -q:a 0 -ac 2 "$MEDIA_ROOT/audio.mp3"
"$FFMPEG" -hide_banner -loglevel error \
    -i "$MEDIA_ROOT/source.flac" \
    -c:a flac "$MEDIA_ROOT/audio.flac"
"$FFMPEG" -hide_banner -loglevel error \
    -f lavfi -i "color=c=black:s=160x90:d=0.25" \
    -c:v libx264 -pix_fmt yuv420p -an "$MEDIA_ROOT/video.mp4"
"$FFMPEG" -hide_banner -loglevel error \
    -i "$MEDIA_ROOT/source.flac" \
    -c:a aac "$MEDIA_ROOT/audio.m4a"
"$FFMPEG" -hide_banner -loglevel error \
    -i "$MEDIA_ROOT/video.mp4" \
    -i "$MEDIA_ROOT/audio.m4a" \
    -map 0:v:0 -map 1:a:0 -c copy -shortest "$MEDIA_ROOT/merged.mp4"
for media in \
        "$MEDIA_ROOT/audio.mp3" \
        "$MEDIA_ROOT/audio.flac" \
        "$MEDIA_ROOT/merged.mp4"; do
    "$FFMPEG" -hide_banner -loglevel error \
        -i "$media" -f null -
done

echo "发布包验证通过："
echo "  架构：arm64"
echo "  签名：ad-hoc/指定 identity 完整"
echo "  运行：只读 App + 空 HOME + 最小 PATH"
echo "  工具：内置 yt-dlp、FFmpeg、MP3、FLAC"
echo "  媒体：MP3/FLAC 编码、MP4 音视频合并与解码"
