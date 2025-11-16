import json
import os
import traceback
from os import path

from loguru import logger

from app.config import config
from app.config.audio_config import get_recommended_volumes_for_content
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


def start_subclip(task_id: str, params: VideoClipParams, subclip_path_videos: dict | None = None):
    """
    后台任务（统一视频裁剪处理）- 优化版本

    实施基于 OST 类型的统一视频裁剪策略，消除双重裁剪问题：
    - OST=0: 根据 TTS 音频时长动态裁剪，移除原声
    - OST=1: 严格按照脚本 timestamp 精确裁剪，保持原声
    - OST=2: 根据 TTS 音频时长动态裁剪，保持原声

    Args:
        task_id: 任务ID
        params: 视频参数
        subclip_path_videos: 备用视频片段路径（可选，用于兜底）
    """
    global merged_audio_path, merged_subtitle_path

    logger.info(f"\n\n## 开始任务: {task_id}")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=0)

    # =========================
    # 1. 加载剪辑脚本
    # =========================
    logger.info("\n\n## 1. 加载视频脚本")
    video_script_path = path.join(params.video_clip_json_path)

    if path.exists(video_script_path):
        try:
            with open(video_script_path, "r", encoding="utf-8") as f:
                list_script = json.load(f)
                video_list = [i["narration"] for i in list_script]
                video_ost = [i["OST"] for i in list_script]
                time_list = [i["timestamp"] for i in list_script]

                video_script = " ".join(video_list)
                logger.debug(f"解说完整脚本:\n{video_script}")
                logger.debug(f"解说 OST 列表:\n{video_ost}")
                logger.debug(f"解说时间戳列表:\n{time_list}")
        except Exception:
            logger.error("无法读取视频 json 脚本，请检查脚本格式是否正确")
            raise ValueError("无法读取视频 json 脚本，请检查脚本格式是否正确")
    else:
        logger.error(
            f"video_script_path: {video_script_path}\n{traceback.format_exc()}"
        )
        raise ValueError("解说脚本不存在！请检查配置是否正确。")

    # =========================
    # 2. 使用 TTS 生成音频素材
    # =========================
    logger.info("\n\n## 2. 根据 OST 设置生成音频列表")
    # 只为 OST=0 或 2 生成音频：0=只解说，2=解说+原声
    tts_segments = [segment for segment in list_script if segment["OST"] in [0, 2]]
    logger.debug(f"需要生成 TTS 的片段数: {len(tts_segments)}")

    tts_results = voice.tts_multiple(
        task_id=task_id,
        list_script=tts_segments,  # 只传入需要 TTS 的片段
        tts_engine=params.tts_engine,
        voice_name=params.voice_name,
        voice_rate=params.voice_rate,
        voice_pitch=params.voice_pitch,
    )

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=20)

    # =========================
    # 3. 统一视频裁剪（基于 OST）
    # =========================
    logger.info("\n\n## 3. 统一视频裁剪（基于 OST 类型）")

    video_clip_result = clip_video.clip_video_unified(
        video_origin_path=params.video_origin_path,
        script_list=list_script,
        tts_results=tts_results,
    )

    # 按 _id 对应 TTS 结果
    tts_clip_result = {
        tts_result["_id"]: tts_result["audio_file"] for tts_result in tts_results
    }
    subclip_clip_result = {
        tts_result["_id"]: tts_result["subtitle_file"] for tts_result in tts_results
    }

    new_script_list = update_script.update_script_timestamps(
        list_script, video_clip_result, tts_clip_result, subclip_clip_result
    )

    logger.info(f"统一裁剪完成，处理了 {len(video_clip_result)} 个视频片段")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=60)

    # =========================
    # 4. 合并音频和字幕
    # =========================
    logger.info("\n\n## 4. 合并音频和字幕")
    total_duration = sum(script["duration"] for script in new_script_list)

    if tts_segments:
        try:
            # 合并音频文件
            merged_audio_path = audio_merger.merge_audio_files(
                task_id=task_id,
                total_duration=total_duration,
                list_script=new_script_list,
            )
            logger.info(f"音频文件合并成功 -> {merged_audio_path}")

            # 合并字幕文件
            merged_subtitle_path = subtitle_merger.merge_subtitle_files(
                new_script_list
            )
            if merged_subtitle_path:
                logger.info(f"字幕文件合并成功 -> {merged_subtitle_path}")
            else:
                logger.warning("没有有效的字幕内容，将生成无字幕视频")
                merged_subtitle_path = ""
        except Exception as e:
            logger.error(f"合并音频/字幕文件失败: {str(e)}")
            if "merged_audio_path" not in locals():
                merged_audio_path = ""
            if "merged_subtitle_path" not in locals():
                merged_subtitle_path = ""
    else:
        logger.warning("没有需要合并的音频/字幕")
        merged_audio_path = ""
        merged_subtitle_path = ""

    # =========================
    # 5. 合并视频片段
    # =========================
    final_video_paths: list[str] = []
    combined_video_paths: list[str] = []

    combined_video_path = path.join(utils.task_dir(task_id), "merger.mp4")
    logger.info(f"\n\n## 5. 合并视频 => {combined_video_path}")

    video_clips: list[str] = []
    for new_script in new_script_list:
        v_path = new_script.get("video")
        if v_path and os.path.exists(v_path):
            video_clips.append(v_path)
        else:
            logger.warning(
                f"片段 {new_script.get('_id')} 的视频文件不存在或未生成: {v_path}"
            )
            # 如果统一裁剪失败，尝试使用备用方案（如果提供了 subclip_path_videos）
            if subclip_path_videos and new_script.get("_id") in subclip_path_videos:
                backup_video = subclip_path_videos[new_script["_id"]]
                if os.path.exists(backup_video):
                    video_clips.append(backup_video)
                    logger.info(f"使用备用视频: {backup_video}")
                else:
                    logger.error(f"备用视频也不存在: {backup_video}")
            else:
                logger.error(f"无法找到片段 {new_script.get('_id')} 的视频文件")

    logger.info(f"准备合并 {len(video_clips)} 个视频片段")

    merger_video.combine_clip_videos(
        output_video_path=combined_video_path,
        video_paths=video_clips,
        video_ost_list=video_ost,
        video_aspect=params.video_aspect,
        threads=params.n_threads,
    )
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=80)

    # =========================
    # 6. 合并字幕 / BGM / 配音 / 视频
    # =========================
    output_video_path = path.join(utils.task_dir(task_id), "combined.mp4")
    logger.info(
        f"\n\n## 6. 最后一步: 合并字幕/BGM/配音/视频 -> {output_video_path}"
    )

    bgm_path = utils.get_bgm_file()

    # 获取优化的音量配置
    optimized_volumes = get_recommended_volumes_for_content("mixed")

    # 检查是否有 OST=1 的原声片段，如果有，则保持原声音量为 1.0 不变
    has_original_audio_segments = any(segment["OST"] == 1 for segment in list_script)

    # TTS 音量：如果用户设置了非默认值，优先用用户值
    final_tts_volume = (
        params.tts_volume
        if hasattr(params, "tts_volume") and params.tts_volume != 1.0
        else optimized_volumes["tts_volume"]
    )

    # 原声音量：如果存在 OST=1 片段，则锁定为 1.0，与原视频保持一致
    if has_original_audio_segments:
        final_original_volume = 1.0
        logger.info("检测到原声片段，原声音量设置为 1.0 以保持与原视频一致")
    else:
        final_original_volume = (
            params.original_volume
            if hasattr(params, "original_volume")
            and params.original_volume != 0.7
            else optimized_volumes["original_volume"]
        )

    # BGM 音量：同样优先使用用户设置
    final_bgm_volume = (
        params.bgm_volume
        if hasattr(params, "bgm_volume") and params.bgm_volume != 0.3
        else optimized_volumes["bgm_volume"]
    )

    logger.info(
        f"音量配置 - TTS: {final_tts_volume}, 原声: {final_original_volume}, BGM: {final_bgm_volume}"
    )

    options = {
        "voice_volume": final_tts_volume,          # 配音音量
        "bgm_volume": final_bgm_volume,            # 背景音乐音量
        "original_audio_volume": final_original_volume,  # 原视频音量
        "keep_original_audio": True,               # 是否保留原声
        "subtitle_enabled": params.subtitle_enabled,      # 字幕开关
        "subtitle_font": params.font_name,
        "subtitle_font_size": params.font_size,
        "subtitle_color": params.text_fore_color,
        "subtitle_bg_color": None,                 # None 表示透明背景
        "subtitle_position": params.subtitle_position,
        "custom_position": params.custom_position,
        "threads": params.n_threads,
    }

    generate_video.merge_materials(
        video_path=combined_video_path,
        audio_path=merged_audio_path,
        subtitle_path=merged_subtitle_path,
        bgm_path=bgm_path,
        output_path=output_video_path,
        options=options,
    )

    final_video_paths.append(output_video_path)
    combined_video_paths.append(combined_video_path)

    logger.success(f"任务 {task_id} 已完成, 生成 {len(final_video_paths)} 个视频.")

    kwargs = {
        "videos": final_video_paths,
        "combined_videos": combined_video_paths,
    }
    sm.state.update_task(
        task_id, state=const.TASK_STATE_COMPLETE, progress=100, **kwargs
    )
    return kwargs


def start_subclip_unified(task_id: str, params: VideoClipParams):
    """
    统一视频裁剪处理函数 - 完全基于 OST 类型的新实现
    （其实就是对 start_subclip 的一个简单封装）
    """
    return start_subclip(task_id=task_id, params=params, subclip_path_videos=None)


def validate_params(video_path, audio_path, output_file, params):
    """
    验证输入参数
    Args:
        video_path: 视频文件路径
        audio_path: 音频文件路径（可以为空字符串）
        output_file: 输出文件路径
        params: 视频参数
    """
    if not video_path:
        raise ValueError("视频路径不能为空")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    if audio_path and not os.path.exists(audio_path):
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    if not output_file:
        raise ValueError("输出文件路径不能为空")

    output_dir = os.path.dirname(output_file)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    if not params:
        raise ValueError("视频参数不能为空")
