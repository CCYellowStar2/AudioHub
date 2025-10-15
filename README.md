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

## 🚀 快速开始

### 步骤 1: 获取代码

```bash
git clone https://github.com/CCYellowStar2/AudioHub.git
cd AudioHub
```

### 步骤 2: 安装依赖

建议使用虚拟环境来隔离项目依赖。

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows
.\venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 使用 pip 安装所有 Python 库
pip install -r requirements.txt
```

### 步骤 3: 运行程序

```bash
python main.py
```

---

## ⚠️ 重要提示：关于 FFmpeg

本程序的核心功能（音频解码和转换）依赖于 **FFmpeg**。

`pip` 安装 `av` 库时会尝试捆绑 FFmpeg，但在有些系统上可能不会成功。

**如果您在运行程序时遇到与 `av`、`ffmpeg` 或 `DLL not found` 相关的错误，说明您的系统缺少 FFmpeg。** 请按照以下步骤解决：

#### 解决方案：手动安装 FFmpeg

- **Windows**:
1. 从官网下载 FFmpeg：[**gyan.dev**](https://www.gyan.dev/ffmpeg/builds/) 或 [**BtbN**](https://github.com/BtbN/FFmpeg-Builds/releases)。
2. **关键**: 请下载 **shared** 版本（文件名中通常包含 `shared`），而不是 `static` 版本。
3. 解压下载的文件，找到 `bin` 目录。
4. 将这个 `bin` 目录的完整路径添加到系统的 **`PATH` 环境变量**中。

- **macOS (使用 Homebrew)**:
```bash
brew install ffmpeg
```

- **Linux (Debian/Ubuntu)**:
```bash
sudo apt update
sudo apt install ffmpeg
```

完成上述操作后，请重新启动 AudioHub。

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
