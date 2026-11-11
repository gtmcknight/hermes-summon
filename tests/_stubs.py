"""Just enough of Hermes's gateway API to test the plugin without Hermes installed.

When Hermes is importable (inside a Hermes checkout or venv), the real modules are used instead.
"""

from __future__ import annotations

import enum
import importlib.util
import os
import re
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


def install() -> None:
    try:
        import gateway.platforms.base  # noqa: F401  (real Hermes present)
        return
    except ImportError:
        pass

    class Platform:
        def __init__(self, value: str):
            self.value = value

        def __eq__(self, other):
            return isinstance(other, Platform) and other.value == self.value

        def __hash__(self):
            return hash(self.value)

    @dataclass
    class PlatformConfig:
        extra: dict = field(default_factory=dict)

    def get_scoped_secret(name: str, default: str = "") -> str:
        return os.environ.get(name, default)

    def extra_or_secret(extra, key, env):
        return os.environ.get(env) or (extra or {}).get(key) or ""

    class MessageType(enum.Enum):
        TEXT = "text"
        PHOTO = "photo"

    @dataclass
    class MessageEvent:
        text: str
        message_type: MessageType = MessageType.TEXT
        source: Any = None
        message_id: Optional[str] = None
        media_urls: list = field(default_factory=list)
        media_types: list = field(default_factory=list)
        reply_to_is_own_message: bool = False
        allow_gateway_control: bool = True
        timestamp: Any = None

    @dataclass
    class SendResult:
        success: bool
        message_id: Optional[str] = None
        error: Optional[str] = None
        raw_response: Any = None
        retryable: bool = False
        continuation_message_ids: tuple = ()

    cached: list[str] = []

    async def cache_image_from_url(url: str, ext: str = ".jpg", retries: int = 2) -> str:
        cached.append(url)
        fd, path = tempfile.mkstemp(suffix=ext)
        os.write(fd, b"\xff\xd8fake")
        os.close(fd)
        return path

    _IMG = re.compile(r"!\[([^\]]*)\]\((\S+?)\)")

    class BasePlatformAdapter:
        def __init__(self, config=None, platform=None, **kwargs):
            self.config = config
            self.platform = platform
            self.events: list = []
            self.connected = False
            self.fatal: Optional[tuple] = None
            self._message_handler = True

        async def handle_message(self, event) -> None:
            self.events.append(event)

        def build_source(self, chat_id, chat_name=None, chat_type="dm", user_id=None, user_name=None, **kw):
            return types.SimpleNamespace(platform=self.platform, chat_id=str(chat_id), chat_name=chat_name,
                                         chat_type=chat_type, user_id=user_id, user_name=user_name, **kw)

        def _mark_connected(self, **kw):
            self.connected = True

        def _mark_disconnected(self):
            self.connected = False

        def _set_fatal_error(self, code, message, *, retryable):
            self.fatal = (code, message, retryable)

        @staticmethod
        def extract_images(content):
            images = [(m.group(2), m.group(1)) for m in _IMG.finditer(content)]
            return images, _IMG.sub("", content).strip()

        @staticmethod
        def validate_media_delivery_path(path, session_key=""):
            return path if Path(path).is_file() else None

    @dataclass
    class PlatformEntry:
        name: str = ""
        label: str = ""
        adapter_factory: Any = None
        check_fn: Any = None
        validate_config: Any = None
        required_env: list = field(default_factory=list)
        install_hint: str = ""
        env_enablement_fn: Any = None
        allowed_users_env: str = ""
        max_message_length: int = 0
        emoji: str = ""
        pii_safe: bool = False
        allow_update_command: bool = True
        platform_hint: str = ""
        display_tier: str = ""
        # display_overrides deliberately missing: register() must drop fields an older core lacks

    data_root = Path(tempfile.mkdtemp(prefix="summon-test-"))

    def plugin_data_dir(name: str) -> Path:
        p = data_root / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    _module("gateway")
    _module("gateway.config", Platform=Platform, PlatformConfig=PlatformConfig)
    _module("gateway.platforms")
    _module("gateway.platforms._shared", get_scoped_secret=get_scoped_secret, extra_or_secret=extra_or_secret)
    _module("gateway.platforms.base", BasePlatformAdapter=BasePlatformAdapter, SendResult=SendResult,
            cache_image_from_url=cache_image_from_url, cached=cached)
    _module("gateway.platforms.event", MessageEvent=MessageEvent, MessageType=MessageType)
    _module("gateway.platforms.helpers", platform_http_client=lambda **kw: None)
    _module("gateway.platform_registry", PlatformEntry=PlatformEntry)
    _module("plugins")
    _module("plugins.plugin_storage", plugin_data_dir=plugin_data_dir)


def load_plugin(name: str = "summon") -> types.ModuleType:
    """Import the repo as a package, the way Hermes loads a plugin directory."""
    install()
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
