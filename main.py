import os
import sys
import time
import queue
import subprocess
from enum import Enum, auto
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
                             QPushButton, QLabel, QLineEdit, QComboBox, QMessageBox,
                             QAction, QMenu, QToolBar, QStatusBar, QSpinBox,
                             QTreeWidget, QTreeWidgetItem, QHeaderView, QSlider, QStyle, QStyleOptionSlider, 
                             QSplitter, QListWidget,QListWidgetItem) # <--- 修改导入
from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QIcon, QFont
import pyaudio
import av          # This is the new core library
    
class ClickableSlider(QSlider):
    """
    一个行为完全可预测的QSlider。
    它通过分离用户意图，并使用Qt的官方精确计算方法，
    彻底解决了所有点击、拖动和边界问题。
    """
    def __init__(self, orientation, parent=None):
        super(ClickableSlider, self).__init__(orientation, parent)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle_rect = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)

            if handle_rect.contains(event.pos()):
                # 用户点击了手柄，意图是拖动。
                # 完全交由父类处理，不做任何干预。
                super().mousePressEvent(event)
            else:
                # 用户点击了轨道，意图是跳转。
                
                # ★★★ 核心修正：使用Qt的精确计算方法 ★★★
                
                # 获取滑轨（Groove）的实际矩形区域，这考虑了所有边距
                groove_rect = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
                
                if self.orientation() == Qt.Horizontal:
                    # 将事件的坐标转换为相对于滑轨的位置
                    slider_pos = event.pos().x() - groove_rect.x()
                    slider_len = groove_rect.width()
                else:
                    slider_pos = event.pos().y() - groove_rect.y()
                    slider_len = groove_rect.height()
                
                # 请求QStyle根据像素位置，精确计算出对应的逻辑值
                value = QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(), slider_pos, slider_len, opt.upsideDown
                )

                # Clamp the value to be within the valid range, just in case
                if value < self.minimum():
                    value = self.minimum()
                if value > self.maximum():
                    value = self.maximum()

                if self.value() != value:
                    self.setValue(value)

                # 依然调用super()，以支持“点击后直接拖动”的无缝体验
                super().mousePressEvent(event)


class AudioPlayerThread(QThread):
    # --- 信号部分保持不变 ---
    position_changed = pyqtSignal(float)
    playback_started = pyqtSignal(str, float)
    playback_finished = pyqtSignal()
    playback_error = pyqtSignal(str)
    seek_completed = pyqtSignal(int)

    CHUNK_SIZE = 4096

    def __init__(self):
        super().__init__()
        self.play_queue = queue.Queue()
        self.command_queue = queue.Queue()
        self._stop = False
        
        self.p_audio = pyaudio.PyAudio()
        self.stream = None
        
        self.current_file = None
        self.total_duration_sec = 0
        
        self._paused = False
        self._interrupt = False
        self.is_song_active = False
        self.playback_start_time = 0
        self.paused_at_sec = 0
        self.pending_seek_while_paused = None
        
    @property
    def is_active(self):
        return self.is_song_active

    def run(self):
        while not self._stop:
            try:
                file_path = self.play_queue.get(timeout=0.1)
                if not file_path or not os.path.isfile(file_path):
                    continue
                
                self._interrupt = False
                self.is_song_active = True
                self.current_file = file_path
                self.pending_seek_while_paused = None # 重置
                
                container = None
                seek_target = None
                
                try:
                    while self.is_song_active and not self._interrupt and not self._stop:
                        container = av.open(file_path)
                        audio_stream = container.streams.audio[0]
                        
                        if seek_target is not None:
                            pts_target = int(seek_target / audio_stream.time_base)
                            container.seek(pts_target, stream=audio_stream, backward=True)
                            seek_target = None
                        else:
                            self.total_duration_sec = float(audio_stream.duration * audio_stream.time_base)
                            sample_rate = audio_stream.rate
                            channels = audio_stream.layout.nb_channels
                            self.playback_started.emit(file_path, self.total_duration_sec)
                            self.stream = self.p_audio.open(
                                format=pyaudio.paInt16, channels=channels, rate=sample_rate, output=True)
                            self.playback_start_time = time.time()
                            self.paused_at_sec = 0
                            self._paused = False

                        resampler = None
                        source_format = audio_stream.codec_context.format.name
                        if source_format != 's16':
                            resampler = av.AudioResampler(format='s16', layout=audio_stream.layout.name, rate=audio_stream.rate)
                        

                        for frame in container.decode(audio_stream):
                            if self._stop or self._interrupt: break
                            
                            # --- 1. 命令处理与暂停等待区 ---
                            # 这是一个统一的循环，它会一直处理命令，直到播放器不处于暂停状态
                            # 并且没有 seek 请求。
                            while self._paused or not self.command_queue.empty():
                                seek_val = self.process_commands()
                                if seek_val is not None:
                                    seek_target = seek_val
                                    break # 收到 seek 指令，跳出等待循环
                                
                                # 如果处理完命令后仍然是暂停状态，就短暂休眠
                                if self._paused:
                                    time.sleep(0.01)
                                else:
                                    # 如果不是暂停状态了（比如收到了unpause），就跳出等待循环
                                    break
                                
                                if self._stop or self._interrupt: break
                            
                            if self._stop or self._interrupt or seek_target is not None: break

                            # --- 2. 音频帧处理区 ---
                            # 能走到这里，说明播放器一定处于“播放”状态
                            frames_to_process = [frame]
                            if resampler:
                                frames_to_process = resampler.resample(frame)

                            for final_frame in frames_to_process:
                                data = final_frame.to_ndarray().tobytes()
                                if self.stream: self.stream.write(data)
                            
                            current_pos = time.time() - self.playback_start_time
                            self.position_changed.emit(current_pos)
                        
                        container.close()
                        container = None
                        
                        if seek_target is None:
                            break

                    if not self._stop and not self._interrupt:
                        self.playback_finished.emit()

                except Exception as e:
                    self.playback_error.emit(f"({os.path.basename(file_path)}): {e}")
                finally:
                    self.is_song_active = False
                    if self.stream:
                        self.stream.stop_stream()
                        self.stream.close()
                        self.stream = None
                    if container:
                        container.close()

            except queue.Empty:
                continue
        
        self.p_audio.terminate()

    # process_commands 方法保持我上次提供的版本，它本身是正确的
    def process_commands(self):
        try:
            cmd, value = self.command_queue.get_nowait()
            if cmd == 'pause':
                if not self._paused:
                    self.paused_at_sec = time.time() - self.playback_start_time
                    self._paused = True
            
            elif cmd == 'unpause':
                if self._paused:
                    if self.pending_seek_while_paused is not None:
                        seek_val = self.pending_seek_while_paused
                        self.pending_seek_while_paused = None
                        self.playback_start_time = time.time() - seek_val
                        self._paused = False
                        return seek_val
                    else:
                        self.playback_start_time = time.time() - self.paused_at_sec
                        self._paused = False

            elif cmd == 'seek':
                self.paused_at_sec = value
                self.seek_completed.emit(int(value))
                
                if self._paused:
                    self.pending_seek_while_paused = value
                else:
                    self.playback_start_time = time.time() - value
                    return value

        except queue.Empty:
            pass
        return None
        
    def _cleanup_stream_resources(self):
        pass # No longer needed, cleanup is in the `finally` block

    # --- 公共控制方法 (无需修改) ---
    def pause(self): self.command_queue.put(('pause', None))
    def unpause(self): self.command_queue.put(('unpause', None))
    def seek(self, position_sec): self.command_queue.put(('seek', position_sec))
    def add_to_queue(self, file_path): self.play_queue.put(file_path)
    
    def interrupt(self):
        self._interrupt = True
        self.is_song_active = False

    def stop(self):
        self._stop = True
        self.interrupt()
        
    def clear_queue(self):
        while not self.play_queue.empty():
            try: self.play_queue.get_nowait()
            except queue.Empty: pass
                
    def remove_file_from_queue(self, file_path):
        temp_queue = queue.Queue()
        while not self.play_queue.empty():
            try:
                item = self.play_queue.get_nowait()
                if item != file_path: temp_queue.put(item)
            except queue.Empty: break
        self.play_queue = temp_queue



class ConverterThread(QThread):
    conversion_finished = pyqtSignal(str, str)
    conversion_progress = pyqtSignal(int)

    def __init__(self, input_path, output_path, target_format='mp3', options=None, parent=None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path
        self.target_format = target_format
        self.options = options if options is not None else {}

    def run(self):
        try:
            input_container = av.open(self.input_path)
            in_stream = input_container.streams.audio[0]
            
            total_duration = in_stream.duration * in_stream.time_base if in_stream.duration else 1
            if total_duration <= 0: total_duration = 1

            output_container = av.open(self.output_path, mode='w')

            if self.target_format == 'mp3':
                # --- MP3 转换路径: 融合了采样率和声道布局的正确处理 ---
                
                MP3_SUPPORTED_RATES = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000}
                
                target_rate = in_stream.rate
                if in_stream.rate not in MP3_SUPPORTED_RATES:
                    target_rate = 44100 
                
                out_stream = output_container.add_stream(
                    self.target_format, 
                    rate=target_rate
                    # layout 参数被永久、正确地移除了！
                )

                if 'b:a' in self.options:
                    out_stream.codec_context.bit_rate = int(self.options['b:a'].replace('k', '')) * 1000

                # 现在，FFmpeg已经为out_stream选择了一个最佳的layout，我们用它来配置重采样器
                resampler = av.AudioResampler(
                    format=out_stream.codec_context.format.name,
                    layout=out_stream.layout.name, # 使用FFmpeg自动选择的布局
                    rate=out_stream.rate,
                )

                last_progress = -1
                for in_frame in input_container.decode(in_stream):
                    for out_frame in resampler.resample(in_frame):
                        for packet in out_stream.encode(out_frame):
                            output_container.mux(packet)
                    
                    current_time = in_frame.pts * in_stream.time_base if in_frame.pts else 0
                    progress = int((current_time / total_duration) * 100)
                    if progress > last_progress: self.conversion_progress.emit(progress); last_progress = progress
                
                for out_frame in resampler.resample(None):
                     for packet in out_stream.encode(out_frame):
                        output_container.mux(packet)
                for packet in out_stream.encode(None):
                    output_container.mux(packet)

            else:
                # --- 无损转换路径 (WAV, FLAC): 同样不建议强制设置 layout，让FFmpeg处理 ---
                out_stream = output_container.add_stream(
                    self.target_format, 
                    rate=in_stream.rate
                    # layout 参数在这里也移除，以获得更好的健壮性
                )
                
                last_progress = -1
                for frame in input_container.decode(in_stream):
                    for packet in out_stream.encode(frame):
                        output_container.mux(packet)

                    current_time = frame.pts * frame.time_base if frame.pts else 0
                    progress = int((current_time / total_duration) * 100)
                    if progress > last_progress: self.conversion_progress.emit(progress); last_progress = progress
                
                for packet in out_stream.encode(None):
                    output_container.mux(packet)

            input_container.close()
            output_container.close()
            
            self.conversion_finished.emit(self.output_path, None)

        except Exception as e:
            error_info = f"编码器: {self.target_format}, 选项: {self.options}\n错误: {e}"
            self.conversion_finished.emit(self.output_path, error_info)



class FileScannerThread(QThread):
    chunk_ready = pyqtSignal(list)
    finished = pyqtSignal(int)

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.is_running = True
        self.CHUNK_SIZE = 100

    def run(self):
        audio_extensions = ('.mp3', '.wav', '.flac', '.ogg', '.m4a', '.wma', '.aac')
        chunk = []
        total_files = 0
        try:
            for entry in os.scandir(self.directory):
                if not self.is_running: break
                if entry.is_file() and entry.name.lower().endswith(audio_extensions):
                    try:
                        file_path = entry.path
                        file_size = entry.stat().st_size
                        chunk.append({'name': entry.name, 'path': file_path, 'size': file_size})
                        if len(chunk) >= self.CHUNK_SIZE:
                            self.chunk_ready.emit(chunk)
                            chunk = []
                    except OSError: continue
            if self.is_running and chunk: self.chunk_ready.emit(chunk)
            total_files = sum(1 for f in os.scandir(self.directory) if f.is_file() and f.name.lower().endswith(audio_extensions))
        except Exception as e:
            print(f"Error scanning directory: {e}")
        finally:
            if self.is_running:
                self.finished.emit(total_files)

    def stop(self):
        self.is_running = False

class LoopMode(Enum):
    NO_LOOP = auto()      # 不循环
    LOOP_LIST = auto()    # 列表循环
    LOOP_ONE = auto()     # 单曲循环
    
class AudioFileManager(QMainWindow):
# ★★★ 用这个完整的方法替换掉你现有的 __init__ 方法 ★★★

    def __init__(self):
        super().__init__()
        self.setWindowTitle("音频文件管理器")
        self.setGeometry(100, 100, 1280, 720) 

        # --- 1. 初始化核心数据和状态 ---
        self.current_dir = ""
        self.audio_files = [] 
        self.playlist = []
        self.current_playlist_index = -1
        self.loop_mode = LoopMode.NO_LOOP
        self._initial_split_set = False
        self.is_paused = False
        self.marked_files = set()
        self.path_to_info_map = {}
        self.path_to_item_map = {}
        self.current_song_duration = 0
        self.is_user_interacting = False
        self.is_seeking = False
        self._is_programmatic_change = False
        self.converter_thread = None
        self.path_to_select_after_scan = None


        # --- 2. 创建所有的UI“零件” (Widgets) ---
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # 顶部浏览区
        self.path_label = QLabel("当前目录:")
        self.path_input = QLineEdit()
        self.path_input.setReadOnly(True)
        self.browse_button = QPushButton("浏览...")

        # 筛选和搜索区
        self.filter_label = QLabel("筛选:")
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["所有文件", "已标记", "未标记"])
        self.search_label = QLabel("搜索:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入文件名...")
        self.height_label = QLabel("列表项高度:")
        self.height_spinbox = QSpinBox()
        self.height_spinbox.setRange(20, 100)
        self.height_spinbox.setValue(50)
        self.height_spinbox.setToolTip("调整列表中每一项的垂直高度。")

        # 中心分割区域
        self.splitter = QSplitter(Qt.Horizontal)
        
        # 左侧文件列表
        self.file_list = QTreeWidget()
        self.file_list.setColumnCount(2)
        self.file_list.setHeaderHidden(True)
        self.file_list.setHeaderLabels(["文件名", "大小"])
        self.file_list.setSelectionMode(QTreeWidget.ExtendedSelection)
        font = QFont()
        font.setPointSize(11)
        self.file_list.setFont(font)
        header = self.file_list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        self.file_list.setColumnWidth(1, 150)
        self.file_list.setStyleSheet("""
            QTreeWidget { background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 5px; }
            QTreeWidget::item { padding-top: 1px; padding-bottom: 1px; border-bottom: 1px solid #e0e0e0; }
            QTreeWidget::item:selected { background-color: #e3f2fd; color: #000; }
        """)

        # 右侧播放列表面板
        self.playlist_panel = QWidget()
        self.playlist_widget = QListWidget()
        # (样式表代码很长，这里省略，保持原样即可)
        self.playlist_widget.setStyleSheet("""
            QListWidget { border: none; background-color: #fafafa; }
            QListWidget::item { background-color: transparent; }
            QListWidget::item:hover, QListWidget::item:selected { background-color: #e3f2fd; }
            QListWidget QLabel[currently_playing="true"] { 
                background-color: #c8e6c9; font-weight: bold; 
                border: 1px solid #a5d6a7; border-radius: 3px;
            }
        """)
        self.prev_button = QPushButton("上一首")
        self.next_button = QPushButton("下一首")
        self.loop_button = QPushButton("循环: 关")
        self.clear_playlist_button = QPushButton("清空播放列表")

        # 底部进度条区域
        self.current_time_label = QLabel("00:00")
        self.progress_slider = ClickableSlider(Qt.Horizontal)
        self.progress_slider.setEnabled(False)
        self.total_time_label = QLabel("00:00")

        # 底部主控制按钮
        self.play_button = QPushButton("播放")
        self.play_button.setEnabled(False)
        self.pause_button = QPushButton("暂停")
        self.pause_button.setEnabled(False)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.mark_button = QPushButton("标记/取消标记")
        self.mark_button.setEnabled(False)
        self.delete_button = QPushButton("删除选中项")
        self.delete_button.setEnabled(False)

        # 状态栏、工具栏和菜单栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.toolbar = QToolBar("主工具栏")
        self.addToolBar(self.toolbar)

        # --- 5. 初始化后台线程 ---
        self.scanner_thread = None
        self.player_thread = AudioPlayerThread()
        self.player_thread.start()

        # --- 3. 创建菜单栏和工具栏 (在所有需要的控件都创建之后) ---
        self.create_menu_bar()
        self.create_toolbar()

        # --- 4. 组装UI布局 ---
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)

        top_layout = QHBoxLayout()
        top_layout.addWidget(self.path_label)
        top_layout.addWidget(self.path_input, 1)
        top_layout.addWidget(self.browse_button)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(self.filter_label)
        filter_layout.addWidget(self.filter_combo)
        filter_layout.addStretch()
        filter_layout.addWidget(self.height_label)
        filter_layout.addWidget(self.height_spinbox)
        filter_layout.addSpacing(15)
        filter_layout.addWidget(self.search_label)
        filter_layout.addWidget(self.search_input)

        playlist_layout = QVBoxLayout(self.playlist_panel)
        playlist_layout.setContentsMargins(0, 0, 0, 0)
        playlist_layout.setSpacing(5)
        playlist_controls_layout = QHBoxLayout()
        playlist_controls_layout.addWidget(self.prev_button)
        playlist_controls_layout.addWidget(self.loop_button)
        playlist_controls_layout.addWidget(self.next_button)
        playlist_layout.addWidget(QLabel("播放列表"))
        playlist_layout.addWidget(self.playlist_widget, 1)
        playlist_layout.addLayout(playlist_controls_layout)
        playlist_layout.addWidget(self.clear_playlist_button)
        
        self.splitter.addWidget(self.file_list)
        self.splitter.addWidget(self.playlist_panel)

        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.current_time_label)
        progress_layout.addWidget(self.progress_slider, 1)
        progress_layout.addWidget(self.total_time_label)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.play_button)
        button_layout.addWidget(self.pause_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addStretch()
        button_layout.addWidget(self.mark_button)
        button_layout.addWidget(self.delete_button)

        main_layout.addLayout(top_layout)
        main_layout.addLayout(filter_layout)
        main_layout.addWidget(self.splitter, 1)
        main_layout.addLayout(progress_layout)
        main_layout.addLayout(button_layout)
        


        # --- 6. 连接所有信号和槽 ---
        self.browse_button.clicked.connect(self.browse_directory)
        self.filter_combo.currentIndexChanged.connect(self.filter_files)
        self.search_input.textChanged.connect(self.filter_files)
        self.height_spinbox.valueChanged.connect(self.adjust_item_height)
        self.file_list.itemSelectionChanged.connect(self.update_button_states)
        self.file_list.itemDoubleClicked.connect(self.play_audio)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self.show_context_menu)
        self.file_list.itemChanged.connect(self.on_item_changed)
        self.playlist_widget.itemDoubleClicked.connect(self.play_from_playlist)
        self.prev_button.clicked.connect(self.play_previous)
        self.playlist_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlist_widget.customContextMenuRequested.connect(self.show_playlist_context_menu)
        self.next_button.clicked.connect(self.play_next)
        self.loop_button.clicked.connect(self.toggle_loop_mode)
        self.clear_playlist_button.clicked.connect(self.clear_playlist)
        self.progress_slider.sliderPressed.connect(self.slider_pressed)
        self.progress_slider.sliderMoved.connect(self.slider_is_moving)
        self.progress_slider.sliderReleased.connect(self.slider_released)
        self.play_button.clicked.connect(self.play_audio)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button.clicked.connect(self.stop_audio)
        self.mark_button.clicked.connect(self.toggle_mark)
        self.delete_button.clicked.connect(self.delete_file)
        self.player_thread.playback_started.connect(self.on_playback_started)
        self.player_thread.playback_finished.connect(self.on_playback_finished)
        self.player_thread.playback_error.connect(self.on_playback_error)
        self.player_thread.position_changed.connect(self.on_position_changed)
        self.player_thread.seek_completed.connect(self.on_seek_completed)

        self._update_menu_actions_state()
        

    def showEvent(self, event):
        """
        当窗口第一次显示时，这个方法会被自动调用。
        这是设置初始尺寸和比例的最佳时机。
        """
        # 调用父类的原始方法，这是必须的
        super().showEvent(event)
        
        # 使用标志位确保这个设置只在第一次显示时执行一次
        if not self._initial_split_set:
            self.splitter.setSizes([int(self.width() * 0.7), int(self.width() * 0.3)])
            self._initial_split_set = True
            

    def reveal_in_explorer(self):
        """
        在操作系统的文件管理器中显示选中的文件。
        """
        selected_items = self.file_list.selectedItems()
        if not selected_items:
            return

        file_path = selected_items[0].data(0, Qt.UserRole)
        
        self._reveal_file_in_explorer(file_path) 
            
    def _reveal_file_in_explorer(self, file_path):
        """
        核心逻辑：在操作系统的文件管理器中显示指定文件。
        """
        if not os.path.exists(file_path):
            QMessageBox.warning(self, "文件未找到", f"文件路径不存在:\n{file_path}")
            return

        if sys.platform == "win32":
            file_path = os.path.normpath(file_path)
            subprocess.Popen(['explorer', '/select,', file_path])
        elif sys.platform == "darwin":
            subprocess.Popen(['open', '-R', file_path])
        else:
            directory = os.path.dirname(file_path)
            subprocess.Popen(['xdg-open', directory])

    def show_playlist_context_menu(self, position):
        """
        显示播放列表的右键菜单。
        """
        # 1. 获取用户点击位置对应的 item
        item = self.playlist_widget.itemAt(position)
        if not item:
            # 如果点击的是空白区域，则不显示菜单
            return

        # 2. 获取该项在播放列表中的索引
        index = self.playlist_widget.row(item)
        if index == -1 or index >= len(self.playlist):
            return

        # 3. 提取文件路径
        file_path = self.playlist[index]

        # 4. 构建菜单
        menu = QMenu()

        # 5. 添加动作
        reveal_action = menu.addAction("在文件管理器中显示")
        menu.addSeparator()
        play_action = menu.addAction("立即播放此项")
        remove_action = menu.addAction("从列表中移除")
        
        # 6. 执行菜单
        action = menu.exec_(self.playlist_widget.mapToGlobal(position))

        # 7. 处理动作
        if action == reveal_action:
            self._reveal_file_in_explorer(file_path)
        elif action == play_action:
            # 播放列表中的项，调用 play_song_at_index 来进入“播放列表模式”
            self.play_song_at_index(index) 
        elif action == remove_action:
            self.remove_from_playlist(index)
            
    def remove_from_playlist(self, index_to_remove):
        """
        从播放列表和UI中移除指定索引的歌曲。
        """
        if not (0 <= index_to_remove < len(self.playlist)):
            return

        # 1. 从数据模型中移除
        removed_path = self.playlist.pop(index_to_remove)
        
        # 2. 从UI中移除对应的项
        item = self.playlist_widget.takeItem(index_to_remove)
        del item # 确保QListWidgetItem被销毁

        # 3. 调整当前播放索引
        if self.current_playlist_index == index_to_remove:
            # 如果移除的是当前正在播放的歌曲
            self.stop_audio()
        elif self.current_playlist_index > index_to_remove:
            # 如果移除的歌曲在当前播放歌曲的前面，当前索引需要向前移动一位
            self.current_playlist_index -= 1
            # 重新高亮以防万一
            self.highlight_current_song()
        elif self.current_playlist_index != -1:
            # 如果当前正在播放，但移除的歌曲在后面，只需重新高亮
            self.highlight_current_song()
            
        self.status_bar.showMessage(f"已从播放列表移除文件: {os.path.basename(removed_path)}")
    
    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "选择音频文件目录")
        if directory:
            self.current_dir = directory
            self.path_input.setText(directory)
            self.start_loading_files()

    def start_loading_files(self):
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.stop()
            self.scanner_thread.wait()
        self.file_list.clear()
        self.audio_files = []
        self.path_to_info_map.clear()
        self.path_to_item_map.clear()
        
        if not self.current_dir: return
        self.set_controls_enabled(False)
        self.status_bar.showMessage("正在扫描目录，请稍候...")
        self.scanner_thread = FileScannerThread(self.current_dir)
        self.scanner_thread.chunk_ready.connect(self.add_file_chunk)
        self.scanner_thread.finished.connect(self.on_scan_finished)
        self.scanner_thread.start()

    def format_time(self, seconds):
        """将秒数格式化为 MM:SS 字符串"""
        if seconds is None or seconds < 0:
            return "00:00"
        mins, secs = divmod(int(seconds), 60)
        return f"{mins:02d}:{secs:02d}"

    def toggle_pause(self):
        # 使用我们新添加的 is_active 属性来做判断
        if not self.player_thread.is_active:
            return

        if self.is_paused:
            self.player_thread.unpause()
            self.pause_button.setText("暂停")
            if self.player_thread.current_file:
                self.status_bar.showMessage(f"正在播放: {os.path.basename(self.player_thread.current_file)}")
            self.is_paused = False
        else:
            self.player_thread.pause()
            self.pause_button.setText("继续")
            self.status_bar.showMessage("已暂停")
            self.is_paused = True

    def on_position_changed(self, position_sec):
        """
        这个槽函数现在由一个“门卫”标志保护。
        """
        # ★★★ 核心：如果用户正在操作滑块，则忽略所有来自后台的更新 ★★★
        if self.is_user_interacting:
            return

        # 如果用户没有在操作，则像以前一样忠实地更新UI
        if position_sec <= self.current_song_duration:
            self.progress_slider.blockSignals(True)
            self.progress_slider.setValue(int(position_sec))
            self.progress_slider.blockSignals(False)
            self.current_time_label.setText(self.format_time(position_sec))

    def on_seek_completed(self, seek_time):
        """
        这是后台发来的“投降”信号，表示它已成功跳转到指定位置。
        只有在此刻，我们才安全地将UI的控制权交还给后台。
        """
        # 作为最后的保险，确保UI在视觉上与后台确认的时间完全同步
        self.progress_slider.setValue(seek_time)
        self.current_time_label.setText(self.format_time(seek_time))
        
        
    def slider_pressed(self):
        """当用户的手指接触到滑块时，升起“门卫”旗帜。"""
        self.is_user_interacting = True

    def slider_is_moving(self, value):
        """当用户拖动时，只更新时间标签，不执行任何seek操作"""
        self.current_time_label.setText(self.format_time(value))

    def slider_released(self):
        """当用户的手指离开滑块时，命令后台，并放下“门卫”旗帜。"""
        seek_time_sec = self.progress_slider.value()
        self.current_time_label.setText(self.format_time(seek_time_sec))
        self.player_thread.seek(seek_time_sec)
        
        # ★★★ 核心：在发出seek命令后，立刻允许后台信号恢复UI更新 ★★★
        self.is_user_interacting = False

    def reset_progress_ui(self):
        """重置进度条和时间标签"""
        self.progress_slider.setValue(0)
        self.progress_slider.setEnabled(False)
        self.current_time_label.setText("00:00")
        self.total_time_label.setText("00:00")
        self.current_song_duration = 0


    def add_file_chunk(self, chunk):
        for file_info_from_thread in chunk:
            file_path = file_info_from_thread['path']
            marked = file_path in self.marked_files
            file_info = {
                'name': file_info_from_thread['name'],
                'path': file_path,
                'size': file_info_from_thread['size'],
                'marked': marked
            }
            self.audio_files.append(file_info)
            
            item = self.create_and_add_list_item(file_info)
            self.path_to_info_map[file_path] = file_info
            self.path_to_item_map[file_path] = item
            
        QApplication.processEvents()

    def on_scan_finished(self, total_count):
        self.status_bar.showMessage(f"加载完成，共找到 {len(self.audio_files)} 个音频文件")
        self.set_controls_enabled(True)
        self.filter_files() # 确保所有文件都根据当前筛选器正确显示

        # ★★★ 核心改动：检查是否有待选中的文件 ★★★
        if self.path_to_select_after_scan:
            item_to_select = self.path_to_item_map.get(self.path_to_select_after_scan)
            if item_to_select:
                self.file_list.clearSelection() # 清除之前的选择
                item_to_select.setSelected(True)
                self.file_list.scrollToItem(item_to_select) # 滚动到该项，确保可见
            
            # ★★★ 重置标记，防止下次刷新时再次选中 ★★★
            self.path_to_select_after_scan = None

    def set_controls_enabled(self, enabled):
        self.browse_button.setEnabled(enabled)
        self.filter_combo.setEnabled(enabled)
        self.search_input.setEnabled(enabled)

    # --- 已修改为创建 QTreeWidgetItem ---
    def create_and_add_list_item(self, file_info):
        item = QTreeWidgetItem()
        
        initial_height = self.height_spinbox.value() 
        item.setSizeHint(0, QSize(0, initial_height)) # 为第0列设置高度
        
        item.setData(0, Qt.UserRole, file_info['path']) # 将路径数据存储在第0列
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked if file_info['marked'] else Qt.Unchecked)
        
        self.update_item_text(item, file_info)
        self.file_list.addTopLevelItem(item) # 使用 addTopLevelItem 添加项
        return item

    # --- 已重写为 QTreeWidget 更新文本 ---
    def update_item_text(self, item, file_info):
        # 第0列: 标记 + 文件名
        star = "★ " if file_info['marked'] else ""
        name_text = f"{star}{file_info['name']}"
        item.setText(0, name_text)

        # 第1列: 文件大小
        size_str = self.format_file_size(file_info.get('size', 0))
        item.setText(1, size_str)
        
        # 将文件大小列右对齐
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)

    # --- 已修改为适配 QTreeWidget ---
    def adjust_item_height(self):
        new_height = self.height_spinbox.value()
        new_size_hint = QSize(0, new_height)
        # 遍历 QTreeWidget 的顶层项目
        for i in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(i)
            if item:
                item.setSizeHint(0, new_size_hint) # 为第0列设置高度

    # --- 已修改以处理 QTreeWidget 的 itemChanged 信号 ---
    def on_item_changed(self, item, column):
        # 确保我们只响应复选框列(第0列)的变化
        if self._is_programmatic_change or column != 0: 
            return
            
        file_path = item.data(0, Qt.UserRole) # 从第0列获取数据
        is_checked = (item.checkState(0) == Qt.Checked) # 从第0列获取状态
        
        if is_checked: self.marked_files.add(file_path)
        else: self.marked_files.discard(file_path)
        
        file_info = self.path_to_info_map.get(file_path)
        if file_info:
            file_info['marked'] = is_checked
            self.update_item_text(item, file_info)
            filter_type = self.filter_combo.currentText()
            if (filter_type == "已标记" and not is_checked) or \
               (filter_type == "未标记" and is_checked):
                item.setHidden(True)
        self.update_button_states()

    def filter_files(self):
        if self.scanner_thread and self.scanner_thread.isRunning(): return
        filter_type = self.filter_combo.currentText()
        search_text = self.search_input.text().lower()

        for file_path, file_info in self.path_to_info_map.items():
            item = self.path_to_item_map.get(file_path)
            if not item: continue

            show = True
            if filter_type == "已标记" and not file_info['marked']: show = False
            if filter_type == "未标记" and file_info['marked']: show = False
            if search_text and search_text not in file_info['name'].lower(): show = False
            item.setHidden(not show)
            
    def _create_conversion_submenu(self, parent_menu):
        """
        一个辅助函数，用于创建格式转换的子菜单。
        这避免了在右键菜单和顶部菜单中重复定义，确保两者完全一致。
        """
        convert_menu = parent_menu.addMenu("格式转换")

        # ★★★ 最终确认版：移除OGG，只保留可靠的转换选项 ★★★
        supported_formats = [
            ("MP3 (192 kbps CBR)", 'mp3', 'mp3', {'b:a': '192k'}),
            ("MP3 (320 kbps CBR)", 'mp3', 'mp3', {'b:a': '320k'}),
            ("OGG Vorbis", 'libvorbis', 'ogg', {}),
            ("WAV (无损 PCM 16-bit)", 'pcm_s16le', 'wav', {}),
            ("FLAC (无损压缩)", 'flac', 'flac', {}),
        ]

        for display_name, codec, extension, options in supported_formats:
            action = convert_menu.addAction(display_name)
            action.triggered.connect(
                lambda checked=False, c=codec, e=extension, o=options: self.start_conversion(c, e, o)
            )
        
        return convert_menu

    def create_menu_bar(self):
        menu_bar = self.menuBar()
        # --- 1. 文件菜单 (File) ---
        file_menu = menu_bar.addMenu("文件(&F)")
        open_action = QAction("打开目录...", self)
        open_action.triggered.connect(self.browse_directory)
        file_menu.addAction(open_action)
        self.reveal_action = QAction("在文件管理器中显示", self)
        self.reveal_action.triggered.connect(self.reveal_in_explorer)
        file_menu.addAction(self.reveal_action)
        file_menu.addSeparator()
        self.delete_selected_action = QAction("删除选中文件", self)
        self.delete_selected_action.triggered.connect(self.delete_file)
        file_menu.addAction(self.delete_selected_action)
        self.delete_marked_action = QAction("删除所有已标记文件", self)
        self.delete_marked_action.triggered.connect(self.delete_marked_files)
        file_menu.addAction(self.delete_marked_action)
        file_menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # --- 2. 编辑菜单 (Edit) ---
        edit_menu = menu_bar.addMenu("编辑(&E)")
        select_all_action = QAction("全选", self)
        select_all_action.triggered.connect(self.file_list.selectAll)
        edit_menu.addAction(select_all_action)
        deselect_all_action = QAction("取消全选", self)
        deselect_all_action.triggered.connect(self.file_list.clearSelection)
        edit_menu.addAction(deselect_all_action)
        invert_selection_action = QAction("反转选择", self)
        invert_selection_action.triggered.connect(self.invert_selection)
        edit_menu.addAction(invert_selection_action)
        edit_menu.addSeparator()
        self.select_marked_action = QAction("选中所有已标记", self)
        self.select_marked_action.triggered.connect(self.select_marked)
        edit_menu.addAction(self.select_marked_action)
        select_unmarked_action = QAction("选中所有未标记", self)
        select_unmarked_action.triggered.connect(self.select_unmarked)
        edit_menu.addAction(select_unmarked_action)
        edit_menu.addSeparator()
        self.toggle_mark_action = QAction("标记/取消标记选中项", self)
        self.toggle_mark_action.triggered.connect(self.toggle_mark)
        edit_menu.addAction(self.toggle_mark_action)
        self.clear_marks_action = QAction("清除所有标记", self)
        self.clear_marks_action.triggered.connect(self.clear_all_marks)
        edit_menu.addAction(self.clear_marks_action)

        # --- 3. 播放菜单 (Playback) ---
        playback_menu = menu_bar.addMenu("播放(&P)")
        self.play_pause_action = QAction("播放", self)
        self.play_pause_action.triggered.connect(self.toggle_play_pause)
        playback_menu.addAction(self.play_pause_action)
        self.stop_action = QAction("停止", self)
        self.stop_action.triggered.connect(self.stop_audio)
        playback_menu.addAction(self.stop_action)
        self.add_to_queue_action = QAction("添加到播放队列", self)
        self.add_to_queue_action.triggered.connect(self.add_to_queue)
        playback_menu.addAction(self.add_to_queue_action)
        self.clear_queue_action = QAction("清空播放队列", self)
        self.clear_queue_action.triggered.connect(self.clear_playlist)
        playback_menu.addAction(self.clear_queue_action)
        playback_menu.addSeparator()
        self.next_action = QAction("下一首", self)
        self.next_action.triggered.connect(self.play_next)
        playback_menu.addAction(self.next_action)
        self.prev_action = QAction("上一首", self)
        self.prev_action.triggered.connect(self.play_previous)
        playback_menu.addAction(self.prev_action)
        playback_menu.addSeparator()
        loop_menu = playback_menu.addMenu("循环模式")
        from PyQt5.QtWidgets import QActionGroup
        loop_group = QActionGroup(self)
        loop_group.setExclusive(True)
        self.loop_none_action = QAction("关闭循环", self, checkable=True)
        self.loop_list_action = QAction("列表循环", self, checkable=True)
        self.loop_one_action = QAction("单曲循环", self, checkable=True)
        loop_menu.addAction(self.loop_none_action)
        loop_menu.addAction(self.loop_list_action)
        loop_menu.addAction(self.loop_one_action)
        loop_group.addAction(self.loop_none_action)
        loop_group.addAction(self.loop_list_action)
        loop_group.addAction(self.loop_one_action)
        self.loop_none_action.triggered.connect(lambda: self.set_loop_mode_from_menu(LoopMode.NO_LOOP))
        self.loop_list_action.triggered.connect(lambda: self.set_loop_mode_from_menu(LoopMode.LOOP_LIST))
        self.loop_one_action.triggered.connect(lambda: self.set_loop_mode_from_menu(LoopMode.LOOP_ONE))
        self.update_loop_menu_state() # 初始化菜单状态

        tools_menu = menu_bar.addMenu("工具(&T)")
        self.convert_menu = self._create_conversion_submenu(tools_menu)

        # --- 4. 视图菜单 (View) ---
        view_menu = menu_bar.addMenu("视图(&V)")
        view_menu.addAction(self.toolbar.toggleViewAction())
        playlist_action = QAction("显示播放列表", self, checkable=True)
        playlist_action.setChecked(True)
        playlist_action.triggered.connect(self.playlist_panel.setVisible)
        view_menu.addAction(playlist_action)
        status_bar_action = QAction("显示状态栏", self, checkable=True)
        status_bar_action.setChecked(True)
        status_bar_action.triggered.connect(self.status_bar.setVisible) 
        view_menu.addAction(status_bar_action)

        # --- 5. 帮助菜单 (Help) ---
        help_menu = menu_bar.addMenu("帮助(&H)")
        about_action = QAction("关于...", self)
        about_action.triggered.connect(self.about_dialog)
        help_menu.addAction(about_action)

    def toggle_play_pause(self):
        """
        一个统一的播放/暂停切换方法，供菜单栏和未来的统一按钮使用。
        """
        # ★★★ 把这里的 is_playing 修改为 is_active ★★★
        if self.player_thread.is_active:
            self.toggle_pause() # 如果正在播放（包括暂停），则调用现有的暂停切换逻辑
        else:
            self.play_audio() # 如果已停止，则调用播放逻辑

    def set_loop_mode_from_menu(self, mode):
        """由菜单栏调用，用于设置循环模式并更新UI。"""
        self.loop_mode = mode
        self.update_loop_button_ui() # 更新按钮文本
        # 注意：菜单的状态由 QActionGroup 自动管理，我们无需手动更新

    def update_loop_menu_state(self):
        """根据当前的循环模式，更新菜单栏中的选中项。"""
        if self.loop_mode == LoopMode.NO_LOOP:
            self.loop_none_action.setChecked(True)
        elif self.loop_mode == LoopMode.LOOP_LIST:
            self.loop_list_action.setChecked(True)
        elif self.loop_mode == LoopMode.LOOP_ONE:
            self.loop_one_action.setChecked(True)

    def about_dialog(self):
        """显示“关于”对话框。"""
        QMessageBox.about(self, "关于 音频文件管理器",
            "<b>音频文件管理器 v2.0</b><br>"
            "一个使用 PyQt5 和 PyAudio 构建的简单音频播放和管理工具。<br><br>"
            "祝您使用愉快！")
    
    def create_toolbar(self):        
        # 2. 再用QSS微调按钮的内边距和样式
        self.toolbar.setStyleSheet("""
            QToolButton {
                padding: 6px;
                margin: 2px;
                border-radius: 4px;
            }
            QToolButton:hover {
                background-color: #e0e0e0;
            }
        """)
        
        open_action = QAction(QIcon.fromTheme("document-open"), "打开目录", self)
        open_action.triggered.connect(self.browse_directory)
        self.toolbar.addAction(open_action)

    def format_file_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0: return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} GB"

    # --- 已修改为从第0列获取数据 ---
    def get_selected_file_info(self):
        selected_items = self.file_list.selectedItems()
        if selected_items:
            file_path = selected_items[0].data(0, Qt.UserRole)
            return self.path_to_info_map.get(file_path)
        return None

    def update_button_states(self):
        selected_items = self.file_list.selectedItems()
        has_selection = len(selected_items) > 0
        
        self.play_button.setEnabled(has_selection)
        self.mark_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        
        if has_selection:
            file_info = self.get_selected_file_info()
            if file_info: self.mark_button.setText("取消标记" if file_info['marked'] else "标记")
        
        # ★★★ 新增：调用方法来同步更新菜单栏的状态 ★★★
        self._update_menu_actions_state()

    def _update_menu_actions_state(self):
        """
        根据当前程序状态，集中更新所有菜单栏动作(QAction)的启用/禁用状态。
        """
        selected_count = len(self.file_list.selectedItems())
        has_selection = selected_count > 0
        is_single_selection = selected_count == 1
        has_marked = bool(self.marked_files)
        has_playlist = bool(self.playlist)
        is_playing_or_paused = self.player_thread.is_active

        # 文件菜单
        self.reveal_action.setEnabled(is_single_selection)
        self.delete_selected_action.setEnabled(has_selection)
        self.delete_marked_action.setEnabled(has_marked)

        # 编辑菜单
        self.toggle_mark_action.setEnabled(has_selection)
        self.select_marked_action.setEnabled(has_marked)
        self.clear_marks_action.setEnabled(has_marked)

        # 播放菜单
        self.play_pause_action.setEnabled(has_selection or is_playing_or_paused)
        if is_playing_or_paused:
            self.play_pause_action.setText("暂停" if not self.is_paused else "继续")
        else:
            self.play_pause_action.setText("播放")
        self.stop_action.setEnabled(is_playing_or_paused)
        self.add_to_queue_action.setEnabled(has_selection)
        self.clear_queue_action.setEnabled(has_playlist)
        self.next_action.setEnabled(has_playlist)
        self.prev_action.setEnabled(has_playlist)

        # 工具菜单
        self.convert_menu.menuAction().setEnabled(is_single_selection)
        
    # --- 已修改为适配 QTreeWidget ---
    def toggle_mark(self):
        selected_items = self.file_list.selectedItems()
        if not selected_items: return
        marked_count = sum(1 for item in selected_items if item.data(0, Qt.UserRole) in self.marked_files)
        new_state_is_marked = marked_count <= len(selected_items) / 2
        filter_type = self.filter_combo.currentText()
        self._is_programmatic_change = True
        for item in selected_items:
            file_path = item.data(0, Qt.UserRole) # 从第0列获取数据
            if new_state_is_marked: self.marked_files.add(file_path)
            else: self.marked_files.discard(file_path)
            
            file_info = self.path_to_info_map.get(file_path)
            if file_info:
                file_info['marked'] = new_state_is_marked
                item.setCheckState(0, Qt.Checked if new_state_is_marked else Qt.Unchecked) # 在第0列设置状态
                self.update_item_text(item, file_info)
            if (filter_type == "已标记" and not new_state_is_marked) or \
               (filter_type == "未标记" and new_state_is_marked):
                item.setHidden(True)
        self._is_programmatic_change = False
        self.update_button_states()

    # --- 已修改为适配 QTreeWidget ---
    def clear_all_marks(self):
        reply = QMessageBox.question(self, '确认清除标记', "确定要清除所有文件的标记吗?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.marked_files.clear()
            
            self._is_programmatic_change = True
            for file_path, file_info in self.path_to_info_map.items():
                file_info['marked'] = False
                item = self.path_to_item_map.get(file_path)
                if item:
                    item.setCheckState(0, Qt.Unchecked) # 在第0列设置状态
                    self.update_item_text(item, file_info)
                    if self.filter_combo.currentText() == "已标记":
                        item.setHidden(True)
            self._is_programmatic_change = False
            
            self.status_bar.showMessage("已清除所有标记")
            self.update_button_states()

    # --- 已修改为适配 QTreeWidget ---
    def delete_file(self):
        selected_items = self.file_list.selectedItems()
        if not selected_items: return
        confirm_text = f"确定要删除选中的 {len(selected_items)} 个文件吗?"
        if len(selected_items) == 1: confirm_text = f"确定要删除文件 '{os.path.basename(selected_items[0].data(0, Qt.UserRole))}' 吗?"
        reply = QMessageBox.question(self, '确认删除', confirm_text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            failed_deletions = []
            items_to_remove = list(selected_items)
            for item in items_to_remove:
                file_path = item.data(0, Qt.UserRole)
                try:
                    if self.player_thread.current_file == file_path: self.player_thread.interrupt()
                    self.player_thread.remove_file_from_queue(file_path)
                    os.remove(file_path)
                    self.marked_files.discard(file_path)
                    
                    if file_path in self.path_to_item_map: del self.path_to_item_map[file_path]
                    if file_path in self.path_to_info_map: del self.path_to_info_map[file_path]
                    
                    self.audio_files = [f for f in self.audio_files if f['path'] != file_path]
                    
                    # 使用新的删除方法
                    index = self.file_list.indexOfTopLevelItem(item)
                    if index != -1:
                        self.file_list.takeTopLevelItem(index)

                except Exception as e: failed_deletions.append(os.path.basename(file_path))
            if failed_deletions: QMessageBox.warning(self, "删除错误", f"以下文件删除失败:\n" + "\n".join(failed_deletions))
            else: self.status_bar.showMessage(f"已成功删除 {len(items_to_remove)} 个文件")

    # --- 已修改为适配 QTreeWidget ---
    def delete_marked_files(self):
        if not self.marked_files:
            QMessageBox.information(self, "提示", "没有已标记的文件可供删除。")
            return
        reply = QMessageBox.question(self, '确认删除', f"确定要删除所有 {len(self.marked_files)} 个已标记的文件吗？\n此操作无法撤销。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            failed_deletions = []
            marked_paths_to_delete = list(self.marked_files)
            for file_path in marked_paths_to_delete:
                try:
                    if self.player_thread.current_file == file_path: self.player_thread.interrupt()
                    self.player_thread.remove_file_from_queue(file_path)
                    os.remove(file_path)
                    self.marked_files.discard(file_path)
                    item_to_remove = self.path_to_item_map.get(file_path)
                    if item_to_remove:
                        index = self.file_list.indexOfTopLevelItem(item_to_remove)
                        if index != -1:
                            self.file_list.takeTopLevelItem(index)
                    
                    if file_path in self.path_to_item_map: del self.path_to_item_map[file_path]
                    if file_path in self.path_to_info_map: del self.path_to_info_map[file_path]
                    
                    self.audio_files = [f for f in self.audio_files if f['path'] != file_path]
                except Exception as e: failed_deletions.append(os.path.basename(file_path))
            if failed_deletions: QMessageBox.warning(self, "删除完成", f"部分文件删除失败:\n" + "\n".join(failed_deletions))
            else: self.status_bar.showMessage(f"已成功删除 {len(marked_paths_to_delete)} 个已标记文件")

    def play_next(self):
        """播放下一首歌曲，会考虑列表循环模式。"""
        if not self.playlist:
            return

        # 计算下一个索引
        next_index = self.current_playlist_index + 1
        
        # 如果超出范围
        if next_index >= len(self.playlist):
            # 如果是列表循环，则回到开头
            if self.loop_mode == LoopMode.LOOP_LIST:
                next_index = 0
            else:
                # 否则不执行任何操作
                return
                
        self.play_song_at_index(next_index)

    def play_previous(self):
        """播放上一首歌曲，会考虑列表循环模式。"""
        if not self.playlist:
            return
            
        # 计算上一个索引
        prev_index = self.current_playlist_index - 1
        
        # 如果超出范围
        if prev_index < 0:
            # 如果是列表循环，则去到结尾
            if self.loop_mode == LoopMode.LOOP_LIST:
                prev_index = len(self.playlist) - 1
            else:
                # 否则不执行任何操作
                return

        self.play_song_at_index(prev_index)

    def toggle_loop_mode(self):
        """切换循环模式：无 -> 列表 -> 单曲 -> 无"""
        if self.loop_mode == LoopMode.NO_LOOP:
            self.loop_mode = LoopMode.LOOP_LIST
        elif self.loop_mode == LoopMode.LOOP_LIST:
            self.loop_mode = LoopMode.LOOP_ONE
        elif self.loop_mode == LoopMode.LOOP_ONE:
            self.loop_mode = LoopMode.NO_LOOP
        
        self.update_loop_button_ui()
        self.update_loop_menu_state() # ★★★ 新增：同步更新菜单栏的选中状态 ★★★

    def update_loop_button_ui(self):
        """根据当前循环模式更新按钮的文本"""
        if self.loop_mode == LoopMode.NO_LOOP:
            self.loop_button.setText("循环: 关")
        elif self.loop_mode == LoopMode.LOOP_LIST:
            self.loop_button.setText("列表循环")
        elif self.loop_mode == LoopMode.LOOP_ONE:
            self.loop_button.setText("单曲循环")
    
    def play_audio(self, item=None, column=None): # 接受可选参数以保持信号连接兼容性
        """
        【新逻辑】立即播放选中的文件（预览模式），不影响播放列表。
        """
        file_info = self.get_selected_file_info()
        if file_info:
            file_path = file_info['path']
            
            # 1. 关键：将自己标记为“非播放列表模式”
            self.current_playlist_index = -1
            
            # 2. 清除播放列表中的所有高亮，因为我们现在是预览模式
            self.highlight_current_song()
            
            # 3. 直接将这“一首”歌交给后台去播放
            self.player_thread.interrupt()
            self.player_thread.clear_queue()
            self.player_thread.add_to_queue(file_path)

    def add_to_queue(self):
        selected_items = self.file_list.selectedItems()
        if not selected_items:
            return

        original_count = len(self.playlist)

        for selected_item in selected_items:
            file_path = selected_item.data(0, Qt.UserRole)
            file_name = os.path.basename(file_path)
            
            self.playlist.append(file_path)
            
            # --- ★★★ 核心改动：创建并添加自定义Widget ★★★ ---
            item = QListWidgetItem(self.playlist_widget) # 创建一个空的item
            
            # 创建一个自定义的widget（这里用QLabel就足够了）
            label = QLabel(file_name)
            label.setContentsMargins(5, 2, 5, 2) # 添加一点内边距，让观感更好
            
            # 将item和widget关联起来
            self.playlist_widget.addItem(item)
            self.playlist_widget.setItemWidget(item, label)
            # --- 改动结束 ---

        self.status_bar.showMessage(f"已将 {len(selected_items)} 个文件添加到播放列表")

        if original_count == 0 and self.current_playlist_index == -1:
            self.play_song_at_index(0)

    def play_next_in_playlist(self):
        # 停止当前可能正在播放的任何内容
        if self.player_thread.is_playing:
            self.player_thread.interrupt()

        # 检查播放列表是否为空
        if not self.playlist:
            self.status_bar.showMessage("播放列表为空")
            # 确保UI处于停止状态
            self.on_playback_finished() 
            return

        # 1. 从数据源中取出下一首歌
        next_song_path = self.playlist.pop(0)
        
        # 2. 从UI中移除对应的项
        # (注意：这里简单地移除了第一项，如果允许拖拽排序，逻辑会更复杂)
        self.playlist_widget.takeItem(0)

        # 3. 将这“一首”歌交给后台去播放
        self.player_thread.clear_queue() # 确保后台队列是干净的
        self.player_thread.add_to_queue(next_song_path)
        
    def play_from_playlist(self, item):
        """当用户双击播放列表中的项时调用。"""
        index = self.playlist_widget.row(item)
        self.play_song_at_index(index)

    def play_song_at_index(self, index):
        """
        播放列表中指定索引的核心函数。
        所有播放操作最终都应调用此函数。
        """
        # 检查索引是否有效
        if not (0 <= index < len(self.playlist)):
            self.stop_audio() # 如果索引无效，则停止一切
            return

        self.current_playlist_index = index
        song_path = self.playlist[self.current_playlist_index]

        # 更新UI高亮
        self.highlight_current_song()

        # 将这“一首”歌交给后台去播放
        self.player_thread.interrupt()
        self.player_thread.clear_queue()
        self.player_thread.add_to_queue(song_path)
        
    def highlight_current_song(self):
        """
        根据 self.current_playlist_index 更新播放列表的UI高亮。
        """
        for i in range(self.playlist_widget.count()):
            item = self.playlist_widget.item(i)
            widget = self.playlist_widget.itemWidget(item)
            
            if widget:
                is_playing = (i == self.current_playlist_index)
                
                # 1. 设置动态属性
                widget.setProperty("currently_playing", is_playing)
                
                # 2. 刷新这个 widget 的样式，使其应用新属性
                self.playlist_widget.style().unpolish(widget)
                self.playlist_widget.style().polish(widget)


        self.playlist_widget.update()
        
    def clear_playlist(self):
        """清空播放列表，并停止当前播放。"""
        self.stop_audio() # 清空列表前先停止播放
        self.playlist.clear()
        self.playlist_widget.clear()
        self.status_bar.showMessage("播放列表已清空")
        
    def stop_audio(self):
        """仅停止当前播放，并重置播放指针，但保留播放列表。"""
        if self.player_thread.isRunning():
            self.player_thread.interrupt()
            self.player_thread.clear_queue()
        
        self.current_playlist_index = -1
        self.highlight_current_song() # 清除高亮
        
        self.play_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("暂停")
        self.is_paused = False
        self.status_bar.showMessage("播放已停止")
        self.reset_progress_ui()
    
    def on_playback_started(self, file_path, duration):
        # 恢复正常的窗口标题
        self.setWindowTitle("音频文件管理器")
        
        self.status_bar.showMessage(f"正在播放: {os.path.basename(file_path)}")
        self.play_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pause_button.setEnabled(True) # <--- 启用暂停按钮
        self.pause_button.setText("暂停")   # <--- 确保文字是“暂停”
        self.is_paused = False             # <--- 重置暂停状态
        self.current_song_duration = duration # 直接使用从线程传来的时长

        if self.current_song_duration > 0:
            self.progress_slider.setRange(0, int(self.current_song_duration))
            self.total_time_label.setText(self.format_time(self.current_song_duration))
            self.progress_slider.setEnabled(True)
        else:
            # 如果pygame也获取不到时长，就禁用进度条
            self.status_bar.showMessage(f"正在播放: {os.path.basename(file_path)} (无法获取时长)")
            self.reset_progress_ui()


    
    def on_playback_finished(self):
        """
        【新逻辑】一首歌自然播放完毕。
        首先检查是否在播放列表模式中，再根据循环模式决定下一步。
        """
        # 1. 检查是否处于“预览模式” (由 play_audio 设置)
        if self.current_playlist_index == -1:
            # 如果是预览模式，歌放完了就直接停止，不做任何事
            self._reset_ui_to_stopped_state()
            return

        # --- 如果代码能执行到这里，说明我们一定在“播放列表模式”中 ---
        
        # 2. 单曲循环模式
        if self.loop_mode == LoopMode.LOOP_ONE:
            self.play_song_at_index(self.current_playlist_index)
            return

        # 3. 列表循环模式
        if self.loop_mode == LoopMode.LOOP_LIST:
            next_index = (self.current_playlist_index + 1) % len(self.playlist)
            self.play_song_at_index(next_index)
            return
                
        # 4. 不循环模式 (NO_LOOP)
        next_index = self.current_playlist_index + 1
        if next_index >= len(self.playlist):
            # 列表结束，重置状态
            self._reset_ui_to_stopped_state()
            self.status_bar.showMessage("播放列表已播完")
        else:
            # 播放下一首
            self.play_song_at_index(next_index)

    
    def on_playback_error(self, error_msg):
        QMessageBox.critical(self, "播放错误", f"无法播放文件: {error_msg}")
        self.play_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False) # <--- 禁用暂停按钮
        self.pause_button.setText("暂停")   # <--- 重置文字
        self.is_paused = False             # <--- 重置状态
        self.reset_progress_ui()

    def _reset_ui_to_stopped_state(self):
        """一个辅助函数，将UI控件重置为完全停止的状态。"""
        self.current_playlist_index = -1
        self.highlight_current_song()
        self.reset_progress_ui()
        self.status_bar.showMessage("播放完毕")
        self.play_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("暂停")
        self.is_paused = False   
        
    # 以下选择函数无需修改，因为它们的操作对 QTreeWidgetItem 同样有效
    def select_marked(self):
        self.file_list.blockSignals(True)
        self.file_list.clearSelection()
        for file_path in self.marked_files:
            item = self.path_to_item_map.get(file_path)
            if item:
                item.setSelected(True)
        self.file_list.blockSignals(False)
        self.update_button_states()

    def select_unmarked(self):
        self.file_list.blockSignals(True)
        self.file_list.clearSelection()
        for file_path in self.path_to_item_map.keys():
            if file_path not in self.marked_files:
                item = self.path_to_item_map[file_path]
                item.setSelected(True)
        self.file_list.blockSignals(False)
        self.update_button_states()

    def invert_selection(self):
        self.file_list.blockSignals(True)
        for item in self.path_to_item_map.values():
            item.setSelected(not item.isSelected())
        self.file_list.blockSignals(False)
        self.update_button_states()
        
    def start_conversion(self, target_format, extension, options):
        """启动选中文件的转换过程。"""
        # ★★★ 在这里加上打印语句来调试 ★★★
        print(f"请求转换 -> 格式: {target_format}, 扩展名: {extension}, 参数: {options}")

        if self.converter_thread and self.converter_thread.isRunning():
            QMessageBox.warning(self, "正在转换", "已有文件正在转换中，请稍后再试。")
            return

        selected_items = self.file_list.selectedItems()
        # 这个检查其实在菜单禁用时已经做了，但作为双重保险
        if not selected_items or len(selected_items) > 1:
            return

        input_path = selected_items[0].data(0, Qt.UserRole)
        base, _ = os.path.splitext(input_path)
        output_path = base + "." + extension

        if input_path == output_path:
            QMessageBox.information(self, "提示", "源文件和目标文件格式相同，无需转换。")
            return

        if os.path.exists(output_path):
            reply = QMessageBox.question(self, '文件已存在', 
                                         f"文件 '{os.path.basename(output_path)}' 已存在。\n您要覆盖它吗？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return

        self.status_bar.showMessage(f"正在准备转换: {os.path.basename(input_path)}...")
        
        # ★★★ 将参数字典传递给线程 ★★★
        self.converter_thread = ConverterThread(input_path, output_path, target_format, options)
        self.converter_thread.conversion_progress.connect(self.on_conversion_progress)
        self.converter_thread.conversion_finished.connect(self.on_conversion_finished)
        self.converter_thread.start()

    def on_conversion_progress(self, progress):
        """当转换线程报告进度时更新状态栏。"""
        self.status_bar.showMessage(f"正在转换... {progress}%")

    def on_conversion_finished(self, output_path, error_message):
        """当转换完成时调用。"""
        if error_message:
            QMessageBox.critical(self, "转换失败", f"转换文件时发生错误：\n{error_message}")
            self.status_bar.showMessage("转换失败！")
        else:
            # ★★★ 核心改动：在刷新前，先把要选中的路径记下来 ★★★
            self.path_to_select_after_scan = output_path
            
            QMessageBox.information(self, "转换成功", f"文件已成功转换为：\n{output_path}")
            self.status_bar.showMessage("转换完成！")
            
            # 刷新文件列表以显示新文件
            self.start_loading_files()

        # 清理线程对象
        self.converter_thread = None

    def show_context_menu(self, position):
        menu = QMenu()
        
        reveal_action = menu.addAction("在文件管理器中显示")
        menu.addSeparator()
        play_action = menu.addAction("立即播放")
        add_to_queue_action = menu.addAction("添加到播放队列")
        
        # ★★★ 复用我们的辅助函数来创建子菜单 ★★★
        convert_menu = self._create_conversion_submenu(menu)
        
        menu.addSeparator()
        mark_action = menu.addAction("标记/取消标记选中项")
        delete_action = menu.addAction("删除选中项")
        delete_marked_action = menu.addAction("删除所有已标记文件")
        menu.addSeparator()
        select_marked_action = menu.addAction("选中所有已标记")
        select_unmarked_action = menu.addAction("选中所有未标记")
        invert_selection_action = menu.addAction("反转选择")
        menu.addSeparator()
        clear_marks_action = menu.addAction("清除所有标记")
        clear_queue_action = menu.addAction("清空播放队列")
        
        selected_count = len(self.file_list.selectedItems())
        has_selection = selected_count > 0
        is_single_selection = selected_count == 1
        has_marked_files = bool(self.marked_files)
        has_playlist = bool(self.playlist)

        # --- 3. 根据状态，统一设置所有菜单项 ---
        # 需要至少有一个选中项
        play_action.setEnabled(has_selection)
        add_to_queue_action.setEnabled(has_selection)
        mark_action.setEnabled(has_selection)
        delete_action.setEnabled(has_selection)
        
        # ★★★ Bug修复：这两个功能严格要求只能选中一个 ★★★
        reveal_action.setEnabled(is_single_selection)
        convert_menu.setEnabled(is_single_selection)
        
        # 需要有已标记的文件
        delete_marked_action.setEnabled(has_marked_files)
        clear_marks_action.setEnabled(has_marked_files)
        select_marked_action.setEnabled(has_marked_files)
        
        # 需要播放列表不为空
        clear_queue_action.setEnabled(has_playlist)
        
        action = menu.exec_(self.file_list.mapToGlobal(position))
        
        if action == reveal_action: self.reveal_in_explorer()
        elif action == play_action: self.play_audio()
        elif action == mark_action: self.toggle_mark()
        elif action == delete_action: self.delete_file()
        elif action == add_to_queue_action: self.add_to_queue()
        elif action == clear_queue_action:
            self.clear_playlist()
            self.status_bar.showMessage("播放队列已清空")
        elif action == clear_marks_action: self.clear_all_marks()
        elif action == delete_marked_action: self.delete_marked_files()
        elif action == select_marked_action: self.select_marked()
        elif action == select_unmarked_action: self.select_unmarked()
        elif action == invert_selection_action: self.invert_selection()
    
    def closeEvent(self, event):
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.stop()
            self.scanner_thread.wait()
        self.player_thread.stop()
        self.player_thread.wait(500)
        super().closeEvent(event)

if __name__ == "__main__":
    if sys.platform == "win32" and hasattr(sys, '_MEIPASS'):
        os.environ["PATH"] = sys._MEIPASS + ";" + os.environ["PATH"]
    app = QApplication(sys.argv)
    window = AudioFileManager()
    window.show()
    sys.exit(app.exec_())

