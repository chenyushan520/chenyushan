import os
import json
import uuid
import shutil
import subprocess
from datetime import datetime

from app.services.config import config


# 读取视频分辨率
def get_video_resolution(video_path: str):
    """
    使用 ffprobe 解析视频的宽高。
    """
    try:
        cmd = [
            config.ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        w = info["streams"][0]["width"]
        h = info["streams"][0]["height"]
        return int(w), int(h)
    except Exception:
        # 如果失败，回退到 UI 设置的分辨率
        return compute_resolution_from_ui()


# UI 分辨率 → 真实宽高
def compute_resolution_from_ui():
    """
    根据 UI 的 video_aspect + video_quality 计算分辨率。
    UI 已保存到 config.app 中。
    """
    aspect = config.app.get("video_aspect", "portrait")       # portrait / landscape
    quality = config.app.get("video_quality", "1080p")        # 720p/1080p/2160p...

    # 高度由 quality 决定
    if quality.endswith("p"):
        height = int(quality.replace("p", ""))
    else:
        height = 1080  # fallback

    # 计算宽度
    if aspect == "portrait":
        # 竖屏：9:16
        width = int(height * 9 / 16)
    else:
        # 横屏：16:9
        width = int(height * 16 / 9)

    return width, height


# 读取模板 JSON
def load_template_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# 将文件复制到目标路径
def copy_asset(src: str, dst: str):
    if src and os.path.exists(src):
        shutil.copy(src, dst)


def export_capcut_draft(
    task_id: str,
    video_path: str,
    audio_path: str,
    output_root=r"F:\剪映草稿\JianyingPro Drafts"
):
    """
    导出剪映草稿工程（可直接打开）。
    """
    # 草稿目录（每个任务独立）
    draft_dir = os.path.join(output_root, f"NarratoAI-自动生成-{task_id}")
    resources_dir = os.path.join(draft_dir, "Resources")

    # 模板路径
    template_dir = os.path.join("resource", "capcut_template")
    content_tpl_path = os.path.join(template_dir, "draft_content_template.json")
    meta_tpl_path = os.path.join(template_dir, "draft_meta_info_template.json")

    # 清理并重建草稿目录
    if os.path.exists(draft_dir):
        shutil.rmtree(draft_dir)
    os.makedirs(resources_dir, exist_ok=True)
    os.makedirs(os.path.join(draft_dir, "matting"))
    os.makedirs(os.path.join(draft_dir, "smart_crop"))
    os.makedirs(os.path.join(draft_dir, "common_attachment"))

    # 复制素材
    video_filename = os.path.basename(video_path)
    audio_filename = os.path.basename(audio_path)

    video_dst = os.path.join(resources_dir, video_filename)
    audio_dst = os.path.join(resources_dir, audio_filename)

    copy_asset(video_path, video_dst)
    copy_asset(audio_path, audio_dst)

    # 计算分辨率
    width, height = get_video_resolution(video_path)

    # 加载模板
    draft_content = load_template_json(content_tpl_path)
    draft_meta = load_template_json(meta_tpl_path)

    # 为剪映素材生成唯一 ID
    video_material_id = str(uuid.uuid4())
    audio_material_id = str(uuid.uuid4())

    # =====================
    # 写入素材（materials）
    # =====================
    draft_content["materials"]["videos"][0]["id"] = video_material_id
    draft_content["materials"]["videos"][0]["path"] = f"Resources/{video_filename}"
    draft_content["materials"]["videos"][0]["media"]["width"] = width
    draft_content["materials"]["videos"][0]["media"]["height"] = height

    draft_content["materials"]["audios"][0]["id"] = audio_material_id
    draft_content["materials"]["audios"][0]["path"] = f"Resources/{audio_filename}"

    # =====================
    # 写入轨道结构（tracks）
    # =====================
    draft_content["tracks"][0]["segments"][0]["material_id"] = video_material_id
    draft_content["tracks"][1]["segments"][0]["material_id"] = audio_material_id

    # =====================
    # 写入画布尺寸
    # =====================
    draft_content["canvas_size"]["width"] = width
    draft_content["canvas_size"]["height"] = height

    # =====================
    # 写入 draft_meta_info
    # =====================
    draft_meta["draft_version"] = "23.3.0"
    draft_meta["project_name"] = f"NarratoAI-自动生成-{task_id}"
    draft_meta["create_time"] = int(datetime.now().timestamp() * 1000)
    draft_meta["canvas_ratio"]["width"] = width
    draft_meta["canvas_ratio"]["height"] = height

    # 保存 JSON
    with open(os.path.join(draft_dir, "draft_content.json"), "w", encoding="utf-8") as f:
        json.dump(draft_content, f, ensure_ascii=False, indent=4)

    with open(os.path.join(draft_dir, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        json.dump(draft_meta, f, ensure_ascii=False, indent=4)

    return draft_dir
