# AudioHub 🎵

![Python](https://img.shields.io/badge/python-3.9+-blue.svg)![License](https://img.shields.io/badge/license-MIT-green.svg)![Status](https://img.shields.io/badge/status-active-brightgreen)

AudioHub 是一个使用 PyQt5 构建的多功能桌面音频管理工具，旨在提供一个集音频播放、文件管理和格式转换为一体的无缝体验。

<img width="1282" height="766" alt="image" src="https://github.com/user-attachments/assets/c784aa59-ed0a-4a80-885f-9b97fd77db6b" />

---

## ✨ 主要功能

- **强大的音频播放器**:
- 支持多种主流格式 (MP3, FLAC, WAV, OGG 等)。
- 精准的播放进度控制，支持点击和拖动跳转。
- 灵活的循环模式：单曲循环、列表循环、不循环。
- **高效的文件管理**:
- 快速扫描并列出指定目录下的所有音频文件。
- 支持按文件名搜索和按标记状态筛选。
- 提供文件标记功能，方便分类和批量操作。
- 支持直接在程序内删除文件。
- **便捷的播放列表**:
- 轻松创建和管理播放列表。
- 支持从主列表添加单个或多个文件到播放列表。
- 双击播放列表中的曲目即可切换。
- **高质量的格式转换**:
- 基于 FFmpeg 内核，提供稳定可靠的格式转换。
- 支持将音频文件转换为 MP3 (不同比特率)、WAV (无损) 和 FLAC (无损)。
- 智能处理高采样率（如 96kHz）和高位深音频的转换。
- **现代化的用户界面**:
- 响应式的界面布局。
- 丰富的右键菜单和顶部菜单栏，提供所有核心功能的快捷访问。
- 详细的状态栏信息提示。

---

## 🛠️ 技术栈

- **图形界面**: [PyQt5](https://riverbankcomputing.com/software/pyqt/intro)
- **音频播放**: [PyAudio](https://people.csail.mit.edu/hubert/pyaudio/)
- **音频解码与转换**: [PyAV](https://pyav.org/docs/stable/) (FFmpeg 的 Python 封装)

---

## 🚀 安装与运行

我们提供两种方式，请根据您的需求选择。

### 方式一：为普通用户 (下载即用)

这是最简单的方式，无需安装 Python 或任何复杂的环境。

1. **下载程序**
- 前往 [**Releases 页面**](https://github.com/CCYellowStar2/AudioHub/releases/latest)。
- 在 "Assets" 部分，下载适用于您操作系统的压缩包 (例如 `AudioHub-Windows.zip`)。

2. **解压文件**
- 将下载的 ZIP 文件解压到一个独立的文件夹中。

3. **运行程序**
- 进入解压后的文件夹，双击 `AudioHub.exe` (或其他可执行文件) 即可启动！

> **注意**: 如果程序无法启动或功能异常 (详见下面的 FFmpeg 提示)，请继续阅读下一节。

### 方式二：为开发者 (从源码运行)

如果您想查看源码、贡献代码或自行构建，请使用此方法。

```bash
# 1. 克隆仓库
git clone https://github.com/CCYellowStar2/AudioHub.git
cd AudioHub

# 2. (推荐) 创建并激活虚拟环境
python -m venv venv
# Windows:
.\venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

# 3. 安装依赖库
pip install -r requirements.txt

# 4. 运行程序
python main.py
```

---

## ⚠️ 故障排除：关于 FFmpeg

本程序依赖 **FFmpeg** 来解码和转换音频。即使是打包好的程序，也需要系统能找到 FFmpeg。

**如果您遇到以下问题：**
* 程序启动时闪退或报错。
* 打开音频文件时程序崩溃。
* 格式转换功能无法使用。

**这几乎总是因为缺少 FFmpeg。** 请根据您的系统选择解决方案：

### Windows 用户

我们提供两种解决方案，**推荐使用简单方法**。

#### 简单方法 (推荐)
1. 从官网下载 FFmpeg：[**gyan.dev**](https://www.gyan.dev/ffmpeg/builds/)。
2. **关键**: 请下载 **shared** 版本 (例如 `ffmpeg-release-full-shared.7z`)。
3. 解压下载的文件，进入 `bin` 目录。
4. 将 `bin` 目录里的**所有 `.dll` 文件**复制到 `AudioHub.exe` 所在的文件夹中，与 `AudioHub.exe` 放在一起。
5. 重新启动 AudioHub 即可。

#### 高级方法 (配置环境变量)
如果您希望系统中其他程序也能使用 FFmpeg，可以将其添加到系统路径中。
1. 完成上述下载和解压步骤。
2. 将 `bin` 目录的完整路径 (例如 `C:\ffmpeg\bin`) 添加到系统的 **`PATH` 环境变量**中。

### macOS / Linux 用户

通常使用包管理器安装即可。

- **macOS (使用 Homebrew)**:
```bash
brew install ffmpeg
```

- **Linux (Debian/Ubuntu)**:
```bash
sudo apt update
sudo apt install ffmpeg
```

---

## 📖 使用指南

1. **打开目录**: 启动程序后，点击 "浏览..." 按钮或使用 `文件 -> 打开目录` 来选择包含音频文件的文件夹。
2. **播放音频**:
- 在左侧文件列表中双击任意文件即可立即播放（预览模式）。
- 选中文件后点击底部的 "播放" 按钮。
3. **管理播放列表**:
- 在左侧文件列表中选中一个或多个文件，右键选择 "添加到播放队列"。
- 双击右侧播放列表中的曲目可跳转播放。
4. **格式转换**:
- 在左侧文件列表中**选中单个**文件。
- 右键点击，在 "格式转换" 子菜单中选择目标格式。
- 转换成功后，文件列表会自动刷新并选中新生成的文件。

---

## 📜 许可证

本项目采用 [MIT License](LICENSE) 授权。

---
