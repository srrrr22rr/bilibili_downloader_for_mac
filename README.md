# b站downloader for Apple Silicon

这是一个面向 Apple Silicon Mac（M1/M2/M3/M4 及后续芯片）的 Bilibili
Terminal 下载前端。发布包内置 Python 运行时、官方 yt-dlp 和 FFmpeg，
普通用户不需要安装 Homebrew、Python、FFmpeg，也不需要接触 Cookie 文本。

> 仅下载你有权访问和保存的内容，并遵守网站条款、版权规则及当地法律。
> 本项目不是 Bilibili 官方客户端，不提供权限绕过、抓包或隐藏音轨提取。

## 系统要求

- Apple Silicon（arm64）Mac；不支持 Intel Mac。
- macOS 14 Sonoma 或更高版本。
- 能正常访问 Bilibili。
- 可选：已安装并登录 Google Chrome 或 Safari，用于读取该账号原本有权
  观看的画质。

当前测试构建：

- b站downloader 2.0.0
- yt-dlp 2026.07.04
- FFmpeg 7.1 arm64（随 `imageio-ffmpeg 0.6.0` 提供）
- Python 3.9.6 arm64 运行时

## 五分钟开始

1. 下载 `b站downloader-2.0.0-macos-arm64.zip`，并核对旁边
   `.sha256` 文件中的 SHA-256。
2. 解压后，把 `b站downloader.app` 放到“应用程序”或其他你有写权限的位置。
3. 双击 App。程序会打开 Terminal，所有交互都在 Terminal 窗口中进行。
4. 选择匿名模式、Chrome 或 Safari 登录状态。
5. 粘贴 AV号、BV号、链接，或整段带标题的网页分享文字。
6. 选择分P、画质、独立音频及无音频视频，确认摘要后开始。

下载结果默认位于：

```text
~/Downloads/b站downloader/
```

失败或取消后可重试的音频源缓存位于：

```text
~/Library/Caches/b站downloader/
```

App 自身不会写入或修改自己的 bundle，因此可放在只读的“应用程序”目录。

## 第一次打开与 Gatekeeper

本地测试包只有 ad-hoc 签名，尚未使用 Apple Developer ID 公证。从 GitHub
下载到一台新 Mac 时，macOS 可能提示无法验证开发者。这不是程序内部错误。

请先尝试打开一次，然后前往：

```text
系统设置 → 隐私与安全性 → 安全性 → 仍要打开
```

只应在你已核对发布包来源和 SHA-256 后批准。不要使用删除整个系统隔离属性
之类的命令。Apple 官方说明见
[打开来自未知开发者的 Mac App](https://support.apple.com/guide/mac-help/mh40616/mac)。

真正做到互联网下载后首次双击无此步骤，需要项目维护者持有 Developer ID，
启用 Hardened Runtime，完成签名、公证和票据附加。GitHub 托管不能代替
Apple 公证。

## 登录状态与隐私

- 选择 Chrome/Safari 后，官方 yt-dlp 在本次进程中临时读取浏览器 Cookie。
- 程序不会生成 `cookies.txt`，不会把 Cookie、SESSDATA 或密码写入项目。
- 不要把 Cookie、SESSDATA、完整环境变量或钥匙串内容发送给任何人。
- 登录只会使用账号本身有权观看的画质；会员、4K、HDR 等仍由账号和视频源
  决定。

Chrome 首次读取时，macOS 可能询问是否允许访问 `Chrome Safe Storage`
钥匙串。这是解密本机 Chrome Cookie 的系统权限，不是让你输入 B 站密码。
如果 Chrome 有多个 Profile，当前版本使用 yt-dlp 默认识别的 Profile。

Safari 可能需要：

```text
系统设置 → 隐私与安全性 → 完全磁盘访问 → Terminal
```

授权后请完全退出并重新打开 Terminal/App。完成使用后可在同一位置撤销权限。

## 分P、画质和输出

多P视频会显示标题和编号：

- 直接回车：当前P。
- `A`：全部分P。
- `1,3-5`：P1、P3、P4、P5。
- `L`：显示完整分P列表。
- `N`：全不选，不启动任何下载。

画质可选最高可用、最高 4K、1080P、720P、480P；菜单 `6` 可查看当前登录
状态实际可用的格式。

普通视频会自动合并音视频，通常输出 MP4；源格式不兼容时可能输出 MKV。
另外可以选择：

- `MP3 V0`：从当前账号可取得的最佳音轨转换，属于高质量有损格式。
- `严格原生 FLAC`：只有平台实际返回 FLAC 源时才生成。FLAC 源会被无损
  重编码成标准 FLAC 文件，不是原始响应的逐字节复制，也不自动代表 Hi-Res。
  AAC、Dolby 等有损源不会被改后缀冒充无损。
- `无音频视频`：额外保留仅视频流文件。

附加文件会重复下载相应媒体流，增加流量、磁盘占用和处理时间。开始前会显示
完整计划摘要，可开始、重新选择或取消。

## 暂停、继续与临时文件

下载或 FFmpeg 转换时按 `Control+C` 会暂停整个任务进程组：

- 直接回车：恢复。
- 输入 `Y`：退出本次任务。
- 保持提示不操作：持续暂停。

yt-dlp 网络下载产生的 `.part` 文件可在相同链接和选项下续传。FFmpeg 转换
本身不能从百分比续转；取消转换时会删除不完整输出，但保留已下载的源音频，
下次重新转换。缓存成功处理后会自动清理。

## 常见问题

| 现象 | 处理 |
|---|---|
| 只能看到低清晰度 | 确认选中了已登录的浏览器和正确 Profile，再用菜单 `6` 查看格式。 |
| Chrome 登录未识别 | 先在 Chrome 打开 bilibili.com 确认登录；允许钥匙串访问。 |
| Safari 显示 `Operation not permitted` | 给 Terminal 完全磁盘访问，完全退出后重开。 |
| FLAC 被跳过 | 当前视频或账号没有返回原生 FLAC；这是严格模式的预期行为。 |
| 合并或转换失败 | 重新下载完整发布包；不要用外部 FFmpeg 替换包内文件。 |
| 磁盘不足 | 清理 `~/Downloads`；确认没有任务运行后再检查用户缓存目录。 |
| Terminal 窗口不要直接关闭 | 用 `Control+C` 暂停，再输入 `Y` 安全退出。 |

报告问题时可提供 macOS 版本、芯片型号、视频 URL 和最后 30 行日志。请先
删除用户名等个人路径，绝不要附 Cookie、SESSDATA、密码或完整环境变量。

## 开发与测试

所有打包工作都在此目录内完成，不会安装或替换 `/Applications` 中的 App。

运行源码测试：

```bash
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
```

构建 Apple Silicon App：

```bash
./scripts/build_macos_arm64.sh
```

验证只读 App、空 HOME、最小 PATH、arm64 架构、签名和内置工具：

```bash
./scripts/test_release.sh
```

产物：

```text
dist/b站downloader.app
dist/b站downloader-2.0.0-macos-arm64.zip
dist/b站downloader-2.0.0-macos-arm64.zip.sha256
```

构建脚本固定校验官方 yt-dlp SHA-256，并使用 arm64 的静态 FFmpeg。发布
前应查看 `THIRD-PARTY-NOTICES.md` 和 `licenses/`。

## 正式签名与公证

公开发布且希望首次双击无未知开发者提示时，需要 Apple Developer Program
提供的 `Developer ID Application` 身份。发布者应从内到外签名所有 Mach-O，
启用 Hardened Runtime 和安全时间戳，再使用 `notarytool` 提交、公证并
`stapler` 附加票据。官方资料：

- [PyInstaller macOS 架构与签名](https://pyinstaller.org/en/stable/feature-notes.html#macos-specific-features)
- [Apple：在 App Store 外分发](https://developer.apple.com/developer-id/)
- [Apple：公证 macOS 软件](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

未配置 Developer ID 时，构建脚本只生成可验证的 ad-hoc 签名测试包，不会
伪称已经过 Apple 公证。

## 第三方组件

发布包聚合了独立运行的 yt-dlp 和 FFmpeg，并包含 Python/Requests 运行时。
许可证、版本、来源和 FFmpeg 对应源码信息见
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) 与 `licenses/`。
