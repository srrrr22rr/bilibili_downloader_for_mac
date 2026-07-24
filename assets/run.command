#!/bin/zsh
set -u

# Resolve everything from this signed app bundle. The new Mac does not need
# Homebrew, Python, FFmpeg, the source repository, or a fixed install path.
readonly RESOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
readonly CLI="$RESOURCE_DIR/runtime/bstation-downloader-cli"

PATH="/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
export LANG="${LANG:-zh_CN.UTF-8}"

if [[ ! -x "$CLI" ]]; then
    echo "应用包不完整：找不到内置下载器。"
    echo "请重新下载完整的发布 ZIP，不要单独复制包内文件。"
    status=1
else
    "$CLI"
    status=$?
fi

echo
if [[ -t 0 ]]; then
    echo "按任意键关闭此窗口。"
    read -k 1
    echo
fi
exit "$status"
