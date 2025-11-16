import os
import json
import traceback
from datetime import datetime

import streamlit as st

from app.config import config
from app.services.llm import llm_request
from app.utils.logger import logger
from app.utils.file_utils import safe_join, ensure_dir
from app.utils.video_utils import extract_srt_from_json, read_srt_file


def generate_script_short_summary(
    subtitle_path: str,
    temperature: float = 0.7,
    script_style: str = "short_summary",
    save_dir: str = "./NarratoAI/storage/script_output"
):
    """
    生成短剧/短视频解说脚本（ASR 提取字幕模式 或 外部字幕模式）

    参数:
        subtitle_path: 字幕文件路径（json 或 srt）
        temperature: LLM 温度
        script_style: prompt 类型
        save_dir: 输出目录
    """

    try:
        logger.info(f"开始生成短剧脚本，字幕文件: {subtitle_path}")

        # --------------------------
        # 1. 检查字幕路径是否存在
        # --------------------------
        if not subtitle_path:
            raise ValueError("subtitle_path 为空，无法生成脚本！")

        if not os.path.exists(subtitle_path):
            raise FileNotFoundError(f"字幕文件不存在：{subtitle_path}")

        ensure_dir(save_dir)

        # --------------------------
        # 2. 读取字幕
        # --------------------------
        if subtitle_path.endswith(".json"):
            logger.info("检测到 JSON 字幕，尝试转换为 srt...")
            srt_text = extract_srt_from_json(subtitle_path)

        elif subtitle_path.endswith(".srt"):
            logger.info("检测到 SRT 字幕，尝试读取...")
            srt_text = read_srt_file(subtitle_path)

        else:
            raise ValueError("字幕格式必须为 JSON 或 SRT")

        if not srt_text or len(srt_text.strip()) == 0:
            raise ValueError("读取到的字幕为空，无法生成脚本！")

        # --------------------------
        # 3. 自动决定文案生成模型
        # --------------------------
        model_name = config.get("script_model", "deepseek-chat")
        api_key = config.get("script_api_key", "")
        base_url = config.get("script_api_base_url", "")

        if not api_key:
            raise ValueError("文案生成 API 密钥未配置！")
        if not base_url:
            raise ValueError("文案生成 API 地址未配置！")

        logger.info(f"使用文案模型：{model_name} @ {base_url}")

        # --------------------------
        # 4. 构建 Prompt
        # --------------------------
        prompt = (
            "你现在是一个专业的短剧解说文案创作者。\n"
            "下面是视频的字幕内容，请根据字幕内容总结、重写、加工，生成一个流畅、连贯、有吸引力的短视频解说文案。\n"
            "你必须保证：\n"
            "1. 语言自然口语化，像人在说话\n"
            "2. 结构合理，有开场、有发展、有结尾\n"
            "3. 保留重点信息，适当优化表达\n"
            "4. 不要重复字幕内容，要用自己的语言重新讲述\n"
            "5. 不要太短，也不要太冗长\n\n"
            f"字幕内容如下：\n\n{srt_text}\n\n"
            "请生成最终短剧解说文案："
        )

        # --------------------------
        # 5. 调用 LLM 模型
        # --------------------------
        llm_payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }

        logger.info("正在调用文案生成模型...")
        response = llm_request(
            api_key=api_key,
            base_url=base_url,
            payload=llm_payload,
        )

        if not response:
            raise RuntimeError("文案生成失败：模型无响应")

        text = response.strip()
        if len(text) < 10:
            raise RuntimeError("文案生成异常：文本内容过短")

        # --------------------------
        # 6. 写入输出文件
        # --------------------------
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"short_summary_{timestamp}.txt"
        output_path = safe_join(save_dir, filename)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)

        logger.info(f"脚本已保存：{output_path}")
        return output_path, text

    except Exception as e:
        logger.error(f"短剧脚本生成时发生错误: {e}")
        logger.error(traceback.format_exc())
        raise e
# ---------------------------------------------------------
# Streamlit 前端 UI 逻辑（短剧 / 解说脚本生成页）
# ---------------------------------------------------------

def render_short_script_page():
    st.header("短剧解说脚本生成")

    st.markdown("使用字幕（ASR）或上传字幕文件，生成短视频解说文案。")

    # ---------- 字幕输入 ----------
    subtitle_source = st.radio(
        "字幕来源方式",
        ["使用 ASR 自动识别（whisper）", "上传字幕文件（srt/json）"],
        horizontal=True,
    )

    subtitle_path = None

    if subtitle_source == "使用 ASR 自动识别（whisper）":
        st.info("请到左侧菜单的 **→ 字幕提取（Whisper ASR）** 页面先生成字幕文件。")
        generated_files = []

        # 自动读取 resource/videos/*.json & *.srt
        resource_dir = "./NarratoAI/resource/videos"
        if os.path.isdir(resource_dir):
            for f in os.listdir(resource_dir):
                if f.endswith(".json") or f.endswith(".srt"):
                    generated_files.append(os.path.join(resource_dir, f))

        generated_files.sort()

        subtitle_path = st.selectbox(
            "选择已生成的字幕文件",
            generated_files if generated_files else ["暂无字幕文件"],
        )
        if subtitle_path == "暂无字幕文件":
            subtitle_path = None

    else:  # 上传字幕
        file = st.file_uploader("上传一个字幕文件（SRT 或 JSON）", type=["srt", "json"])
        if file:
            save_dir = "./NarratoAI/uploads"
            ensure_dir(save_dir)

            subtitle_path = os.path.join(save_dir, file.name)
            with open(subtitle_path, "wb") as f:
                f.write(file.read())

            st.success(f"字幕文件已保存到：{subtitle_path}")

    # ---------- 温度 ----------
    temperature = st.slider("文案生成随机度（温度）", 0.1, 1.5, 0.7, step=0.05)

    # ---------- 生成按钮 ----------
    if st.button("生成短剧 / 解说脚本"):
        if not subtitle_path:
            st.error("请先选择或上传字幕文件！")
            return

        try:
            with st.spinner("正在生成文案，请稍候..."):
                file_path, text = generate_script_short_summary(
                    subtitle_path=subtitle_path,
                    temperature=temperature,
                )

            st.success("文案生成成功！")
            st.write("生成结果：")
            st.text_area("预览", text, height=300)

            st.download_button(
                label="下载脚本文本文件",
                file_name=os.path.basename(file_path),
                data=text,
            )

        except Exception as e:
            st.error(f"生成过程中发生错误：{str(e)}")
            st.code(traceback.format_exc())


# 如果作为 Streamlit 页面运行
if __name__ == "__main__":
    render_short_script_page()
