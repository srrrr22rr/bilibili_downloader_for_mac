#!/bin/zsh
set -euo pipefail

# Reproducible Apple Silicon release build. All generated files stay under
# this staging directory; the script never installs or modifies /Applications.
readonly PACKAGE_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
readonly BUILD_VENV="$PACKAGE_ROOT/.venv-build"
readonly BUILD_ROOT="$PACKAGE_ROOT/build"
readonly PYI_DIST="$BUILD_ROOT/pyinstaller-dist"
readonly PYI_WORK="$BUILD_ROOT/pyinstaller-work"
readonly FFMPEG_BUNDLE_PATH="$BUILD_ROOT/ffmpeg"
readonly APP_PATH="$PACKAGE_ROOT/dist/b站downloader.app"
readonly VERSION="${VERSION:-1.1.0}"
readonly BUILD_NUMBER="${BUILD_NUMBER:-11000}"
readonly ZIP_PATH="$PACKAGE_ROOT/dist/bilibili-downloader-${VERSION}-macos-arm64.zip"
readonly LEGACY_ZIP_PATH="$PACKAGE_ROOT/dist/b站downloader-${VERSION}-macos-arm64.zip"
readonly BOOTSTRAP_PYTHON="${BOOTSTRAP_PYTHON:-/usr/bin/python3}"

mkdir -p "$BUILD_ROOT" "$PACKAGE_ROOT/dist"
export PIP_CACHE_DIR="$BUILD_ROOT/pip-cache"
export PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-cache"
export PYTHONPYCACHEPREFIX="$BUILD_ROOT/python-cache"

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "错误：arm64 发布包必须在 Apple Silicon Mac 上构建。" >&2
    exit 1
fi
if [[ ! -x "$BOOTSTRAP_PYTHON" ]]; then
    echo "错误：找不到构建用 Python：$BOOTSTRAP_PYTHON" >&2
    exit 1
fi

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
    "$BOOTSTRAP_PYTHON" -m venv "$BUILD_VENV"
fi
"$BUILD_VENV/bin/python" -m pip install --disable-pip-version-check \
    -r "$PACKAGE_ROOT/requirements-build.txt"

(
    cd "$PACKAGE_ROOT/vendor"
    /usr/bin/shasum -a 256 -c yt-dlp.sha256
)
chmod 755 "$PACKAGE_ROOT/vendor/yt-dlp"

unset IMAGEIO_FFMPEG_EXE
readonly FFMPEG_PATH="$(
    "$BUILD_VENV/bin/python" -c \
        'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'
)"
if [[ ! -x "$FFMPEG_PATH" ]]; then
    echo "错误：imageio-ffmpeg 未提供可执行文件。" >&2
    exit 1
fi
if [[ "$(lipo -archs "$FFMPEG_PATH")" != *arm64* ]]; then
    echo "错误：内置 FFmpeg 不包含 arm64 架构。" >&2
    exit 1
fi
readonly FFMPEG_ENCODERS="$(
    "$FFMPEG_PATH" -hide_banner -encoders 2>/dev/null
)"
if [[ "$FFMPEG_ENCODERS" != *libmp3lame* ]]; then
    echo "错误：内置 FFmpeg 缺少 libmp3lame，无法生成 MP3 V0。" >&2
    exit 1
fi
if [[ "$FFMPEG_ENCODERS" != *" flac "* ]]; then
    echo "错误：内置 FFmpeg 缺少 FLAC 编码器。" >&2
    exit 1
fi

"$BUILD_VENV/bin/python" "$PACKAGE_ROOT/scripts/build_icon.py" \
    "$PACKAGE_ROOT/assets/icon-1024-clean.png" \
    "$BUILD_ROOT/bilibili-downloader.icns"
cp "$FFMPEG_PATH" "$FFMPEG_BUNDLE_PATH"
chmod 755 "$FFMPEG_BUNDLE_PATH"

/bin/rm -rf "$PYI_DIST" "$PYI_WORK" "$APP_PATH"
"$BUILD_VENV/bin/python" -m PyInstaller \
    --noconfirm \
    --clean \
    --onedir \
    --console \
    --target-arch arm64 \
    --name bstation-downloader-cli \
    --distpath "$PYI_DIST" \
    --workpath "$PYI_WORK" \
    --specpath "$BUILD_ROOT" \
    --paths "$PACKAGE_ROOT/src" \
    --add-binary "$PACKAGE_ROOT/vendor/yt-dlp:bin" \
    --add-binary "$FFMPEG_BUNDLE_PATH:bin" \
    "$PACKAGE_ROOT/src/bilibili_ytdlp_macos.py"

mkdir -p \
    "$APP_PATH/Contents/MacOS" \
    "$APP_PATH/Contents/Resources"
cp "$PACKAGE_ROOT/assets/Info.plist" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
    "Set :CFBundleShortVersionString $VERSION" \
    "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
    "Set :CFBundleVersion $BUILD_NUMBER" \
    "$APP_PATH/Contents/Info.plist"

xcrun clang \
    -arch arm64 \
    -mmacosx-version-min=14.0 \
    -Os \
    "$PACKAGE_ROOT/assets/launcher.c" \
    -o "$APP_PATH/Contents/MacOS/bstation-downloader"

cp "$PACKAGE_ROOT/assets/run.command" \
    "$APP_PATH/Contents/Resources/run.command"
cp "$BUILD_ROOT/bilibili-downloader.icns" \
    "$APP_PATH/Contents/Resources/bilibili-downloader.icns"
cp "$PACKAGE_ROOT/README.md" \
    "$APP_PATH/Contents/Resources/使用说明.md"
/usr/bin/ditto \
    "$PACKAGE_ROOT/docs" \
    "$APP_PATH/Contents/Resources/docs"
cp "$PACKAGE_ROOT/THIRD-PARTY-NOTICES.md" \
    "$APP_PATH/Contents/Resources/THIRD-PARTY-NOTICES.md"
cp "$PACKAGE_ROOT/LICENSE" \
    "$APP_PATH/Contents/Resources/LICENSE"
/usr/bin/ditto \
    "$PACKAGE_ROOT/licenses" \
    "$APP_PATH/Contents/Resources/licenses"
/usr/bin/ditto \
    "$PYI_DIST/bstation-downloader-cli" \
    "$APP_PATH/Contents/Resources/runtime"
chmod 755 \
    "$APP_PATH/Contents/MacOS/bstation-downloader" \
    "$APP_PATH/Contents/Resources/run.command" \
    "$APP_PATH/Contents/Resources/runtime/bstation-downloader-cli"

# Screenshots copied from Finder can carry sign-blocking resource forks.
# Clean only this newly staged bundle; the source screenshots stay untouched.
/usr/bin/xattr -cr "$APP_PATH"

# PyInstaller signs collected arm64 Mach-O files ad hoc. Seal the outer bundle
# as well; a Developer ID identity can replace "-" in the documented release
# workflow without changing bundle contents.
/usr/bin/codesign \
    --force \
    --deep \
    --sign "${CODESIGN_IDENTITY:--}" \
    "$APP_PATH"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_PATH"

/bin/rm -f \
    "$ZIP_PATH" \
    "$ZIP_PATH.sha256" \
    "$LEGACY_ZIP_PATH" \
    "$LEGACY_ZIP_PATH.sha256"
/usr/bin/ditto \
    -c -k --norsrc --keepParent \
    "$APP_PATH" \
    "$ZIP_PATH"
(
    cd "$PACKAGE_ROOT/dist"
    /usr/bin/shasum -a 256 "$(basename "$ZIP_PATH")"
) > "$ZIP_PATH.sha256"

echo
echo "构建完成："
echo "  $APP_PATH"
echo "  $ZIP_PATH"
echo "  $ZIP_PATH.sha256"
