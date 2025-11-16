# app/services/openai_compat.py

import requests
from typing import List, Dict, Any, Optional


class OpenAICompatClient:
    """
    通用 OpenAI Chat Completions 兼容客户端
    - 支持官方 OpenAI
    - 支持 302.AI（https://api.302.ai）
    - 支持其他 OpenAI 格式的第三方网关

    用法：
        client = OpenAICompatClient(
            base_url="https://api.302.ai",     # 或 https://api.openai.com
            api_key="sk-xxx",
        )
        resp = client.chat(
            model="deepseek/deepseek-chat",
            messages=[{"role": "user", "content": "你好"}],
            temperature=0.7,
        )
        print(resp["choices"][0]["message"]["content"])
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 60):
        if not base_url:
            raise ValueError("base_url 不能为空")

        # 统一规范：以 /v1 结尾（302.ai 要求末尾加 /v1）
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"

        self.base_url = base
        self.api_key = api_key
        self.timeout = timeout

    # -----------------------------
    # Chat Completions
    # -----------------------------

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        **extra_params: Any,
    ) -> Dict[str, Any]:
        """
        调用 /chat/completions

        extra_params 可以传：
        - max_tokens
        - top_p
        - presence_penalty
        - frequency_penalty
        - tools / tool_choice
        - 任何 OpenAI 兼容参数
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        payload.update(extra_params)

        resp = requests.post(
            url, headers=headers, json=payload, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    # -----------------------------
    # 简单封装：文本/图片混合聊天
    # -----------------------------

    def chat_text(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        system_prompt: Optional[str] = None,
        **extra_params: Any,
    ) -> str:
        """
        纯文本聊天封装：返回字符串
        """
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = self.chat(
            model=model,
            messages=messages,
            temperature=temperature,
            **extra_params,
        )
        return data["choices"][0]["message"]["content"]

    def chat_with_image(
        self,
        model: str,
        image_url: str,
        prompt: str = "",
        temperature: float = 0.7,
        system_prompt: Optional[str] = None,
        **extra_params: Any,
    ) -> str:
        """
        图像 + 文本分析（视频分析模型可以用这种方式）
        image_url 可以是：
        - 网络地址
        - data:image/jpeg;base64,xxxx
        """
        messages: List[Dict[str, Any]] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        content: List[Dict[str, Any]] = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url},
            }
        )

        messages.append({"role": "user", "content": content})

        data = self.chat(
            model=model,
            messages=messages,
            temperature=temperature,
            **extra_params,
        )
        return data["choices"][0]["message"]["content"]
