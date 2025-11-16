import math
import json
import os.path
import re
import traceback
from os import path
from loguru import logger

from app.config import config
from app.config.audio_config import AudioConfig, get_recommended_volumes_for_content
from app.models import const
from app.models.schema import VideoClipParams
from app.services import (
    voice,
    audio_merger,
    subtitle_merger,
    clip_video,
    merger_video,
    update_script,
    generate_video,
)
from app.services import state as sm
from app.utils import utils


def start_subclip(task_id: str, params: VideoClipParams, subclip_path_videos: dict = None):
    """
    旧版入口：保留兼容。
    实际逻辑统一走 _run_pipeline，仅在前面多跑一遍 create_subclip（如果你还有地方依赖它的结果）。
    """
    logger.info(f"[start_subclip] task_id={task_id}")

    # 统一从 params 里取基础信息
    content_id = params.video_id
    video_subtitles = _normalize_subtitles(params.video_subtitles)
    output_resolution = params.video_output_resolution

    sm.state.set("manager", task_id, "total", 100)
    sm.state.reset_progress("manager", task_id)

    logger.info(f"[start_subclip] subtitles count={len(video_subtitles)}")
    sm.state.set("manager", task_id, "msg", "裁剪视频片段（旧版兼容）")
    sm.state.set("manager", task_id, "progress", 5)

    # 旧版：先用 create_subclip 裁一遍，方便兼容老逻辑/调试
    try:
        subclip_path_videos = clip_video.create_subclip(
            content_id=content_id,
            video_subtitles=video_subtitles,
            output_resolution=output_resolution,
        )
        logger.info(f"[start_subclip] 旧版裁剪完成, subclip_path_videos keys={list(subclip_path_videos.keys())}")
    except Exception as e:
        logger.error(f"[start_subclip] 旧版 create_subclip 过程中出现错误: {e}")
        traceback.print_exc()

    # 主流程统一走新版
    return _run_pipeline(task_id=task_id, params=params, video_subtitles=video_subtitles)


def start_subclip_unified(task_id: str, params: VideoClipParams):
    """
    新版统一裁剪入口（推荐使用）
    """
    logger.info(f"[start_subclip_unified] task_id={task_id}")

    video_subtitles = _normalize_subtitles(params.video_subtitles)
    sm.state.set("manager", task_id, "total", 100)
    sm.state.reset_progress("manager", task_id)

    logger.info(f"[start_subclip_unified] subtitles count={len(video_subtitles)}")

    return _run_pipeline(task_id=task_id, params=params, video_subtitles=video_subtitles)


# ======================================================================
#                          核心流程：统一实现
# ======================================================================

def _run_pipeline(task_id: str, params: VideoClipParams, video_subtitles: list) -> bool:
    """
    统一的脚本 → 配音 → 裁剪 → 合成 流程
    说明：
        - 不修改 list_script 结构，保持与 subtitle_merger / update_script / clip_video_unified 的对接
        - 只对 OST=0/2 做 TTS，OST=1 保留原声，保证音画同步逻辑不被破坏
    """
    content_id = params.video_id
    content_duration = params.video_durations
    output_resolution = params.video_output_resolution

    # 1. 初始化脚本：根据字幕列表生成基础脚本结构
    sm.state.set("manager", task_id, "msg", "初始化脚本")
    sm.state.set("manager", task_id, "progress", 10)

    list_script = subtitle_merger.init_script(
        content_id=content_id,
        video_subtitles=video_subtitles,
    )

    if not list_script:
        logger.error("[pipeline] 字幕列表为空，无法生成脚本")
        sm.state.set("manager", task_id, "msg", "字幕列表为空，无法生成脚本")
        sm.state.set("manager", task_id, "progress", 100)
        return False

    logger.info(f"[pipeline] 初始化脚本完成，分段数量: {len(list_script)}")

    # 2. 根据整段时长 / 解说脚本，更新每一段的时间信息等（音画对齐关键一步）
    sm.state.set("manager", task_id, "msg", "更新脚本时间轴")
    sm.state.set("manager", task_id, "progress", 25)

    list_script = update_script.update_script(
        content_duration,
        video_subtitles,
        list_script,
    )

    if not list_script:
        logger.error("[pipeline] 脚本更新失败")
        sm.state.set("manager", task_id, "msg", "脚本更新失败")
        sm.state.set("manager", task_id, "progress", 100)
        return False

    logger.info(f"[pipeline] 脚本更新完成，分段数量: {len(list_script)}")

    # 3. 批量生成配音（仅 OST=0/2）
    sm.state.set("manager", task_id, "msg", "生成配音")
    sm.state.set("manager", task_id, "progress", 40)

    # 只对 OST=0（无原声，仅配音）和 OST=2（原声+配音）做 TTS；
    # OST=1（仅原声）不做配音，完整保留原视频声音。
    tts_segments = [segment for segment in list_script if segment.get("OST") in [0, 2]]

    if not tts_segments:
        logger.warning("[pipeline] 没有需要生成配音的片段（OST 全是 1），跳过 TTS 步骤")
        tts_results = {}
    else:
        try:
            tts_results = voice.batch_tts(tts_segments, content_id=content_id)
        except Exception as e:
            logger.error(f"[pipeline] 调用 batch_tts 失败: {e}")
            traceback.print_exc()
            sm.state.set("manager", task_id, "msg", "配音生成失败")
            sm.state.set("manager", task_id, "progress", 100)
            return False

        if not tts_results:
            logger.error("[pipeline] 配音生成结果为空")
            sm.state.set("manager", task_id, "msg", "配音生成失败")
            sm.state.set("manager", task_id, "progress", 100)
            return False

    logger.info("[pipeline] 配音生成完成")
    sm.state.set("manager", task_id, "progress", 60)

    # 4. 统一裁剪并合并：这里真正根据 list_script 的时间轴 & TTS 对齐画面
    sm.state.set("manager", task_id, "msg", "裁剪视频并合并音画")

    try:
        video_clip_result = clip_video.clip_video_unified(
            video_origin_path=params.video_origin_path,
            script_list=list_script,
            tts_results=tts_results,
        )

        if not video_clip_result or not video_clip_result.get("video_clips"):
            logger.error("[pipeline] clip_video_unified 返回结果为空或缺少 video_clips")
            sm.state.set("manager", task_id, "msg", "视频裁剪失败")
            sm.state.set("manager", task_id, "progress", 100)
            return False

        sm.state.set("manager", task_id, "progress", 80)
        sm.state.set("manager", task_id, "msg", "合成最终视频")

        final_video_path = generate_video.merge_materials(
            video_clips=video_clip_result["video_clips"],
            bgm_type=params.bgm_type,
            bgm_file=params.bgm_file,
            voice_volume=params.voice_volume,
            bgm_volume=params.bgm_volume,
            output_resolution=output_resolution,
            subtitles=list_script,
            generate_subtitle=params.generate_subtitle,
            subtitle_font=params.subtitle_font,
            subtitle_color=params.subtitle_color,
            subtitle_position=params.subtitle_position,
            subtitle_size=params.subtitle_size,
        )

        if not final_video_path:
            logger.error("[pipeline] 生成最终视频失败（final_video_path 为空）")
            sm.state.set("manager", task_id, "msg", "生成最终视频失败")
            sm.state.set("manager", task_id, "progress", 100)
            return False

        sm.state.set("manager", task_id, "msg", "处理完成")
        sm.state.set("manager", task_id, "progress", 100)
        logger.info(f"[pipeline] 任务 {task_id} 处理完成，最终视频路径: {final_video_path}")
        return True

    except Exception as e:
        logger.error(f"[pipeline] 合并音频/视频过程中出现错误: {e}")
        traceback.print_exc()
        sm.state.set("manager", task_id, "msg", "合并音频/视频失败")
        sm.state.set("manager", task_id, "progress", 100)
        return False


# ======================================================================
#                                工具函数
# ======================================================================

def _normalize_subtitles(video_subtitles) -> list:
    """把可能是 JSON 字符串的 video_subtitles 统一变成 list"""
    if isinstance(video_subtitles, list):
        return video_subtitles

    try:
        return json.loads(video_subtitles or "[]")
    except Exception:
        logger.warning(f"[normalize_subtitles] 无法解析 video_subtitles，使用空列表。raw={video_subtitles}")
        return []
