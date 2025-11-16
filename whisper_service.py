import os
import json
from typing import List, Dict, Any
from faster_whisper import WhisperModel


# ================== 路径配置 ==================

# 当前文件所在目录，例如：.../NarratoAI_v0.7/NarratoAI
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 模型目录：你放 model.bin / config.json / tokenizer.json / vocabulary.txt 的地方
# 现在是：.../NarratoAI_v0.7/NarratoAI/models/medium
WHISPER_MODEL_DIR = os.path.join(BASE_DIR, "models", "medium")

# 视频目录：.../NarratoAI_v0.7/NarratoAI/resource/videos
VIDEO_DIR = os.path.join(BASE_DIR, "resource", "videos")


# ================== 工具函数 ==================

def seconds_to_srt_time(sec: float) -> str:
    """
    把秒转换成 SRT 标准时间格式：HH:MM:SS,mmm
    """
    if sec < 0:
        sec = 0

    hours = int(sec // 3600)
    minutes = int((sec % 3600) // 60)
    seconds = int(sec % 60)
    millis = int((sec - int(sec)) * 1000)

    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def ensure_dir(path: str):
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


# ================== 核心转写函数 ==================

def transcribe_video(
    video_filename: str,
    model_size: str = "medium",
    language: str = "auto",
    device: str = "cpu",
    compute_type: str = "int8"
) -> Dict[str, Any]:
    """
    使用 faster-whisper 对单个视频文件进行转写。

    :param video_filename:  视频文件名字，例如 "test01.mp4"
    :param model_size:     模型规格（目前没用到，只是预留）
    :param language:       语言代码，例如 "zh" / "en" / "ja"，"auto" 表示自动检测
    :param device:         "cpu" 或 "cuda"
    :param compute_type:   量化类型，例如 "int8", "int8_float16", "float16"
    :return:               返回一个 dict，包含 JSON 路径、SRT 路径以及分段结果
    """

    # 拼接完整视频路径
    video_path = os.path.join(VIDEO_DIR, video_filename)

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"找不到视频文件: {video_path}")

    print("======================================")
    print(f"开始识别视频: {video_path}")
    print(f"模型目录: {WHISPER_MODEL_DIR}")
    print("======================================")

    # 加载模型（使用本地目录）
    model = WhisperModel(
        WHISPER_MODEL_DIR,
        device=device,
        compute_type=compute_type
    )

    print("模型加载完成，开始转写...")

    segments, info = model.transcribe(
        audio=video_path,
        beam_size=5,
        language=language if language != "auto" else None
    )

    print(f"检测到语言: {info.language}, 置信度: {info.language_probability:.2f}")

    # ================== 收集结果 ==================
    results: List[Dict[str, Any]] = []
    srt_lines: List[str] = []

    for i, seg in enumerate(segments, start=1):
        start = seg.start
        end = seg.end
        text = seg.text.strip()

        # 结果存入列表，后面保存 JSON
        results.append({
            "index": i,
            "start": float(start),
            "end": float(end),
            "text": text
        })

        # 实时打印一下
        print(f"[{start:>6.2f} - {end:>6.2f}] {text}")

        # 转成 SRT 行
        srt_start = seconds_to_srt_time(start)
        srt_end = seconds_to_srt_time(end)
        srt_lines.append(str(i))
        srt_lines.append(f"{srt_start} --> {srt_end}")
        srt_lines.append(text)
        srt_lines.append("")   # 空行

    # ================== 输出路径 ==================
    base_name, _ = os.path.splitext(video_filename)

    json_output_path = os.path.join(VIDEO_DIR, f"{base_name}.whisper.json")
    srt_output_path = os.path.join(VIDEO_DIR, f"{base_name}.srt")

    # 确保目录存在
    ensure_dir(os.path.dirname(json_output_path))
    ensure_dir(os.path.dirname(srt_output_path))

    # 保存 JSON
    with open(json_output_path, "w", encoding="utf-8") as f_json:
        json.dump(
            {
                "video": video_filename,
                "language": info.language,
                "language_probability": float(info.language_probability),
                "segments": results
            },
            f_json,
            ensure_ascii=False,
            indent=2
        )

    # 保存 SRT
    with open(srt_output_path, "w", encoding="utf-8") as f_srt:
        f_srt.write("\n".join(srt_lines))

    print("======================================")
    print(f"✅ 识别完成，JSON 已保存到: {json_output_path}")
    print(f"✅ 字幕 SRT 已保存到: {srt_output_path}")
    print("======================================")

    return {
        "json_path": json_output_path,
        "srt_path": srt_output_path,
        "segments": results,
        "language": info.language,
        "language_probability": float(info.language_probability)
    }


# ================== 命令行测试入口 ==================

if __name__ == "__main__":
    """
    测试步骤（示意）：
    1. 打开命令行，cd 到 NarratoAI 项目目录，例如：
       cd /d <你的_NarratoAI_目录>
    2. 进入 NarratoAI 子目录：
       cd NarratoAI
    3. 运行：
       ..\\lib\\python\\python.exe whisper_service.py

    你可以自行修改下面的 test_video 名字来测试不同视频。
    """
    test_video = "test01.mp4"
    transcribe_video(test_video)
