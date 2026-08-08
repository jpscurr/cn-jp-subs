"""字幕生成工具包：为中文、日文视频生成可用于句子挖掘的字幕。"""

import sys

__version__ = "2.0.0"

# Windows 控制台默认使用 cp1252 编码，遇到第一个中文标题或对钩符号就会崩溃。
# 在这里统一改成 UTF-8，这样每个入口（命令行、网页服务）都不会再出问题。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
