#!/usr/bin/env python
# -*- coding: UTF-8 -*-

'''
@Project: NarratoAI
@File   : 生成介绍文案（generate_narration_script）
@Author : Viccy同学
@Date   : 2025/5/8 上午11:33
'''

import json
import os
import traceback
import asyncio
from openai import OpenAI
from loguru import logger

from app.config import config
from app.utils import utils
from app.services import state as sm
from app.services.prompts import PromptManager
import app.services.llm  # 这会触发 LLM 提供商注册
from app.services.llm.migration_adapter import generate_narration as generate_narration_new
from app.services.narration_prompt import build_director_prompt    # 导演系统提示词构建


def _get_user_style_prompt() -> str:
    """
    统一获取“用户自定义解说风格提示词”

    新版 UI：存到 config.app["narration_style_text"]
    老版 UI：存到 config.ui["narration_style_prompt"]

    为了兼容两种写法，这里做一个统一入口。
    """
    # 新版配置优先
    txt = str(config.app.get("narration_style_text", "")).strip()
    if txt:
        return txt

    # 兼容旧字段
    return str(config.ui.get("narration_style_prompt", "")).strip()


def parse_frame_analysis_to_markdown(json_file_path: str) -> str:
    """
    解析视频帧分析 / 字幕 JSON 文件并转换为 Markdown 格式，
    提供给大模型作为“看过视频之后的结构化描述”。

    :param json_file_path: JSON 文件路径
    :return: Markdown 格式字符串
    """
    if not os.path.exists(json_file_path):
        return f"错误: 文件 {json_file_path} 不存在"

    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)

        markdown_output = []

        # === 1. 视频基础信息 ===
        video_info = data.get("video_info", {})
        duration = video_info.get("duration", "未知")
        frame_count = video_info.get("frame_count", "未知")
        fps = video_info.get("fps", "未知")
        resolution = video_info.get("resolution", "未知")

        markdown_output.append("# 视频分析结果\n")
        markdown_output.append("## 视频基础信息\n")
        markdown_output.append(f"- 时长: {duration}\n")
        markdown_output.append(f"- 帧数: {frame_count}\n")
        markdown_output.append(f"- 帧率: {fps}\n")
        markdown_output.append(f"- 分辨率: {resolution}\n")

        # === 2. 场景 / 片段信息（兼容 “帧分析” 和 “ASR 字幕分析” 两种结构） ===
        # 2.1 场景分段（如果存在 scenes 字段）
        scenes = data.get("scenes", [])
        if scenes:
            markdown_output.append("## 场景分段信息（基于画面分析）\n")
            for i, scene in enumerate(scenes, start=1):
                start_time = scene.get("start_time", "未知")
                end_time = scene.get("end_time", "未知")
                description = scene.get("description", "无描述")

                markdown_output.append(f"### 场景 {i}\n")
                markdown_output.append(f"- 起始时间: {start_time}\n")
                markdown_output.append(f"- 结束时间: {end_time}\n")
                markdown_output.append(f"- 画面描述: {description}\n")

        # 2.2 字幕片段（如果是 ASR 字幕 JSON）
        # 约定结构：data["subtitles"] = [{ "start": 0.0, "end": 3.2, "text": "……" }, ...]
        subtitles = data.get("subtitles", [])
        if subtitles:
            markdown_output.append("## 字幕分段信息（基于语音识别）\n")
            for i, seg in enumerate(subtitles, start=1):
                start = seg.get("start", 0.0)
                end = seg.get("end", 0.0)
                text = seg.get("text", "").strip()

                markdown_output.append(f"### 字幕片段 {i}\n")
                markdown_output.append(f"- 起始时间: {start:.3f} 秒\n")
                markdown_output.append(f"- 结束时间: {end:.3f} 秒\n")
                markdown_output.append(f"- 原文字幕: {text}\n")

        # === 3. 关键帧分析信息（如果是画面分析 JSON） ===
        keyframes = data.get("keyframes", [])
        if keyframes:
            markdown_output.append("## 关键帧分析\n")
            for i, frame in enumerate(keyframes, start=1):
                timestamp = frame.get("timestamp", "未知")
                objects = frame.get("objects", [])
                actions = frame.get("actions", [])
                emotions = frame.get("emotions", [])
                summary = frame.get("summary", "无总结")

                markdown_output.append(f"### 关键帧 {i}\n")
                markdown_output.append(f"- 时间戳: {timestamp}\n")
                markdown_output.append(
                    f"- 画面中的主要物体: {', '.join(objects) if objects else '无'}\n"
                )
                markdown_output.append(
                    f"- 动作: {', '.join(actions) if actions else '无'}\n"
                )
                markdown_output.append(
                    f"- 情绪: {', '.join(emotions) if emotions else '无'}\n"
                )
                markdown_output.append(f"- 总结: {summary}\n")

        # === 4. 整体风格 / 节奏信息 ===
        overall_style = data.get("overall_style", {})
        if overall_style:
            markdown_output.append("## 整体画面风格与节奏\n")
            color_tone = overall_style.get("color_tone", "未知")
            pacing = overall_style.get("pacing", "未知")
            atmosphere = overall_style.get("atmosphere", "未知")
            markdown_output.append(f"- 色调: {color_tone}\n")
            markdown_output.append(f"- 节奏: {pacing}\n")
            markdown_output.append(f"- 氛围: {atmosphere}\n")

        return "\n".join(markdown_output)

    except Exception as e:
        return f"解析JSON时发生错误: {str(e)}"


async def _generate_narration_legacy(video_id: str, frame_json: str = "", _type: int = 0):
    """
    旧版文案生成逻辑（保留兼容性）
    - 直接使用 OpenAI ChatCompletion
    - 仍然支持前端“解说风格提示词”
    """
    logger.info(f"use legacy narration generator, video_id={video_id}")
    if not frame_json:
        return {"result": 0, "msg": "frame_json is empty"}

    try:
        prompt_manager = PromptManager()
        base_prompt = prompt_manager.get_short_drama_prompt()

        # 【统一入口】用户自定义风格提示词
        user_style_prompt = _get_user_style_prompt()
        if user_style_prompt:
            full_prompt = (
                base_prompt
                + "\n\n[用户自定义解说风格要求]\n"
                + user_style_prompt
            )
        else:
            full_prompt = base_prompt

        logger.debug(f"legacy full prompt: {full_prompt}")

        # 把帧分析 / 字幕 JSON 转成 Markdown 作为 user 内容
        content = parse_frame_analysis_to_markdown(frame_json)

        client = OpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url
        )
        resp = client.chat.completions.create(
            model=config.narration_model_name,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": content},
            ],
        )
        text = resp.choices[0].message.content
        return {"result": 1, "msg": "success", "text": text}

    except Exception as e:
        logger.error(f"legacy narration error: {e}")
        traceback.print_exc()
        return {"result": 0, "msg": str(e)}


async def generate_narration(video_id: str, frame_json: str = "", _type: int = 0):
    """
    新版文案生成：
    - 优先走 migration_adapter（支持 Deepseek、OpenAI、其他服务商）
    - 自动注入“导演系统提示词 + 自定义风格提示词”
    - 同时兼容“AI 看画面 JSON” & “ASR 字幕 JSON”
    - 如果失败会自动降级到旧逻辑 _generate_narration_legacy
    """
    logger.info(
        f"generate_narration: video_id={video_id}, frame_json={frame_json}, type={_type}"
    )
    if not frame_json:
        return {"result": 0, "msg": "frame_json is empty"}

    try:
        # 1. 构造“AI 导演系统规则”提示词（黄金结构 + 技术规范等）
        channel_name = config.app.get("channel_name", "NarratoAI 解说频道")
        director_prompt = build_director_prompt(channel_name=channel_name)

        # 2. 画面分析 / 字幕 JSON 转 Markdown，
        #    作为“我已经看完视频（或看完字幕）后的结构化笔记”
        markdown_content = parse_frame_analysis_to_markdown(frame_json)

        # 3. 用户在前端输入的“解说风格提示词”（完全自由，比如：毒舌、纪实、顾我、先高潮再转折…）
        user_style_prompt = _get_user_style_prompt()

        # 4. 最终给模型的 system prompt = 导演规则 + 用户风格（如果有）
        full_prompt_parts = [director_prompt]
        if user_style_prompt:
            full_prompt_parts.append("\n[用户自定义风格要求]\n" + user_style_prompt)

        full_prompt = "\n\n".join(full_prompt_parts)

        logger.debug(
            f"[director_prompt] + [user_style_prompt] 组合后的提示词：{full_prompt}"
        )

        # 5. 调用新版 migration_adapter 统一接口
        result = await generate_narration_new(
            video_id=video_id,
            system_prompt=full_prompt,
            user_content=markdown_content,
            _type=_type,
        )

        # 兼容原有返回格式
        if result and isinstance(result, dict):
            return {"result": 1, "msg": "success", "text": result.get("text", "")}
        else:
            logger.warning("migration_adapter 返回格式不符合预期，回退到 legacy 逻辑")
            return await _generate_narration_legacy(video_id, frame_json, _type)

    except Exception as e:
        logger.error(f"generate_narration error: {e}")
        traceback.print_exc()
        # 出错时回退到旧逻辑
        return await _generate_narration_legacy(video_id, frame_json, _type)


def generate_narration_sync(video_id: str, frame_json: str = "", _type: int = 0):
    """
    同步封装，方便在非 async 环境中调用
    """
    return asyncio.run(generate_narration(video_id, frame_json, _type))
