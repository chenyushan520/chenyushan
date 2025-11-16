import os
import json
import asyncio
from typing import List, Dict, Any, Callable

from loguru import logger

from app.utils import utils, video_processor
from app.utils.script_generator import ScriptProcessor
from app.config import config


class ScriptGenerator:
    def __init__(self):
        # 临时目录，用于存放关键帧等中间文件
        self.temp_dir = utils.temp_dir()
        self.keyframes_dir = os.path.join(self.temp_dir, "keyframes")

    async def generate_script(
        self,
        video_path: str,
        video_theme: str = "",
        custom_prompt: str = "",
        frame_interval_input: int = 5,          # 目前未使用，保留接口兼容
        skip_seconds: int = 0,
        threshold: int = 30,
        vision_batch_size: int = 5,
        vision_llm_provider: str = "gemini",
        progress_callback: Callable[[float, str], None] = None
    ) -> List[Dict[Any, Any]]:
        """
        生成视频脚本的核心逻辑（根据解说来源模式自动分流）

        Args:
            video_path:         视频文件路径
            video_theme:        视频主题（会传给文案模型）
            custom_prompt:      自定义提示词（文案风格）
            frame_interval_input: 预留参数，当前不使用
            skip_seconds:       跳过视频开头的秒数
            threshold:          关键帧差异阈值（越大关键帧越少）
            vision_batch_size:  视觉模型一次处理多少张图
            vision_llm_provider:视觉模型提供商，例如 gemini
            progress_callback:  进度回调函数，progress_callback(百分比, 描述)

        Returns:
            List[Dict]: 生成的视频脚本（通常是 JSON 结构）
        """
        if progress_callback is None:
            progress_callback = lambda p, m: None

        # 前端 basic_settings 已经把单选写进了 config.app["narration_source_mode"]
        mode = config.app.get("narration_source_mode", "subtitle")
        logger.info(f"当前解说来源模式: {mode}")

        try:
            if mode == "subtitle":
                # ===== 字幕模式：调用 ASR + 字幕转脚本 =====
                return await self._generate_script_from_subtitle(
                    video_path=video_path,
                    video_theme=video_theme,
                    custom_prompt=custom_prompt,
                    progress_callback=progress_callback,
                )
            else:
                # ===== AI 看画面模式：保持原来的关键帧 + 视觉分析逻辑 =====
                return await self._generate_script_from_vision(
                    video_path=video_path,
                    video_theme=video_theme,
                    custom_prompt=custom_prompt,
                    skip_seconds=skip_seconds,
                    threshold=threshold,
                    vision_batch_size=vision_batch_size,
                    vision_llm_provider=vision_llm_provider,
                    progress_callback=progress_callback,
                )

        except Exception:
            logger.exception("Generate script failed")
            raise

    # ==================================================================
    #  一、字幕模式：调用 ASR（whisper） + 按时间轴生成脚本
    # ==================================================================
    async def _generate_script_from_subtitle(
        self,
        video_path: str,
        video_theme: str,
        custom_prompt: str,
        progress_callback: Callable[[float, str], None],
    ) -> List[Dict[Any, Any]]:
        """
        使用字幕生成脚本：
        1. 调用 subtitle_asr_service.run_asr_for_video(video_path)
        2. 读取 whisper json / srt
        3. 组合为 frame_content_list
        4. 丢给 ScriptProcessor（AI 导演）生成完整剧本
        """
        progress_callback(10, "正在识别字幕...")

        # 延迟导入，避免循环依赖
        try:
            from app.services.subtitle_asr_service import run_asr_for_video
        except ImportError as e:
            logger.error(f"导入 subtitle_asr_service 失败: {e}")
            raise

        # 兼容 run_asr_for_video 同步 / 异步两种写法
        if asyncio.iscoroutinefunction(run_asr_for_video):
            asr_result = await run_asr_for_video(video_path)
        else:
            loop = asyncio.get_running_loop()
            asr_result = await loop.run_in_executor(None, run_asr_for_video, video_path)

        # 期望 asr_result 至少包含 json_path / srt_path 或 segments
        json_path = None
        srt_path = None
        segments: List[Dict[str, Any]] = []

        if isinstance(asr_result, dict):
            json_path = (
                asr_result.get("json_path")
                or asr_result.get("whisper_json_path")
                or asr_result.get("subtitle_json_path")
            )
            srt_path = (
                asr_result.get("srt_path")
                or asr_result.get("subtitle_path")
            )
            if isinstance(asr_result.get("segments"), list):
                segments = asr_result["segments"]

        # 优先从 json 读取详细分段信息
        if not segments and json_path and os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("segments"), list):
                    segments = data["segments"]
                elif isinstance(data, list):
                    segments = data
            except Exception as e:
                logger.warning(f"读取字幕 JSON 失败: {e}")

        # 如果没有 json，就简单解析一下 srt
        if not segments and srt_path and os.path.exists(srt_path):
            segments = self._parse_srt_file(srt_path)

        if not segments:
            raise RuntimeError("未从 ASR 结果中获取到任何字幕段落")

        progress_callback(60, "正在整理字幕内容...")

        # 把字幕段落转换成 frame_content_list（AI 导演输入）
        frame_content_list: List[Dict[str, Any]] = []
        for idx, seg in enumerate(segments, start=1):
            text = (seg.get("text") or seg.get("content") or "").strip()
            if not text:
                continue

            # whisper 通常是秒数
            start = seg.get("start", 0.0)
            end = seg.get("end", start + 1.0)
            timestamp = f"{self._format_seconds(start)}-{self._format_seconds(end)}"

            frame_content_list.append(
                {
                    "_id": idx,
                    "timestamp": timestamp,
                    # 这里沿用原有结构，把“字幕内容”塞进 picture 字段，
                    # Prompt 里会解释：picture 是这一段“字幕+画面”的核心信息
                    "picture": text,
                    "narration": "",
                    "OST": 2,
                }
            )

        if not frame_content_list:
            raise RuntimeError("字幕解析结果为空，无法生成脚本")

        # 用统一的文本 LLM 生成解说脚本
        script = self._generate_script_with_text_llm(
            frame_content_list=frame_content_list,
            video_theme=video_theme,
            custom_prompt=custom_prompt,
            progress_callback=progress_callback,
        )

        return json.loads(script) if isinstance(script, str) else script

    def _parse_srt_file(self, srt_path: str) -> List[Dict[str, Any]]:
        """非常简单的 SRT 解析器，只为兜底使用"""
        segments: List[Dict[str, Any]] = []
        if not os.path.exists(srt_path):
            return segments

        def parse_time(t: str) -> float:
            # 格式：00:00:01,000
            try:
                hms, ms = t.split(",")
                h, m, s = hms.split(":")
                total = int(h) * 3600 + int(m) * 60 + float(s)
                return total + int(ms) / 1000.0
            except Exception:
                return 0.0

        with open(srt_path, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f]

        idx = 0
        while idx < len(lines):
            # 跳过编号行
            if not lines[idx].strip():
                idx += 1
                continue

            # 尝试解析时间行
            if idx + 1 >= len(lines):
                break

            time_line = lines[idx + 1]
            if "-->" not in time_line:
                idx += 1
                continue

            start_str, end_str = [x.strip() for x in time_line.split("-->")]
            start = parse_time(start_str)
            end = parse_time(end_str)

            # 收集文本行
            text_lines: List[str] = []
            j = idx + 2
            while j < len(lines) and lines[j].strip():
                text_lines.append(lines[j].strip())
                j += 1

            text = " ".join(text_lines).strip()
            if text:
                segments.append(
                    {"start": start, "end": end, "text": text}
                )

            idx = j + 1

        return segments

    def _format_seconds(self, seconds: float) -> str:
        """秒数转 SRT 时间戳 HH:MM:SS,mmm"""
        try:
            total_ms = int(seconds * 1000)
            ms = total_ms % 1000
            total_sec = total_ms // 1000
            s = total_sec % 60
            total_min = total_sec // 60
            m = total_min % 60
            h = total_min // 60
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        except Exception:
            return "00:00:00,000"

    # ==================================================================
    #  二、AI 看画面模式：保持原有逻辑，封装成单独方法
    # ==================================================================
    async def _generate_script_from_vision(
        self,
        video_path: str,
        video_theme: str,
        custom_prompt: str,
        skip_seconds: int,
        threshold: int,
        vision_batch_size: int,
        vision_llm_provider: str,
        progress_callback: Callable[[float, str], None],
    ) -> List[Dict[Any, Any]]:
        """原来的 AI 看画面模式逻辑，基本保持不变"""
        # 1. 提取关键帧
        progress_callback(10, "正在提取关键帧...")
        keyframe_files = await self._extract_keyframes(
            video_path=video_path,
            skip_seconds=skip_seconds,
            threshold=threshold,
        )

        # 2. 用统一的 LLM 接口做画面分析 + 文案生成
        script = await self._process_with_llm(
            keyframe_files=keyframe_files,
            video_theme=video_theme,
            custom_prompt=custom_prompt,
            vision_batch_size=vision_batch_size,
            vision_llm_provider=vision_llm_provider,
            progress_callback=progress_callback,
        )

        return json.loads(script) if isinstance(script, str) else script

    async def _extract_keyframes(
        self,
        video_path: str,
        skip_seconds: int,
        threshold: int
    ) -> List[str]:
        """
        提取视频关键帧（带缓存）

        - 会根据视频路径 + 修改时间算一个 hash
        - 同一个视频如果已经提取过关键帧，会直接用缓存，加速很多
        """
        video_hash = utils.md5(video_path + str(os.path.getmtime(video_path)))
        video_keyframes_dir = os.path.join(self.keyframes_dir, video_hash)

        keyframe_files: List[str] = []

        # 1. 先看缓存有没有
        if os.path.exists(video_keyframes_dir):
            for filename in sorted(os.listdir(video_keyframes_dir)):
                if filename.endswith(".jpg"):
                    keyframe_files.append(os.path.join(video_keyframes_dir, filename))

            if keyframe_files:
                logger.info(f"Using cached keyframes: {video_keyframes_dir}")
                return keyframe_files

        # 2. 没有缓存就重新提取
        os.makedirs(video_keyframes_dir, exist_ok=True)

        try:
            processor = video_processor.VideoProcessor(video_path)
            processor.process_video_pipeline(
                output_dir=video_keyframes_dir,
                skip_seconds=skip_seconds,
                threshold=threshold,
            )

            for filename in sorted(os.listdir(video_keyframes_dir)):
                if filename.endswith(".jpg"):
                    keyframe_files.append(os.path.join(video_keyframes_dir, filename))

            return keyframe_files

        except Exception:
            # 提取失败时，清理一下这个目录，防止下次读到半成品
            if os.path.exists(video_keyframes_dir):
                import shutil
                shutil.rmtree(video_keyframes_dir)
            raise

    async def _process_with_llm(
        self,
        keyframe_files: List[str],
        video_theme: str,
        custom_prompt: str,
        vision_batch_size: int,
        vision_llm_provider: str,
        progress_callback: Callable[[float, str], None],
    ) -> str:
        """
        使用统一 LLM 接口处理关键帧：

        1. 用视觉模型分析每一批图片，得到分段描述（带时间段）
        2. 把这些描述整理为 frame_content_list
        3. 交给文本模型生成故事脚本 JSON
        """
        progress_callback(30, "正在初始化视觉分析器...")

        # 视觉模型使用新的 migration_adapter 统一接口
        from app.services.llm.migration_adapter import create_vision_analyzer

        # 读取视觉模型配置
        vision_api_key = config.app.get(f"vision_{vision_llm_provider}_api_key")
        vision_model = config.app.get(f"vision_{vision_llm_provider}_model_name")
        vision_base_url = config.app.get(f"vision_{vision_llm_provider}_base_url")

        if not vision_api_key or not vision_model:
            raise ValueError(f"未配置 {vision_llm_provider} 的 API Key 或模型名称")

        # 创建统一视觉分析器
        analyzer = create_vision_analyzer(
            provider=vision_llm_provider,
            api_key=vision_api_key,
            model=vision_model,
            base_url=vision_base_url,
        )

        progress_callback(40, "正在分析关键帧...")

        # 执行异步视觉分析（分批处理）
        results = await analyzer.analyze_images(
            images=keyframe_files,
            prompt=config.app.get("vision_analysis_prompt"),
            batch_size=vision_batch_size,
        )

        progress_callback(60, "正在整理分析结果...")

        # ===== 1）把视觉结果整理成带时间段的文本 =====
        frame_analysis = ""
        prev_batch_files: List[str] | None = None

        for result in results:
            if "error" in result:
                logger.warning(
                    f"批次 {result.get('batch_index')} 处理出现警告: {result['error']}"
                )
                continue

            batch_files = self._get_batch_files(
                keyframe_files, result, vision_batch_size
            )
            first_timestamp, last_timestamp, _ = self._get_batch_timestamps(
                batch_files, prev_batch_files
            )

            frame_analysis += f"\n=== {first_timestamp}-{last_timestamp} ===\n"
            frame_analysis += result["response"]
            frame_analysis += "\n"

            prev_batch_files = batch_files

        if not frame_analysis.strip():
            raise Exception("未能生成有效的帧分析结果")

        # ===== 2）整理成 frame_content_list，传给文本模型 =====
        progress_callback(70, "正在构建脚本结构...")

        frame_content_list: List[Dict[str, Any]] = []
        prev_batch_files = None

        for result in results:
            if "error" in result:
                continue

            batch_files = self._get_batch_files(
                keyframe_files, result, vision_batch_size
            )
            _, _, timestamp_range = self._get_batch_timestamps(
                batch_files, prev_batch_files
            )

            frame_content = {
                "timestamp": timestamp_range,     # 这一段画面的时间范围
                "picture": result["response"],   # 视觉模型对画面的文字描述
                "narration": "",                 # 先留空，后面由文本模型填
                "OST": 2,
            }
            frame_content_list.append(frame_content)
            prev_batch_files = batch_files

        if not frame_content_list:
            raise Exception("没有有效的帧内容可以处理")

        # ===== 3）用文本模型生成解说脚本 =====
        return self._generate_script_with_text_llm(
            frame_content_list=frame_content_list,
            video_theme=video_theme,
            custom_prompt=custom_prompt,
            progress_callback=progress_callback,
        )

    # ==================================================================
    #  通用：文本 LLM 生成解说脚本（字幕模式 / 画面模式共用）
    # ==================================================================
    def _generate_script_with_text_llm(
        self,
        frame_content_list: List[Dict[str, Any]],
        video_theme: str,
        custom_prompt: str,
        progress_callback: Callable[[float, str], None],
    ) -> str:
        """统一使用文本模型，把 frame_content_list 变成最终 JSON 脚本"""
        progress_callback(90, "正在生成解说文案...")

        text_provider = config.app.get("text_llm_provider", "gemini").lower()
        text_api_key = config.app.get(f"text_{text_provider}_api_key")
        text_model = config.app.get(f"text_{text_provider}_model_name")
        text_base_url = config.app.get(f"text_{text_provider}_base_url")

        if not text_api_key or not text_model:
            raise ValueError(f"未配置文本模型 {text_provider} 的 API 或模型名称")

        # 根据 provider 选择不同的生成器
        if text_provider == "gemini(openai)":
            # 使用 OpenAI 兼容的 Gemini 代理
            from app.utils.script_generator import GeminiOpenAIGenerator

            generator = GeminiOpenAIGenerator(
                model_name=text_model,
                api_key=text_api_key,
                prompt=custom_prompt,
                base_url=text_base_url,
            )
            processor = ScriptProcessor(
                model_name=text_model,
                api_key=text_api_key,
                base_url=text_base_url,
                prompt=custom_prompt,
                video_theme=video_theme,
            )
            processor.generator = generator
        else:
            # 标准 ScriptProcessor（原生 Gemini / DeepSeek / 其他）
            processor = ScriptProcessor(
                model_name=text_model,
                api_key=text_api_key,
                base_url=text_base_url,
                prompt=custom_prompt,
                video_theme=video_theme,
            )

        # 返回 JSON 文本字符串（上层已兼容 str / dict）
        return processor.process_frames(frame_content_list)

    # ----------------- 辅助方法 -----------------

    def _get_batch_files(
        self,
        keyframe_files: List[str],
        result: Dict[str, Any],
        batch_size: int,
    ) -> List[str]:
        """根据 batch_index 取出当前批次的图片文件列表"""
        batch_start = result["batch_index"] * batch_size
        batch_end = min(batch_start + batch_size, len(keyframe_files))
        return keyframe_files[batch_start:batch_end]

    def _get_batch_timestamps(
        self,
        batch_files: List[str],
        prev_batch_files: List[str] | None = None,
    ) -> tuple[str, str, str]:
        """
        把一批关键帧文件名解析为时间范围（支持毫秒级）

        文件名约定示例：
        frame_0001_00:00:05,123.jpg
        """
        if not batch_files:
            logger.warning("Empty batch files")
            return "00:00:00,000", "00:00:00,000", "00:00:00,000-00:00:00,000"

        # 只有 1 张图时，用上一批最后一张 + 当前这一张作为区间
        if len(batch_files) == 1 and prev_batch_files:
            first_frame = os.path.basename(prev_batch_files[-1])
            last_frame = os.path.basename(batch_files[0])
        else:
            first_frame = os.path.basename(batch_files[0])
            last_frame = os.path.basename(batch_files[-1])

        first_time = first_frame.split("_")[2].replace(".jpg", "")
        last_time = last_frame.split("_")[2].replace(".jpg", "")

        def format_timestamp(time_str: str) -> str:
            """将时间字符串转换为 HH:MM:SS,mmm 格式"""
            try:
                if len(time_str) < 1:
                    logger.warning(f"Invalid timestamp format: {time_str}")
                    return "00:00:00,000"

                # 处理毫秒部分
                if "," in time_str:
                    time_part, ms_part = time_str.split(",")
                    ms = int(ms_part)
                else:
                    time_part = time_str
                    ms = 0

                # 处理时分秒
                parts = time_part.split(":")
                if len(parts) == 3:        # HH:MM:SS
                    h, m, s = map(int, parts)
                elif len(parts) == 2:      # MM:SS
                    h = 0
                    m, s = map(int, parts)
                else:                      # SS
                    h = 0
                    m = 0
                    s = int(parts[0])

                # 进位处理
                if s >= 60:
                    m += s // 60
                    s = s % 60
                if m >= 60:
                    h += m // 60
                    m = m % 60

                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

            except Exception as e:
                logger.error(f"时间戳格式转换错误 {time_str}: {str(e)}")
                return "00:00:00,000"

        first_timestamp = format_timestamp(first_time)
        last_timestamp = format_timestamp(last_time)
        timestamp_range = f"{first_timestamp}-{last_timestamp}"

        return first_timestamp, last_timestamp, timestamp_range
