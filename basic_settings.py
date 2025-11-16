import traceback

import streamlit as st
import os
from app.config import config
from app.utils import utils
from loguru import logger


def validate_api_key(api_key: str, provider: str) -> tuple[bool, str]:
    """验证API密钥格式"""
    if not api_key or not api_key.strip():
        return False, f"{provider} API密钥不能为空"

    # 基本长度检查
    if len(api_key.strip()) < 10:
        return False, f"{provider} API密钥长度过短，请检查是否正确"

    return True, ""


def validate_base_url(base_url: str, provider: str) -> tuple[bool, str]:
    """验证Base URL格式"""
    if not base_url or not base_url.strip():
        return True, ""  # base_url可以为空

    base_url = base_url.strip()
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        return False, f"{provider} Base URL必须以http://或https://开头"

    return True, ""


def validate_model_name(model_name: str, provider: str) -> tuple[bool, str]:
    """验证模型名称"""
    if not model_name or not model_name.strip():
        return False, f"{provider} 模型名称不能为空"

    return True, ""


def validate_litellm_model_name(model_name: str, model_type: str) -> tuple[bool, str]:
    """验证 LiteLLM 模型名称格式

    Args:
        model_name: 模型名称，应为 provider/model 格式
        model_type: 模型类型（如"视频分析"、"文案生成"）

    Returns:
        (是否有效, 错误消息)
    """
    if not model_name or not model_name.strip():
        return False, f"{model_type} 模型名称不能为空"

    model_name = model_name.strip()

    # LiteLLM 推荐格式：provider/model（如 gemini/gemini-2.0-flash-lite）
    # 但也支持直接的模型名称（如 gpt-4o，LiteLLM 会自动推断 provider）

    # 检查是否包含 provider 前缀（推荐格式）
    if "/" in model_name:
        parts = model_name.split("/")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return (
                False,
                f"{model_type} 模型名称格式错误。推荐格式: provider/model （如 gemini/gemini-2.0-flash-lite）",
            )

        # 验证 provider 名称（只允许字母、数字、下划线、连字符）
        provider = parts[0]
        if not provider.replace("-", "").replace("_", "").isalnum():
            return False, f"{model_type} Provider 名称只能包含字母、数字、下划线和连字符"
    else:
        # 直接模型名称也是有效的（LiteLLM 会自动推断）
        logger.debug(f"{model_type} 模型名称未包含 provider 前缀，LiteLLM 将自动推断")

    # 基本长度检查
    if len(model_name) < 3:
        return False, f"{model_type} 模型名称过短"

    if len(model_name) > 200:
        return False, f"{model_type} 模型名称过长"

    return True, ""


def show_config_validation_errors(errors: list):
    """显示配置验证错误"""
    if errors:
        for error in errors:
            st.error(error)


def render_basic_settings(tr):
    """渲染基础设置面板"""
    with st.expander(tr("Basic Settings"), expanded=False):
        # =========================
        # 解说来源模式（字幕 / 画面）
        # =========================
        st.markdown("### 解说来源模式")

        mode_options = {
            "subtitle": "使用字幕生成解说（ASR 提取字幕 + 时间轴）",
            "vision": "使用视频画面分析生成解说（AI 看画面）",
        }

        # 从配置里读取已保存的模式，默认字幕模式
        saved_mode = config.app.get("narration_source_mode", "subtitle")
        if saved_mode not in mode_options:
            saved_mode = "subtitle"

        selected_mode = st.radio(
            "解说生成方式",
            options=list(mode_options.keys()),
            index=list(mode_options.keys()).index(saved_mode),
            format_func=lambda k: mode_options[k],
            help="字幕模式：先提取字幕再写剧本；画面模式：让 AI 直接看视频画面来写剧本。",
            key="narration_source_mode_radio",
            horizontal=True,
        )

        # 写入配置 & session_state（后端真正生成脚本时会读取这个值）
        config.app["narration_source_mode"] = selected_mode
        st.session_state["narration_source_mode"] = selected_mode

        # 同步脚本模式给后端：subtitle -> asr，vision -> vision
        script_mode = "asr" if selected_mode == "subtitle" else "vision"
        config.app["script_mode"] = script_mode
        st.session_state["script_mode"] = script_mode

        st.markdown("---")

        # 三列布局
        config_panels = st.columns(3)
        left_config_panel = config_panels[0]
        middle_config_panel = config_panels[1]
        right_config_panel = config_panels[2]

        with left_config_panel:
            render_language_settings(tr)
            render_duration_settings(tr)  # 成品时长 & 原声占比
            render_proxy_settings(tr)

        with middle_config_panel:
            render_vision_llm_settings(tr)  # 视频分析模型设置

        with right_config_panel:
            render_text_llm_settings(tr)  # 文案生成模型设置
            render_narration_style_settings(tr)  # 解说风格设置窗口

        # --- 统一保存按钮：把这一页所有 config 的修改写入配置文件 ---
        st.markdown("---")
        if st.button("保存基础设置", use_container_width=True):
            try:
                config.save_config()
                st.success("基础设置已保存 ✅")
            except Exception as e:
                st.error("保存失败，请查看日志")
                logger.error(f"保存基础设置失败: {e}")


def render_narration_style_settings(tr):
    """渲染解说风格自由提示词设置"""

    st.subheader("解说风格设置")

    st.markdown(
        "在这里可以自由写任何你想要的解说风格提示词，例如：毒舌吐槽、古风文案、模仿某个同行的节奏等。"
        "这些内容会在每次生成解说时作为【风格要求】传给后端的大模型。"
    )

    # 结构模式选择：用于控制整体叙事形态（黄金结构 / Vlog / 纪录片 / 悬疑 / 自由）
    structure_options = {
        "golden": "默认黄金叙事结构（钩子-发展-转折-高潮-余韵）",
        "vlog": "Vlog 口播结构（像朋友聊天一样随性讲）",
        "documentary": "纪录片结构（背景-经过-结果，冷静克制）",
        "suspense": "悬疑反转结构（前期埋伏笔，结尾大反转）",
        "free": "完全自由结构（不使用任何固定套路）",
    }

    # 读取当前已保存的结构模式，默认 golden
    saved_mode = config.app.get("narration_structure_mode", "golden")
    if saved_mode not in structure_options:
        saved_mode = "golden"

    # 下拉框选择结构模式
    structure_mode = st.selectbox(
        "解说结构模式",
        options=list(structure_options.keys()),
        index=list(structure_options.keys()).index(saved_mode),
        format_func=lambda k: structure_options[k],
        help=(
            "这里控制解说脚本的整体结构（比如是否用钩子-发展-高潮-余韵）。"
            "如果你在下面的风格提示词里再次写了结构要求，以你的提示词为最高优先级。"
        ),
        key="narration_structure_mode_select",
    )

    # 写入配置 & session_state，后端可以直接用
    config.app["narration_structure_mode"] = structure_mode
    st.session_state["narration_structure_mode"] = structure_mode

    # 读取已有的风格提示（如果之前保存过）
    existing_style = config.app.get("narration_style_text", "")

    # 风格提示词输入框
    style_text = st.text_area(
        "自定义解说风格提示词",
        value=existing_style,
        height=160,
        placeholder=(
            "示例：\n"
            "解说人格：一个经验老到、嘴毒但有审美的电影解说人。\n"
            "语气要求：整体偏毒舌、爱吐槽，但吐槽点要精准，不要无脑骂；"
            "该夸的时候要真诚夸几句，形成对比。\n"
            "结构要求：可以写“使用 Vlog 自由口播结构”，"
            "或者“不要钩子-发展-高潮结构，改用纪录片按时间线讲”，完全由你决定。\n"
            "如果参考某个同行频道的节奏，可以写：整体节奏参考 XX 频道，但禁止抄袭对方的台词和梗，只学习情绪和节奏感。"
        ),
        help="你写什么风格，这段文字就会作为【风格要求】传给 AI，用来指导解说文案的口吻和人格。",
        key="narration_style_text_input",
    )

    # 实时写入配置 & session_state，让后端立即生效
    config.app["narration_style_text"] = style_text
    st.session_state["narration_style_text"] = style_text

    # 自动保存到配置文件，重启后也能保留
    try:
        config.save_config()
    except Exception as e:
        logger.error(f"保存解说风格配置失败: {str(e)}")


def render_language_settings(tr):
    """渲染语言和解说目标语言设置"""

    # ---- 1. 界面语言固定为简体中文 ----
    st.subheader(tr("Language"))

    # 直接把 UI 语言锁死为 zh，不再让用户选择
    config.ui["language"] = "zh"
    st.session_state["ui_language"] = "zh"
    st.write("界面语言：简体中文（已固定）")

    # ---- 2. 解说目标语言（脚本输出语言）----
    st.markdown("---")
    st.markdown("**解说目标语言（脚本输出语言）**")

    # 支持的解说语言列表
    narration_lang_display = {
        "zh": "zh - 简体中文（解说）",
        "en": "en - English（英文解说）",
        "fr": "fr - Français（法语解说）",
        "de": "de - Deutsch（德语解说）",
        "it": "it - Italiano（意大利语解说）",
        "ja": "ja - 日本語（日语解说）",
        "ko": "ko - 한국어（韩语解说）",
        "ms": "ms - Bahasa Melayu（马来语解说）",
        "id": "id - Bahasa Indonesia（印尼语解说）",
        "vi": "vi - Tiếng Việt（越南语解说）",
    }

    # 当前已保存的解说语言，默认 zh
    current_narration_lang = config.app.get("narration_language", "zh")
    narration_codes = list(narration_lang_display.keys())

    # 找到默认 index
    try:
        default_idx = narration_codes.index(current_narration_lang)
    except ValueError:
        default_idx = 0

    # 这里的选择框就是“上传视频后，用什么语言解说”
    narration_lang_code = st.selectbox(
        "解说目标语言（脚本生成语言）",
        options=narration_codes,
        index=default_idx,
        format_func=lambda k: narration_lang_display.get(k, k),
        key="narration_language_select",
        help="这里控制 AI 生成解说文案的语言，比如选 en 就会用英文写解说。",
    )

    # 写入 session_state 和 config，供后端使用
    config.app["narration_language"] = narration_lang_code
    st.session_state["narration_language"] = narration_lang_code


def render_duration_settings(tr):
    """渲染成品总时长 & 原声对白设置"""

    st.subheader("成品时长 & 原声对白设置")

    # 1. 成品目标总时长（分钟）
    target_minutes_default = config.app.get("target_duration_minutes", 0.0)

    target_minutes = st.number_input(
        "成品目标总时长（分钟）",
        min_value=0.0,
        max_value=10.0,
        value=float(target_minutes_default),
        step=0.5,
        help="0 表示不限制；否则会把最终成品视频尽量控制在这个时长内。",
    )

    config.app["target_duration_minutes"] = float(target_minutes)
    st.session_state["target_duration_minutes"] = float(target_minutes)

    # 2. 原声对白占比（百分比）
    st.markdown("---")
    original_ratio_default = config.app.get("original_audio_ratio_percent", 30)

    original_ratio_percent = st.slider(
        "原声对白占比（%）",
        min_value=0,
        max_value=100,
        value=int(original_ratio_default),
        step=5,
        help="控制成品中原声对白的占比。例如 20%~50%。",
    )

    config.app["original_audio_ratio_percent"] = int(original_ratio_percent)
    config.app["original_audio_ratio"] = float(original_ratio_percent) / 100.0

    st.session_state["original_audio_ratio_percent"] = int(original_ratio_percent)
    st.session_state["original_audio_ratio"] = float(original_ratio_percent) / 100.0


def render_proxy_settings(tr):
    """渲染代理设置"""
    # 获取当前代理状态
    proxy_enabled = config.proxy.get("enabled", False)
    proxy_url_http = config.proxy.get("http")
    proxy_url_https = config.proxy.get("https")

    # 添加代理开关
    proxy_enabled = st.checkbox(tr("Enable Proxy"), value=proxy_enabled)

    # 保存代理开关状态（实时）
    config.proxy["enabled"] = proxy_enabled
    st.session_state["proxy_enabled"] = proxy_enabled

    # 只有在代理启用时才显示代理设置输入框
    if proxy_enabled:
        HTTP_PROXY = st.text_input(tr("HTTP_PROXY"), value=proxy_url_http)
        HTTPS_PROXY = st.text_input(tr("HTTPs_PROXY"), value=proxy_url_https)

        if HTTP_PROXY and HTTPS_PROXY:
            config.proxy["http"] = HTTP_PROXY
            config.proxy["https"] = HTTPS_PROXY
            os.environ["HTTP_PROXY"] = HTTP_PROXY
            os.environ["HTTPS_PROXY"] = HTTPS_PROXY
    else:
        # 当代理被禁用时，清除环境变量和配置
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)
        config.proxy["http"] = ""
        config.proxy["https"] = ""


def test_vision_model_connection(api_key, base_url, model_name, provider, tr):
    """老版本测试视觉模型连接（保留以兼容可能的旧代码调用）"""
    import requests

    logger.debug(f"大模型连通性测试: {base_url} 模型: {model_name} apikey: {api_key}")
    if provider.lower() == "gemini":
        # 原生Gemini API测试
        try:
            request_data = {
                "contents": [{"parts": [{"text": "直接回复我文本'当前网络可用'"}]}]
            }

            api_base_url = base_url
            url = f"{api_base_url}/models/{model_name}:generateContent"
            response = requests.post(
                url,
                json=request_data,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                timeout=10,
            )

            if response.status_code == 200:
                return True, tr("原生Gemini模型连接成功")
            else:
                return False, f"{tr('原生Gemini模型连接失败')}: HTTP {response.status_code}"
        except Exception as e:
            return False, f"{tr('原生Gemini模型连接失败')}: {str(e)}"
    elif provider.lower() == "gemini(openai)":
        # OpenAI兼容的Gemini代理测试
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            test_url = f"{base_url.rstrip('/')}/chat/completions"
            test_data = {
                "model": model_name,
                "messages": [{"role": "user", "content": "直接回复我文本'当前网络可用'"}],
                "stream": False,
            }

            response = requests.post(test_url, headers=headers, json=test_data, timeout=10)
            if response.status_code == 200:
                return True, tr("OpenAI兼容Gemini代理连接成功")
            else:
                return False, f"{tr('OpenAI兼容Gemini代理连接失败')}: HTTP {response.status_code}"
        except Exception as e:
            return False, f"{tr('OpenAI兼容Gemini代理连接失败')}: {str(e)}"
    else:
        from openai import OpenAI

        try:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )

            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": [
                            {"type": "text", "text": "You are a helpful assistant."}
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20241022/emyrja/dog_and_girl.jpeg"
                                },
                            },
                            {"type": "text", "text": "回复我网络可用即可"},
                        ],
                    },
                ],
            )
            if response and response.choices:
                return True, tr("QwenVL model is available")
            else:
                return False, tr("QwenVL model returned invalid response")

        except Exception as e:
            return False, f"{tr('QwenVL model is not available')}: {str(e)}"


def test_litellm_vision_model(api_key: str, base_url: str, model_name: str, tr) -> tuple[bool, str]:
    """测试 LiteLLM 视觉模型连接（尽量通用：支持国内外 OpenAI 兼容 / LiteLLM 支持的视觉模型）"""
    try:
        import litellm
        import os
        import base64
        import io
        from PIL import Image

        logger.debug(
            f"LiteLLM 视觉模型连通性测试: model={model_name}, api_key={api_key[:10]}..., base_url={base_url}"
        )

        # 提取 provider 名称
        provider = model_name.split("/")[0] if "/" in model_name else "unknown"

        # 设置 API key 到环境变量（覆盖常见国内外大模型）
        env_key_mapping = {
            # 国外
            "gemini": "GEMINI_API_KEY",
            "google": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "qwen": "QWEN_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
            "siliconflow": "SILICONFLOW_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            # 国内常见
            "doubao": "DOUBAO_API_KEY",          # 豆包
            "volcengine": "VOLCENGINE_API_KEY",  # 火山引擎
            "ernie": "ERNIE_API_KEY",            # 文心一言
            "yiyan": "YIYAN_API_KEY",
            "baidu": "BAIDU_API_KEY",
            "qianfan": "QIANFAN_API_KEY",
        }
        env_var = env_key_mapping.get(provider.lower(), f"{provider.upper()}_API_KEY")
        old_key = os.environ.get(env_var)
        os.environ[env_var] = api_key

        try:
            # 创建测试图片（1x1 白色像素）
            test_image = Image.new("RGB", (1, 1), color="white")
            img_buffer = io.BytesIO()
            test_image.save(img_buffer, format="JPEG")
            img_bytes = img_buffer.getvalue()
            base64_image = base64.b64encode(img_bytes).decode("utf-8")

            # 构建测试请求
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请直接回复“连接成功”四个字"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        },
                    ],
                }
            ]

            # 准备参数
            completion_kwargs = {
                "model": model_name,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 20,
            }

            if base_url:
                completion_kwargs["api_base"] = base_url

            # 调用 LiteLLM（同步调用用于测试）
            response = litellm.completion(**completion_kwargs)

            if response and response.choices and len(response.choices) > 0:
                return True, f"视觉模型连接成功 ({model_name})"
            else:
                return False, "视觉模型返回空响应，请检查模型名称和接口地址"

        finally:
            # 恢复原始环境变量
            if old_key is not None:
                os.environ[env_var] = old_key
            else:
                os.environ.pop(env_var, None)

    except Exception as e:
        error_msg = str(e)
        logger.error(f"LiteLLM 视觉模型测试失败: {error_msg}")

        if "authentication" in error_msg.lower() or "api_key" in error_msg.lower():
            return False, "认证失败，请检查 API Key / 接口地址是否正确"
        elif "not found" in error_msg.lower() or "404" in error_msg:
            return False, "模型不存在，请检查模型名称是否正确"
        elif "rate limit" in error_msg.lower():
            return False, "超出速率限制，请稍后重试"
        else:
            return False, f"连接失败: {error_msg}"


def test_litellm_text_model(api_key: str, base_url: str, model_name: str, tr) -> tuple[bool, str]:
    """测试 LiteLLM 文本模型连接（支持 DeepSeek / 豆包 / 通义 / 文心一言 等国内外模型）"""
    try:
        import litellm
        import os

        logger.debug(
            f"LiteLLM 文本模型连通性测试: model={model_name}, api_key={api_key[:10]}..., base_url={base_url}"
        )

        # 提取 provider 名称
        provider = model_name.split("/")[0] if "/" in model_name else "unknown"

        # 设置 API key 到环境变量
        env_key_mapping = {
            # 国外
            "gemini": "GEMINI_API_KEY",
            "google": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "qwen": "QWEN_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
            "siliconflow": "SILICONFLOW_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            # 国内常见
            "doubao": "DOUBAO_API_KEY",
            "volcengine": "VOLCENGINE_API_KEY",
            "ernie": "ERNIE_API_KEY",
            "yiyan": "YIYAN_API_KEY",
            "baidu": "BAIDU_API_KEY",
            "qianfan": "QIANFAN_API_KEY",
        }
        env_var = env_key_mapping.get(provider.lower(), f"{provider.upper()}_API_KEY")
        old_key = os.environ.get(env_var)
        os.environ[env_var] = api_key

        try:
            # 构建测试请求
            messages = [{"role": "user", "content": "请直接回复“连接成功”四个字"}]

            completion_kwargs = {
                "model": model_name,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 20,
            }

            if base_url:
                completion_kwargs["api_base"] = base_url

            response = litellm.completion(**completion_kwargs)

            if response and response.choices and len(response.choices) > 0:
                return True, f"文本模型连接成功 ({model_name})"
            else:
                return False, "文本模型返回空响应，请检查模型名称和接口地址"

        finally:
            if old_key is not None:
                os.environ[env_var] = old_key
            else:
                os.environ.pop(env_var, None)

    except Exception as e:
        error_msg = str(e)
        logger.error(f"LiteLLM 文本模型测试失败: {error_msg}")

        if "authentication" in error_msg.lower() or "api_key" in error_msg.lower():
            return False, "认证失败，请检查 API Key / 接口地址是否正确"
        elif "not found" in error_msg.lower() or "404" in error_msg:
            return False, "模型不存在，请检查模型名称是否正确"
        elif "rate limit" in error_msg.lower():
            return False, "超出速率限制，请稍后重试"
        else:
            return False, f"连接失败: {error_msg}"


def render_vision_llm_settings(tr):
    """渲染视频分析模型设置（LiteLLM 统一配置）"""
    st.subheader(tr("Vision Model Settings"))

    # 固定使用 LiteLLM 提供商
    config.app["vision_llm_provider"] = "litellm"

    # 获取已保存的 LiteLLM 配置
    vision_model_name = config.app.get(
        "vision_litellm_model_name", "gemini/gemini-2.0-flash-lite"
    )
    vision_api_key = config.app.get("vision_litellm_api_key", "")
    vision_base_url = config.app.get("vision_litellm_base_url", "")

    # 渲染配置输入框
    st_vision_model_name = st.text_input(
        tr("Vision Model Name"),
        value=vision_model_name,
        help=(
            "LiteLLM 模型格式: provider/model\n\n"
            "【常用示例】\n"
            "  • gemini/gemini-2.0-flash-lite\n"
            "  • gemini/gemini-1.5-pro\n"
            "  • openai/gpt-4o, openai/gpt-4o-mini\n"
            "  • qwen/qwen2.5-vl-32b-instruct\n"
            "  • siliconflow/Qwen/Qwen2.5-VL-32B-Instruct\n\n"
            "【国内视觉模型示例（需自己搭好 OpenAI 兼容网关 / LiteLLM 支持）】\n"
            "  • deepseek/deepseek-vl\n"
            "  • doubao/doubao-1.5-vl\n"
            "  • ernie/ernie-vilg-vl\n"
            "  • qianfan/ernie-vl\n\n"
            "支持 100+ providers，详见: https://docs.litellm.ai/docs/providers"
        ),
    )

    st_vision_api_key = st.text_input(
        tr("Vision API Key"),
        value=vision_api_key,
        type="password",
        help=(
            "对应 provider / 网关 的 API 密钥，例如：\n"
            "  • Gemini: Google AI Studio 里生成的 key\n"
            "  • DeepSeek / 通义 / 豆包 / 文心一言 / 千帆 的控制台密钥\n"
            "  • 或你自己搭建的 OpenAI 兼容网关的访问密钥\n"
        ),
    )

    st_vision_base_url = st.text_input(
        tr("Vision Base URL"),
        value=vision_base_url,
        help=(
            "自定义 API 端点（可选）\n\n"
            "留空：使用 LiteLLM 默认端点（官方接口）。\n"
            "如果是国内网关 / 自建代理，请填其 OpenAI 兼容地址，例如：\n"
            "  • https://api.deepseek.com/v1\n"
            "  • https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "  • https://ark.cn-beijing.volces.com/api/v3/openai\n"
            "  • https://open.bigmodel.cn/api/paas/v4\n"
            "  • https://你的网关域名/v1\n"
        ),
    )

    # 测试连接按钮
    if st.button(tr("Test Connection"), key="test_vision_connection"):
        test_errors = []
        if not st_vision_api_key:
            test_errors.append("请先输入 API 密钥")
        if not st_vision_model_name:
            test_errors.append("请先输入模型名称")

        if test_errors:
            for error in test_errors:
                st.error(error)
        else:
            with st.spinner(tr("Testing connection...")):
                try:
                    success, message = test_litellm_vision_model(
                        api_key=st_vision_api_key,
                        base_url=st_vision_base_url,
                        model_name=st_vision_model_name,
                        tr=tr,
                    )

                    if success:
                        st.success(message)
                    else:
                        st.error(message)
                except Exception as e:
                    st.error(f"测试连接时发生错误: {str(e)}")
                    logger.error(f"LiteLLM 视频分析模型连接测试失败: {str(e)}")

    # 验证和保存配置（实时写入 config + session_state）
    validation_errors = []
    config_changed = False

    # 验证模型名称
    if st_vision_model_name:
        is_valid, error_msg = validate_litellm_model_name(
            st_vision_model_name, "视频分析"
        )
        if is_valid:
            config.app["vision_litellm_model_name"] = st_vision_model_name
            st.session_state["vision_litellm_model_name"] = st_vision_model_name
            config_changed = True
        else:
            validation_errors.append(error_msg)

    # 验证 API 密钥
    if st_vision_api_key:
        is_valid, error_msg = validate_api_key(st_vision_api_key, "视频分析")
        if is_valid:
            config.app["vision_litellm_api_key"] = st_vision_api_key
            st.session_state["vision_litellm_api_key"] = st_vision_api_key
            config_changed = True
        else:
            validation_errors.append(error_msg)

    # 验证 Base URL（可选）
    if st_vision_base_url:
        is_valid, error_msg = validate_base_url(st_vision_base_url, "视频分析")
        if is_valid:
            config.app["vision_litellm_base_url"] = st_vision_base_url
            st.session_state["vision_litellm_base_url"] = st_vision_base_url
            config_changed = True
        else:
            validation_errors.append(error_msg)

    # 显示验证错误
    show_config_validation_errors(validation_errors)

    # 新增：显式保存按钮（写入配置文件）
    if st.button("保存视频分析模型配置", use_container_width=True, key="save_vision_model"):
        if validation_errors:
            st.error("当前配置存在错误，请先修正上面的红色提示")
        else:
            try:
                config.save_config()
                st.success("视频分析模型配置已保存（LiteLLM） ✅")
            except Exception as e:
                st.error(f"保存配置失败: {str(e)}")
                logger.error(f"保存视频分析配置失败: {str(e)}")


def test_text_model_connection(api_key, base_url, model_name, provider, tr):
    """老版本测试文本模型连接（保留以兼容可能的旧代码调用）"""
    import requests

    logger.debug(f"大模型连通性测试: {base_url} 模型: {model_name} apikey: {api_key}")

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # 特殊处理Gemini
        if provider.lower() == "gemini":
            try:
                request_data = {
                    "contents": [{"parts": [{"text": "直接回复我文本'当前网络可用'"}]}]
                }

                api_base_url = base_url
                url = f"{api_base_url}/models/{model_name}:generateContent"

                response = requests.post(
                    url,
                    json=request_data,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    timeout=10,
                )

                if response.status_code == 200:
                    return True, tr("原生Gemini模型连接成功")
                else:
                    return False, f"{tr('原生Gemini模型连接失败')}: HTTP {response.status_code}"
            except Exception as e:
                return False, f"{tr('原生Gemini模型连接失败')}: {str(e)}"

        elif provider.lower() == "gemini(openai)":
            test_url = f"{base_url.rstrip('/')}/chat/completions"
            test_data = {
                "model": model_name,
                "messages": [{"role": "user", "content": "直接回复我文本'当前网络可用'"}],
                "stream": False,
            }

            response = requests.post(test_url, headers=headers, json=test_data, timeout=10)
            if response.status_code == 200:
                return True, tr("OpenAI兼容Gemini代理连接成功")
            else:
                return False, f"{tr('OpenAI兼容Gemini代理连接失败')}: HTTP {response.status_code}"
        else:
            test_url = f"{base_url.rstrip('/')}/chat/completions"

            test_data = {
                "model": model_name,
                "messages": [{"role": "user", "content": "直接回复我文本'当前网络可用'"}],
                "stream": False,
            }

            response = requests.post(
                test_url,
                headers=headers,
                json=test_data,
            )

            if response.status_code == 200:
                return True, tr("Text model is available")
            else:
                return False, f"{tr('Text model is not available')}: HTTP {response.status_code}"

    except Exception as e:
        logger.error(traceback.format_exc())
        return False, f"{tr('Connection failed')}: {str(e)}"


def render_text_llm_settings(tr):
    """渲染文案生成模型设置（LiteLLM 统一配置）"""
    st.subheader(tr("Text Generation Model Settings"))

    # 固定使用 LiteLLM 提供商
    config.app["text_llm_provider"] = "litellm"

    # 获取已保存的 LiteLLM 配置
    text_model_name = config.app.get("text_litellm_model_name", "deepseek/deepseek-chat")
    text_api_key = config.app.get("text_litellm_api_key", "")
    text_base_url = config.app.get("text_litellm_base_url", "")

    # 渲染配置输入框
    st_text_model_name = st.text_input(
        tr("Text Model Name"),
        value=text_model_name,
        help=(
            "LiteLLM 模型格式: provider/model\n\n"
            "【推荐示例】\n"
            "  • deepseek/deepseek-chat\n"
            "  • deepseek/deepseek-reasoner\n"
            "  • gemini/gemini-2.0-flash\n"
            "  • openai/gpt-4o, openai/gpt-4o-mini\n"
            "  • qwen/qwen-plus, qwen/qwen-turbo\n"
            "  • siliconflow/deepseek-ai/DeepSeek-R1\n"
            "  • moonshot/moonshot-v1-8k\n"
            "  • doubao/doubao-1.5-pro\n"
            "  • ernie/ernie-4.0\n"
            "  • qianfan/ernie-speed-8k\n\n"
            "支持 100+ providers，详见: https://docs.litellm.ai/docs/providers"
        ),
    )

    st_text_api_key = st.text_input(
        tr("Text API Key"),
        value=text_api_key,
        type="password",
        help=(
            "对应 provider / 网关 的 API 密钥，例如：\n"
            "  • DeepSeek: https://platform.deepseek.com/api_keys\n"
            "  • Gemini: Google AI Studio\n"
            "  • OpenAI: https://platform.openai.com/api-keys\n"
            "  • 通义千问 / 豆包 / 文心一言 / 千帆控制台提供的 Key\n"
            "  • 或你自建 OpenAI 兼容网关的访问密钥\n"
        ),
    )

    st_text_base_url = st.text_input(
        tr("Text Base URL"),
        value=text_base_url,
        help=(
            "自定义 API 端点（可选），用于国内外网关 / 代理 / 自建模型。\n\n"
            "示例：\n"
            "  • https://api.deepseek.com/v1\n"
            "  • https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "  • https://ark.cn-beijing.volces.com/api/v3/openai\n"
            "  • https://open.bigmodel.cn/api/paas/v4\n"
            "  • https://你的网关域名/v1\n"
        ),
    )

    # 测试连接按钮
    if st.button(tr("Test Connection"), key="test_text_connection"):
        test_errors = []
        if not st_text_api_key:
            test_errors.append("请先输入 API 密钥")
        if not st_text_model_name:
            test_errors.append("请先输入模型名称")

        if test_errors:
            for error in test_errors:
                st.error(error)
        else:
            with st.spinner(tr("Testing connection...")):
                try:
                    success, message = test_litellm_text_model(
                        api_key=st_text_api_key,
                        base_url=st_text_base_url,
                        model_name=st_text_model_name,
                        tr=tr,
                    )

                    if success:
                        st.success(message)
                    else:
                        st.error(message)
                except Exception as e:
                    st.error(f"测试连接时发生错误: {str(e)}")
                    logger.error(f"LiteLLM 文案生成模型连接测试失败: {str(e)}")

    # 验证和保存配置（实时写入 config + session_state）
    text_validation_errors = []
    text_config_changed = False

    # 验证模型名称
    if st_text_model_name:
        is_valid, error_msg = validate_litellm_model_name(
            st_text_model_name, "文案生成"
        )
        if is_valid:
            config.app["text_litellm_model_name"] = st_text_model_name
            st.session_state["text_litellm_model_name"] = st_text_model_name
            text_config_changed = True
        else:
            text_validation_errors.append(error_msg)

    # 验证 API 密钥
    if st_text_api_key:
        is_valid, error_msg = validate_api_key(st_text_api_key, "文案生成")
        if is_valid:
            config.app["text_litellm_api_key"] = st_text_api_key
            st.session_state["text_litellm_api_key"] = st_text_api_key
            text_config_changed = True
        else:
            text_validation_errors.append(error_msg)

    # 验证 Base URL（可选）
    if st_text_base_url:
        is_valid, error_msg = validate_base_url(st_text_base_url, "文案生成")
        if is_valid:
            config.app["text_litellm_base_url"] = st_text_base_url
            st.session_state["text_litellm_base_url"] = st_text_base_url
            text_config_changed = True
        else:
            text_validation_errors.append(error_msg)

    # 显示验证错误
    show_config_validation_errors(text_validation_errors)

    # 新增：显式保存按钮（写入配置文件）
    if st.button("保存文案生成模型配置", use_container_width=True, key="save_text_model"):
        if text_validation_errors:
            st.error("当前配置存在错误，请先修正上面的红色提示")
        else:
            try:
                config.save_config()
                st.success("文案生成模型配置已保存（LiteLLM） ✅")
            except Exception as e:
                st.error(f"保存配置失败: {str(e)}")
                logger.error(f"保存文案生成配置失败: {str(e)}")
