import os
import json
import traceback
from loguru import logger
from app.config import config    # 新增：读取解说目标语言
# import tiktoken
from typing import List, Dict
from datetime import datetime
from openai import OpenAI
import requests
import time



class BaseGenerator:
    def __init__(self, model_name: str, api_key: str, prompt: str):
        self.model_name = model_name
        self.api_key = api_key
        self.base_prompt = prompt
        self.conversation_history = []
        self.chunk_overlap = 50
        self.last_chunk_ending = ""
        self.default_params = {
            "temperature": 0.7,
            "max_tokens": 500,
            "top_p": 0.9,
            "frequency_penalty": 0.3,
            "presence_penalty": 0.5
        }

    def _try_generate(self, messages: list, params: dict = None) -> str:
        max_attempts = 3
        tolerance = 5
        
        for attempt in range(max_attempts):
            try:
                response = self._generate(messages, params or self.default_params)
                return self._process_response(response)
            except Exception as e:
                if attempt == max_attempts - 1:
                    raise
                logger.warning(f"Generation attempt {attempt + 1} failed: {str(e)}")
                continue
        return ""

    def _generate(self, messages: list, params: dict) -> any:
        raise NotImplementedError
        
    def _process_response(self, response: any) -> str:
        return response

    def generate_script(self, scene_description: str, word_count: int) -> str:
        """生成脚本的通用方法"""
        prompt = f"""{self.base_prompt}

上一段文案的结尾：{self.last_chunk_ending if self.last_chunk_ending else "这是第一段，无需考虑上文"}

当前画面描述：{scene_description}

请确保新生成的文案与上文自然衔接，保持叙事的连贯性和趣味性。
不要出现除了文案以外的其他任何内容；
严格字数要求：{word_count}字，允许误差±5字。"""

        messages = [
            {"role": "system", "content": self.base_prompt},
            {"role": "user", "content": prompt}
        ]

        try:
            generated_script = self._try_generate(messages, self.default_params)
            
            # 更新上下文
            if generated_script:
                self.last_chunk_ending = generated_script[-self.chunk_overlap:] if len(
                    generated_script) > self.chunk_overlap else generated_script
                
            return generated_script
            
        except Exception as e:
            logger.error(f"Script generation failed: {str(e)}")
            raise


class OpenAIGenerator(BaseGenerator):
    """OpenAI API 生成器实现"""
    def __init__(self, model_name: str, api_key: str, prompt: str, base_url: str):
        super().__init__(model_name, api_key, prompt)
        base_url = base_url or f"https://api.openai.com/v1"
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.max_tokens = 5000
        
        # OpenAI特定参数
        self.default_params = {
            **self.default_params,
            "stream": False,
            "user": "script_generator"
        }
        
        # # 初始化token计数器
        # try:
        #     self.encoding = tiktoken.encoding_for_model(self.model_name)
        # except KeyError:
        #     logger.warning(f"未找到模型 {self.model_name} 的专用编码器，使用默认编码器")
        #     self.encoding = tiktoken.get_encoding("cl100k_base")

    def _generate(self, messages: list, params: dict) -> any:
        """实现OpenAI特定的生成逻辑"""
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                **params
            )
            return response
        except Exception as e:
            logger.error(f"OpenAI generation error: {str(e)}")
            raise

    def _process_response(self, response: any) -> str:
        """处理OpenAI的响应"""
        if not response or not response.choices:
            raise ValueError("Invalid response from OpenAI API")
        return response.choices[0].message.content.strip()

    def _count_tokens(self, messages: list) -> int:
        """计算token数量"""
        num_tokens = 0
        for message in messages:
            num_tokens += 3
            for key, value in message.items():
                num_tokens += len(self.encoding.encode(str(value)))
                if key == "role":
                    num_tokens += 1
        num_tokens += 3
        return num_tokens


class GeminiGenerator(BaseGenerator):
    """原生Gemini API 生成器实现"""
    def __init__(self, model_name: str, api_key: str, prompt: str, base_url: str = None):
        super().__init__(model_name, api_key, prompt)

        self.base_url = base_url or "https://generativelanguage.googleapis.com/v1beta"
        self.client = None

        # 原生Gemini API参数
        self.default_params = {
            "temperature": self.default_params["temperature"],
            "topP": self.default_params["top_p"],
            "topK": 40,
            "maxOutputTokens": 4000,
            "candidateCount": 1,
            "stopSequences": []
        }


class GeminiOpenAIGenerator(BaseGenerator):
    """OpenAI兼容的Gemini代理生成器实现"""
    def __init__(self, model_name: str, api_key: str, prompt: str, base_url: str = None):
        super().__init__(model_name, api_key, prompt)

        if not base_url:
            raise ValueError("OpenAI兼容的Gemini代理必须提供base_url")

        self.base_url = base_url.rstrip('/')

        # 使用OpenAI兼容接口
        from openai import OpenAI
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )

        # OpenAI兼容接口参数
        self.default_params = {
            "temperature": self.default_params["temperature"],
            "max_tokens": 4000,
            "stream": False
        }

    def _generate(self, messages: list, params: dict) -> any:
        """实现OpenAI兼容Gemini代理的生成逻辑"""
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                **params
            )
            return response
        except Exception as e:
            logger.error(f"OpenAI兼容Gemini代理生成错误: {str(e)}")
            raise

    def _process_response(self, response: any) -> str:
        """处理OpenAI兼容接口的响应"""
        if not response or not response.choices:
            raise ValueError("OpenAI兼容Gemini代理返回无效响应")
        return response.choices[0].message.content.strip()

    def _generate(self, messages: list, params: dict) -> any:
        """实现原生Gemini API的生成逻辑"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 转换消息格式为Gemini格式
                prompt = "\n".join([m["content"] for m in messages])

                # 构建请求数据
                request_data = {
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }],
                    "generationConfig": params,
                    "safetySettings": [
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        }
                    ]
                }

                # 构建请求URL
                url = f"{self.base_url}/models/{self.model_name}:generateContent"

                # 发送请求
                response = requests.post(
                    url,
                    json=request_data,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self.api_key
                    },
                    timeout=120
                )

                if response.status_code == 429:
                    # 处理限流
                    wait_time = 65 if attempt == 0 else 30
                    logger.warning(f"原生Gemini API 触发限流，等待{wait_time}秒后重试...")
                    time.sleep(wait_time)
                    continue

                if response.status_code == 400:
                    raise Exception(f"请求参数错误: {response.text}")
                elif response.status_code == 403:
                    raise Exception(f"API密钥无效或权限不足: {response.text}")
                elif response.status_code != 200:
                    raise Exception(f"原生Gemini API请求失败: {response.status_code} - {response.text}")

                response_data = response.json()

                # 检查响应格式
                if "candidates" not in response_data or not response_data["candidates"]:
                    if attempt < max_retries - 1:
                        logger.warning("原生Gemini API 返回无效响应，等待30秒后重试...")
                        time.sleep(30)
                        continue
                    else:
                        raise Exception("原生Gemini API返回无效响应，可能触发了安全过滤")

                candidate = response_data["candidates"][0]

                # 检查是否被安全过滤阻止
                if "finishReason" in candidate and candidate["finishReason"] == "SAFETY":
                    raise Exception("内容被Gemini安全过滤器阻止")

                # 创建兼容的响应对象
                class CompatibleResponse:
                    def __init__(self, data):
                        self.data = data
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            self.text = ""
                            for part in candidate["content"]["parts"]:
                                if "text" in part:
                                    self.text += part["text"]
                        else:
                            self.text = ""

                return CompatibleResponse(response_data)

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    logger.warning(f"网络请求失败，等待30秒后重试: {str(e)}")
                    time.sleep(30)
                    continue
                else:
                    logger.error(f"原生Gemini API请求失败: {str(e)}")
                    raise
            except Exception as e:
                if attempt < max_retries - 1 and "429" in str(e):
                    logger.warning("原生Gemini API 触发限流，等待65秒后重试...")
                    time.sleep(65)
                    continue
                else:
                    logger.error(f"原生Gemini 生成文案错误: {str(e)}")
                    raise

    def _process_response(self, response: any) -> str:
        """处理原生Gemini API的响应"""
        if not response or not response.text:
            raise ValueError("原生Gemini API返回无效响应")
        return response.text.strip()


class QwenGenerator(BaseGenerator):
    """阿里云千问 API 生成器实现"""
    def __init__(self, model_name: str, api_key: str, prompt: str, base_url: str):
        super().__init__(model_name, api_key, prompt)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        
        # Qwen特定参数
        self.default_params = {
            **self.default_params,
            "stream": False,
            "user": "script_generator"
        }

    def _generate(self, messages: list, params: dict) -> any:
        """实现千问特定的生成逻辑"""
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                **params
            )
            return response
        except Exception as e:
            logger.error(f"Qwen generation error: {str(e)}")
            raise

    def _process_response(self, response: any) -> str:
        """处理千问的响应"""
        if not response or not response.choices:
            raise ValueError("Invalid response from Qwen API")
        return response.choices[0].message.content.strip()


class MoonshotGenerator(BaseGenerator):
    """Moonshot API 生成器实现"""
    def __init__(self, model_name: str, api_key: str, prompt: str, base_url: str):
        super().__init__(model_name, api_key, prompt)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or "https://api.moonshot.cn/v1"
        )
        
        # Moonshot特定参数
        self.default_params = {
            **self.default_params,
            "stream": False,
            "stop": None,
            "user": "script_generator",
            "tools": None
        }

    def _generate(self, messages: list, params: dict) -> any:
        """实现Moonshot特定的生成逻辑，包含429误重试机制"""
        while True:
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    **params
                )
                return response
            except Exception as e:
                error_str = str(e)
                if "Error code: 429" in error_str:
                    logger.warning("Moonshot API 触发限流，等待65秒后重试...")
                    time.sleep(65)  # 等待65秒后重试
                    continue
                else:
                    logger.error(f"Moonshot generation error: {error_str}")
                    raise

    def _process_response(self, response: any) -> str:
        """处理Moonshot的响应"""
        if not response or not response.choices:
            raise ValueError("Invalid response from Moonshot API")
        return response.choices[0].message.content.strip()


class DeepSeekGenerator(BaseGenerator):
    """DeepSeek API 生成器实现"""
    def __init__(self, model_name: str, api_key: str, prompt: str, base_url: str):
        super().__init__(model_name, api_key, prompt)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or "https://api.deepseek.com"
        )
        
        # DeepSeek特定参数
        self.default_params = {
            **self.default_params,
            "stream": False,
            "user": "script_generator"
        }

    def _generate(self, messages: list, params: dict) -> any:
        """实现DeepSeek特定的生成逻辑"""
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,  # deepseek-chat 或 deepseek-coder
                messages=messages,
                **params
            )
            return response
        except Exception as e:
            logger.error(f"DeepSeek generation error: {str(e)}")
            raise

    def _process_response(self, response: any) -> str:
        """处理DeepSeek的响应"""
        if not response or not response.choices:
            raise ValueError("Invalid response from DeepSeek API")
        return response.choices[0].message.content.strip()


class ScriptProcessor:
    def __init__(
        self,
        model_name: str,
        api_key: str = None,
        base_url: str = None,
        prompt: str = None,
        video_theme: str = ""
    ):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.video_theme = video_theme
        # 如果外面有自定义 prompt（比如你自己配了风格提示词），优先用外面的
        self.prompt = prompt or self._get_default_prompt()

        logger.info(f"文本 LLM 提供商: {model_name}")
        if 'gemini' in model_name.lower():
            self.generator = GeminiGenerator(model_name, self.api_key, self.prompt, self.base_url)
        elif 'qwen' in model_name.lower():
            self.generator = QwenGenerator(model_name, self.api_key, self.prompt, self.base_url)
        elif 'moonshot' in model_name.lower():
            self.generator = MoonshotGenerator(model_name, self.api_key, self.prompt, self.base_url)
        elif 'deepseek' in model_name.lower():
            self.generator = DeepSeekGenerator(model_name, self.api_key, self.prompt, self.base_url)
        else:
            self.generator = OpenAIGenerator(model_name, self.api_key, self.prompt, self.base_url)

    def _get_default_prompt(self) -> str:
        """
        默认解说文案 Prompt（工业级版本）：
        - 会根据 config.app['narration_language'] 决定用什么语言写
        - 会读取 config.app['narration_style_text'] 作为风格补充
        - 输出为【纯解说文本】，不输出 JSON / 标题 / 标签
        """
        # 1. 目标语言
        try:
            lang_code = config.app.get("narration_language", "zh")
        except Exception:
            lang_code = "zh"

        lang_instructions = {
            "zh": "请用自然、口语化的【简体中文】写解说文案。",
            "en": "Please write the narration in natural, fluent English.",
            "fr": "Veuillez écrire la narration en français naturel et courant.",
            "de": "Bitte schreibe den Kommentar in natürlichem, flüssigem Deutsch.",
            "it": "Scrivi la narrazione in un italiano naturale e scorrevole.",
            "ja": "ナレーションは自然で話し言葉に近い日本語で書いてください。",
            "ko": "해설 대사는 자연스럽고 구어체에 가까운 한국어로 작성해 주세요.",
            "ms": "Sila tulis narasi dalam bahasa Melayu yang semula jadi dan lancar.",
            "id": "Tulis narasi dalam bahasa Indonesia yang alami dan mengalir.",
            "vi": "Hãy viết lời thuyết minh bằng tiếng Việt tự nhiên và dễ nghe.",
        }
        lang_tip = lang_instructions.get(lang_code, lang_instructions["zh"])

        # 2. 解说风格说明（前端设置的预设 + 自定义）
        try:
            style_text = config.app.get("narration_style_text", "").strip()
        except Exception:
            style_text = ""

        style_block = f"\n【风格要求】\n{style_text}\n" if style_text else ""

        # 3. 工业级叙事导演规则（改写成适配“纯文本解说”的版本）
        return f"""
你是一位拥有 30 年经验的顶尖电影导演和金牌剪辑师，对镜头语言、观众心理和叙事节奏有近乎偏执的追求。
你现在的唯一任务，是为一段主题为《{self.video_theme}》的影片，创作可以直接用于中短视频平台的【黄金解说文案】。

{lang_tip}{style_block}

【整体叙事思路】
1. 你要把整部影片当作一个完整故事来处理，在解说时遵循经典的“钩子→建构→转折→发展→高潮→余韵”叙事弧线：
   - 钩子：在开头几句里抛出最有冲击力的矛盾、悬念或情绪点，让观众立刻被吸引。
   - 建构：简明清晰地交代人物关系、背景设定和当前局势，为后续冲突做铺垫。
   - 转折：点出出乎意料的变化或反常行为，制造情绪波动。
   - 发展：通过连续的“爽点 / 痒点 / 痛点”，让情绪和紧张感逐步升级。
   - 高潮：把最关键的冲突、真相或情绪爆发留在最打动人的一刻。
   - 余韵：最后用一句点题金句、反思、或留白式问题，给观众留下回味或评论欲望。

2. 在实际生成时，你会按系统提供的【画面描述 + 时间范围】逐段输出解说。
   - 如果这是影片的开头片段，要优先承担“钩子”和“建构”的功能；
   - 中段片段以“发展”和“转折”为主；
   - 靠近尾声的片段，要逐步引导到“高潮”和“余韵”。

【音画同步要求（非常重要）】
1. 你说到的每一个动作、表情、物体或场景元素，都必须能在当前画面中看见，禁止出现与画面严重不符的内容。
2. 如果画面在表现悲伤，你的解说不可以用轻松搞笑的语气硬拉笑点；情绪基调必须和画面一致或合理反差。
3. 避免提前剧透画面中尚未出现的关键情节，除非当前片段本身就是在复盘或总结。

【原声对白的使用原则（配合系统的 OST 设置）】
1. 当画面中有极具张力、无法用旁白替代的关键对白时，你要在解说中“给它让路”，例如：
   - 只简单铺垫情绪和背景，然后留出空间让观众自己听原声；
   - 可以用一句引导语，例如“接着，他说出了这一句，让所有人都沉默了……”。
2. 不要在解说中逐字复述已经非常有表现力的对白，避免画蛇添足。
3. 当系统把某些片段设置为只保留原声时，你的解说文本应该避免和原声内容完全重复。

【技术与节奏要求】
1. 解说必须口语化，像在跟朋友讲电影，而不是写论文：
   - 句子不要太长，多用短句和停顿；
   - 适当使用口头语（但不要太低俗），让人听着顺耳。
2. 每一段解说的字数会由系统根据时长预估并传给你，你要优先保证“不要太长”：
   - 宁可稍微短一点，也不要超出太多，避免口播念不完。
3. 保持信息密度与节奏：重要的信息清晰说完，没必要的形容词少用。

【输出格式要求】
1. 每次只输出【当前片段】的解说正文，不要输出任何标题、分段标号、角色说明或 JSON。
2. 不要输出“旁白：”“解说：”之类的前缀，直接进入内容本身。
3. 不要解释你在做什么，也不要重复系统指令。

接下来系统会按时间顺序，把多个画面描述依次给你。
请你严格遵守以上规则，为每一个片段写出对应的解说内容。
"""


    def calculate_duration_and_word_count(self, time_range: str) -> int:
        """
        根据时间范围估算字数：
        - 按原视频片段时长计算（每 0.4 秒约 1 字）
        """
        try:
            start_str, end_str = time_range.split('-')

            def time_to_seconds(time_str: str) -> float:
                try:
                    time_part, ms_part = time_str.split(',')
                    hours, minutes, seconds = map(int, time_part.split(':'))
                    milliseconds = int(ms_part)
                    total_seconds = (hours * 3600) + (minutes * 60) + seconds + (milliseconds / 1000)
                    return total_seconds
                except ValueError as e:
                    logger.warning(f"时间格式解析错误: {time_str}, error: {e}")
                    return 0.0

            start_seconds = time_to_seconds(start_str)
            end_seconds = time_to_seconds(end_str)
            duration = max(0.0, end_seconds - start_seconds)

            word_count = int(duration / 0.4)
            word_count = max(10, min(word_count, 500))

            logger.debug(f"时间范围 {time_range} 的持续时间为 {duration:.3f}秒, 估算字数: {word_count}")
            return word_count

        except Exception:
            logger.warning(f"字数计算错误: {traceback.format_exc()}")
            return 100

    def process_frames(self, frame_content_list: List[Dict]) -> List[Dict]:
        """
        遍历每一帧内容：
        1. 根据时间范围估算字数
        2. 调用 LLM 生成解说
        3. 默认先给 OST=2，后面在 _save_results 里再按占比微调
        """
        for frame_content in frame_content_list:
            word_count = self.calculate_duration_and_word_count(frame_content["timestamp"])
            script = self.generator.generate_script(frame_content["picture"], word_count)
            frame_content["narration"] = script
            frame_content["OST"] = 2  # 先默认原声+解说混合
            logger.info(f"时间范围: {frame_content['timestamp']}, 建议字数: {word_count}")
            logger.info(script)

        self._save_results(frame_content_list)
        return frame_content_list

    def _save_results(self, frame_content_list: List[Dict]):
        """
        保存结果，并根据配置：
        - 控制成品总时长（target_duration_minutes）
        - 按原声占比（original_audio_ratio）分配 OST=2 / OST=0
        - 生成新的时间轴 new_timestamp
        """
        try:
            # 读取总时长 & 原声比例配置
            try:
                target_minutes = float(config.app.get("target_duration_minutes", 0.0) or 0.0)
            except Exception:
                target_minutes = 0.0

            max_duration = target_minutes * 60.0 if target_minutes > 0 else None  # 秒

            try:
                original_ratio = float(config.app.get("original_audio_ratio", 0.3))
            except Exception:
                original_ratio = 0.3
            original_ratio = min(max(original_ratio, 0.0), 1.0)

            def time_to_seconds(time_str: str) -> float:
                """将时间字符串转换为秒数（包含毫秒）"""
                try:
                    if ',' in time_str:
                        time_part, ms_part = time_str.split(',')
                        ms = float(ms_part) / 1000
                    else:
                        time_part = time_str
                        ms = 0.0

                    parts = time_part.split(':')
                    if len(parts) == 3:
                        h, m, s = map(float, parts)
                        seconds = h * 3600 + m * 60 + s
                    elif len(parts) == 2:
                        m, s = map(float, parts)
                        seconds = m * 60 + s
                    else:
                        seconds = float(parts[0])

                    return seconds + ms
                except Exception as e:
                    logger.error(f"时间格式转换错误 {time_str}: {str(e)}")
                    return 0.0

            def format_timestamp(seconds: float) -> str:
                """将秒数转换为 HH:MM:SS,mmm 格式"""
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                seconds_remainder = seconds % 60
                whole_seconds = int(seconds_remainder)
                milliseconds = int((seconds_remainder - whole_seconds) * 1000)
                return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"

            # 第一遍：根据 target_duration_minutes 过滤 / 截断片段
            effective_frames = []
            current_time = 0.0

            for frame in frame_content_list:
                start_str, end_str = frame['timestamp'].split('-')
                start_seconds = time_to_seconds(start_str)
                end_seconds = time_to_seconds(end_str)
                duration = max(0.0, end_seconds - start_seconds)

                if max_duration is not None:
                    if current_time >= max_duration:
                        break  # 已经达到总时长上限，后面片段直接丢弃
                    remaining = max_duration - current_time
                    if duration > remaining:
                        duration = remaining  # 截断最后一个片段
                        if duration <= 0:
                            break

                # 记录“生效时长”
                effective_frames.append({
                    "frame": frame,
                    "duration": duration
                })
                current_time += duration

            total_effective_duration = current_time

            # 第二步：根据 original_audio_ratio 分配 OST
            target_original_time = total_effective_duration * original_ratio
            used_original_time = 0.0

            for item in effective_frames:
                frame = item["frame"]
                duration = item["duration"]

                # 如果还没达到目标原声时间，就给 OST=2（原声+解说）
                if used_original_time < target_original_time and duration > 0:
                    frame["OST"] = 2
                    used_original_time += duration
                else:
                    # 超出后，统一改为 OST=0（只解说 / 只配乐）
                    frame["OST"] = frame.get("OST", 2)
                    if frame["OST"] == 2:
                        frame["OST"] = 0

            # 第三步：根据“生效时长”生成新的时间轴 new_timestamp
            new_time = 0.0
            for item in effective_frames:
                frame = item["frame"]
                duration = item["duration"]

                new_start = format_timestamp(new_time)
                new_end = format_timestamp(new_time + duration)
                frame["new_timestamp"] = f"{new_start}-{new_end}"

                new_time += duration

            # 保存 JSON 结果
            file_name = f"storage/json/step2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            os.makedirs(os.path.dirname(file_name), exist_ok=True)

            with open(file_name, 'w', encoding='utf-8') as file:
                json.dump([item["frame"] for item in effective_frames], file, ensure_ascii=False, indent=4)

            logger.info(f"保存脚本成功，总时长(限制后): {format_timestamp(total_effective_duration)}, 原声占比目标: {original_ratio:.2f}, 实际原声时长: {used_original_time:.3f}s")

        except Exception as e:
            logger.error(f"保存结果时发生错误: {str(e)}\n{traceback.format_exc()}")
            raise
