from app.config import config


def _get_lang_name(lang_code: str) -> str:
    """把语言代码转换成更友好的名字，用于提示词里展示"""
    mapping = {
        "zh": "简体中文",
        "en": "English（英文）",
        "ja": "日本語（日语）",
        "ko": "한국어（韩语）",
        "fr": "Français（法语）",
        "de": "Deutsch（德语）",
        "it": "Italiano（意大利语）",
        "ms": "Bahasa Melayu（马来语）",
        "id": "Bahasa Indonesia（印尼语）",
        "vi": "Tiếng Việt（越南语）",
    }
    return mapping.get(lang_code, lang_code)


def _get_lang_default_style(lang_code: str) -> str:
    """
    根据解说语言，给一个“默认的本地化表达习惯”说明。
    如果用户没写自定义风格，就用这里的说明；
    如果用户写了，则作为参考 + 用户提示词优先。
    """
    defaults = {
        "zh": (
            "- 使用适合中文短视频平台的口语化表达，节奏相对紧凑，\n"
            "  可以适度吐槽、带一点网络梗，但不要低俗，不要恶意攻击演员或群体。\n"
            "- 用词自然，不要像翻译腔，更像一个资深影评/解说博主在和观众聊天。"
        ),
        "en": (
            "- 使用地道的英文表达，风格参考 YouTube 电影解说频道，\n"
            "  语气自然、轻松，允许适度幽默和个人观点。\n"
            "- 尽量避免直译式的中式英文，更像 native speaker 的口吻。"
        ),
        "ja": (
            "- 使用自然的日常会话日语，而不是生硬的书面语或夸张的动漫腔。\n"
            "- 语气可以礼貌但不僵硬，像日本影评人或电影系up主在讲电影。"
        ),
        "ko": (
            "- 使用符合韩国 YouTube 电影解说风格的表达，\n"
            "  适度吐槽、偶尔感叹，但整体语气友好自然。\n"
            "- 不要使用过于教科书式的韩语，更像真实创作者在说话。"
        ),
        "fr": (
            "- 使用自然的法语表达，可以略带文艺感，但不要太晦涩。\n"
            "- 更像一位法国影评人在做口头解说，而不是正式书面评论。"
        ),
        "de": (
            "- 使用自然的德语，表达清晰、逻辑性强，可以适度幽默。\n"
            "- 更偏口语，不要过于生硬的书面语。"
        ),
        "it": (
            "- 使用富有情绪和节奏感的意大利语，允许适度夸张的感叹和修辞。\n"
            "- 像一个热情的意大利电影爱好者在讲自己喜欢/吐槽的片子。"
        ),
        "ms": (
            "- 使用自然的马来语口语表达，语气友好、平易近人。\n"
            "- 更像本地创作者的讲述方式，而不是教科书式表达。"
        ),
        "id": (
            "- 使用自然的印尼语表达，可以轻松、幽默一点。\n"
            "- 像一个熟悉电影的印尼YouTuber在做解说。"
        ),
        "vi": (
            "- 使用自然、口语化的越南语，节奏不要太快，保证听众能跟上。\n"
            "- 可以适度加入感叹和观点，但整体保持清晰易懂。"
        ),
    }

    generic = (
        "- 使用该语言下最自然的口语表达方式，像本地创作者在做电影解说一样。\n"
        "- 避免生硬的翻译腔，保证语气自然、有画面感。"
    )

    return defaults.get(lang_code, generic)


def build_director_prompt(channel_name: str | None = None) -> str:
    """
    构建用于“解说脚本生成”的 system prompt。
    会自动读取前端 UI 中配置的：
    - 解说语言
    - 成品目标时长
    - 原声对白占比
    - 解说风格提示词（用户自定义，优先级最高）
    - 解说结构模式（黄金/Vlog/纪录片/悬疑/自由）
    """

    app_cfg = config.app

    # 频道名
    channel_name = channel_name or app_cfg.get("channel_name", "NarratoAI 电影解说频道")

    # 解说语言
    lang_code = app_cfg.get("narration_language", "zh")
    lang_name = _get_lang_name(lang_code)

    # 成品时长（分钟）
    try:
        duration_min = float(app_cfg.get("target_duration_minutes", 0.0) or 0.0)
    except Exception:
        duration_min = 0.0

    # 原声对白占比（优先用百分比字段，没有就退回到 ratio）
    try:
        original_ratio_percent = int(app_cfg.get("original_audio_ratio_percent", 30))
    except Exception:
        # 如果只存了 ratio（0~1），做个转换
        try:
            ratio = float(app_cfg.get("original_audio_ratio", 0.3))
            original_ratio_percent = int(ratio * 100)
        except Exception:
            original_ratio_percent = 30

    # 用户自定义风格提示词
    style_text = (app_cfg.get("narration_style_text") or "").strip()

    # 结构模式
    structure_mode = app_cfg.get("narration_structure_mode", "golden")
    valid_modes = {"golden", "vlog", "documentary", "suspense", "free"}
    if structure_mode not in valid_modes:
        structure_mode = "golden"

    # 粗略估算一个“字数上限”提示（软约束）
    if duration_min > 0:
        total_seconds = duration_min * 60
        # 简单按中文 4字/秒、其它语种 ~5字符/秒估算
        if lang_code == "zh":
            chars_per_second = 4.0
        else:
            chars_per_second = 5.0
        max_chars = int(total_seconds * chars_per_second)
        length_instruction = (
            f"成片目标时长约为 {duration_min:.1f} 分钟，请将整篇旁白控制在不超过约 {max_chars} 个字符的量级。"
        )
    else:
        length_instruction = (
            "成片目标时长未做强约束，可以根据剧情复杂度自由控制篇幅，但仍需保持紧凑。"
        )

    # 不同结构模式下的结构要求
    if structure_mode == "golden":
        structure_block = """【叙事结构要求（黄金结构，适用于大部分短视频）】
- 使用“钩子 → 建构 → 转折 → 发展 → 高潮 → 余韵”的故事弧线。
- 开头 1~3 句必须是强有冲击力的【钩子】，可以是悬念、反常识或极端情绪场面。
- 中段按照剧情发展递进，每一小段都要有“爽点/笑点/痛点”。
- 高潮部分情绪拉到最高，适当减慢语速以增强张力。
- 结尾用一句【金句】或开放式问题收束，引导观众评论和关注。
"""
    elif structure_mode == "vlog":
        structure_block = """【叙事结构要求（Vlog 口播风格）】
- 不要使用固定的“钩子/高潮/余韵”结构。
- 模拟一个熟悉电影的 UP 主，像对朋友聊天一样讲剧情和感受。
- 可以从自己最想吐槽或最喜欢的点切入，随后自由穿插剧情。
- 允许适当跑题，但整体仍要让第一次看电影的人听得懂发生了什么。
"""
    elif structure_mode == "documentary":
        structure_block = """【叙事结构要求（纪录片风格）】
- 不要使用短视频那种“反转钩子”套路。
- 按“背景 → 事件经过 → 结果/影响”的顺序冷静叙述。
- 语言保持克制、理性，可以有情感但不夸张。
- 适合历史片、真实事件改编、社会题材影片。 
"""
    elif structure_mode == "suspense":
        structure_block = """【叙事结构要求（悬疑/反转风格）】
- 前半段刻意隐藏真相，只给出正常/表层的信息。
- 中段逐步抖出更多线索，让观众感觉“哪里不对劲”。
- 最后用一个清晰的大反转，把真相一次性讲明白。
- 允许适当误导观众，但结局必须讲清楚逻辑，不能糊弄。
"""
    else:  # free
        structure_block = """【叙事结构要求（完全自由）】
- 不使用任何固定叙事模板，你可以根据影片特点自由安排讲述顺序。
- 唯一要求是：让第一次没看过原片的观众，也能在听完后明白剧情和人物关系。
- 可以只追求情绪流动，也可以按主题分块讲述，完全由你决定。
"""

    # 语言与受众要求
    language_block = f"""【语言与受众】
- 你输出的解说脚本必须使用：{lang_name}（语言代码：{lang_code}）。
- 不得输出与该语言无关的大段其它语言内容，如需引用外语台词，请在括号中少量点缀，并立即翻译或解释。
"""

    # 时长与节奏控制
    length_block = f"""【时长与节奏控制】
- {length_instruction}
- 文案要有明显的段落节奏，可以通过自然换行、停顿点、情绪起伏来控制。
- 严禁为了凑时长而灌水，所有句子都必须要么推进剧情，要么增强人物理解，要么制造情绪价值。
"""

    # 原声对白使用说明
    original_dialogue_block = f"""【原声对白与解说配合】
- 约 {original_ratio_percent}% 的成片时长应该保留原片原声对白或关键音效，以增强沉浸感。
- 请在你输出的结构化脚本中，为需要保留原声的片段设置例如 include_original_dialogue = true，
  并让该处的解说文本简洁，或使用占位符标记（例如 "[use_original_sound]"），具体字段名以调用方要求为准。
- 原声片段应主要放在：情绪爆发、关键台词、反转揭晓等位置，而不是平均分布。
"""

    # 按语言自动生成的“本地化表达风格”说明
    lang_style_default = _get_lang_default_style(lang_code)

    # 风格要求块：
    # 1) 如果用户写了风格提示词：语言默认风格作为参考，用户风格为最高优先级
    # 2) 如果用户没写：直接用语言默认风格作为风格说明
    if style_text:
        style_block = f"""【语言文化与默认表达风格参考】
{lang_style_default}

【解说人格与风格要求（用户自定义，优先级最高）】
下面是一段由用户提供的风格设定，请你严格遵守其中的语气、节奏、解说人格与创作偏好：
----------------- 用户风格设定开始 -----------------
{style_text}
----------------- 用户风格设定结束 -----------------
在上述用户风格设定，与本 system prompt 中其他条目冲突时，以用户风格设定为最高优先级。
"""
    else:
        style_block = f"""【解说人格与风格要求】
{lang_style_default}
- 如果后续用户提供更具体的风格提示（例如“毒舌吐槽”“温柔纪录片”“vlog 聊天风格”），
  你需要在保持上述语言习惯的基础上，优先满足用户的风格要求。
"""

    # 总体身份与任务说明
    identity_block = f"""【你的身份与任务】
你是一位拥有 30 年经验的顶尖电影导演兼金牌剪辑师，对镜头语言、观众心理和叙事节奏有极高要求。
现在，你是频道《{channel_name}》的首席内容官，你的唯一任务是：
根据提供的影片素材分析结果和字幕（如果有），生成一份可以直接用于自动化制作的【高质量解说脚本】。
调用方会在 user 消息中说明需要的 JSON/结构化字段格式，你必须在满足格式的前提下，严格遵守本 system prompt 的所有硬性约束。
"""

    full_prompt = (
        identity_block
        + "\n\n"
        + language_block
        + "\n\n"
        + length_block
        + "\n\n"
        + original_dialogue_block
        + "\n\n"
        + structure_block
        + "\n\n"
        + style_block
    )

    return full_prompt
