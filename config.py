import os
import socket
import toml
import shutil
from loguru import logger

# 项目根目录
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
config_file = f"{root_dir}/config.toml"
version_file = f"{root_dir}/project_version"


def get_version_from_file():
    """从 project_version 文件中读取版本号"""
    try:
        if os.path.isfile(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        return "0.1.0"  # 默认版本号
    except Exception as e:
        logger.error(f"读取版本号文件失败: {str(e)}")
        return "0.1.0"  # 默认版本号


def load_config():
    """
    加载配置文件：
    - 如果 config.toml 不存在，则从 config.example.toml 拷贝一个
    - 兼容 utf-8-sig 编码
    """
    # fix: IsADirectoryError: [Errno 21] Is a directory: '/NarratoAI/config.toml'
    if os.path.isdir(config_file):
        shutil.rmtree(config_file)

    if not os.path.isfile(config_file):
        example_file = f"{root_dir}/config.example.toml"
        if os.path.isfile(example_file):
            shutil.copyfile(example_file, config_file)
            logger.info("copy config.example.toml to config.toml")

    logger.info(f"load config from file: {config_file}")

    try:
        _config_ = toml.load(config_file)
    except Exception as e:
        logger.warning(f"load config failed: {str(e)}, try to load as utf-8-sig")
        with open(config_file, mode="r", encoding="utf-8-sig") as fp:
            _cfg_content = fp.read()
            _config_ = toml.loads(_cfg_content)
    return _config_


_cfg = load_config()

# ----------------- 各模块配置 -----------------
app = _cfg.get("app", {})
whisper = _cfg.get("whisper", {})
proxy = _cfg.get("proxy", {})
azure = _cfg.get("azure", {})
tencent = _cfg.get("tencent", {})
soulvoice = _cfg.get("soulvoice", {})
ui = _cfg.get("ui", {})
frames = _cfg.get("frames", {})
tts_qwen = _cfg.get("tts_qwen", {})

# ---------- 新增：脚本模式 & ASR 默认配置 ----------

# 解说脚本生成模式：
#   - "subtitle" : 使用字幕/ASR 模式（先提取字幕+时间轴，再写文案）
#   - "vision"   : 使用 AI 看画面模式（Gemini 等视觉模型）
if "script_mode" not in app:
    # 默认使用字幕模式，和你 UI 上“使用字幕生成解说”保持一致
    app["script_mode"] = "subtitle"

# whisper / ASR 模型的默认路径配置（本地 faster-whisper-medium）
# 这样你把整个文件夹拷到另一台电脑，也能直接用，不需要再手动配置路径
if "model_dir" not in whisper or not whisper.get("model_dir"):
    whisper["model_dir"] = os.path.join(root_dir, "models", "medium")

# 是否启用本地 ASR（给后面 UI/任务逻辑用）
if "enabled" not in whisper:
    whisper["enabled"] = True

# ----------------- 公共配置 -----------------
hostname = socket.gethostname()

log_level = _cfg.get("log_level", "DEBUG")
listen_host = _cfg.get("listen_host", "0.0.0.0")
listen_port = _cfg.get("listen_port", 8080)
project_name = _cfg.get("project_name", "NarratoAI")
project_description = _cfg.get(
    "project_description",
    "<a href='https://github.com/linyqh/NarratoAI'>https://github.com/linyqh/NarratoAI</a>",
)
# 从文件读取版本号，而不是从配置文件中获取
project_version = get_version_from_file()
reload_debug = False


def save_config():
    """
    保存当前内存中的配置到 config.toml

    注意：这里会把 app/azure/tencent/soulvoice/ui/tts_qwen/whisper/proxy/frames
    都写回去，保证你在 UI 里改过的设置能持久保存。
    """
    _cfg["app"] = app
    _cfg["azure"] = azure
    _cfg["tencent"] = tencent
    _cfg["soulvoice"] = soulvoice
    _cfg["ui"] = ui
    _cfg["tts_qwen"] = tts_qwen
    _cfg["whisper"] = whisper
    _cfg["proxy"] = proxy
    _cfg["frames"] = frames

    with open(config_file, "w", encoding="utf-8") as f:
        f.write(toml.dumps(_cfg))


# ----------------- 环境变量设置 -----------------
imagemagick_path = app.get("imagemagick_path", "")
if imagemagick_path and os.path.isfile(imagemagick_path):
    os.environ["IMAGEMAGICK_BINARY"] = imagemagick_path

ffmpeg_path = app.get("ffmpeg_path", "")
if ffmpeg_path and os.path.isfile(ffmpeg_path):
    os.environ["IMAGEIO_FFMPEG_EXE"] = ffmpeg_path

logger.info(f"{project_name} v{project_version}")
